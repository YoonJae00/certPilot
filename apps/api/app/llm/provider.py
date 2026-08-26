"""LLM 프로바이더 추상화.

기본 프로바이더는 **OpenAI** 다. 키가 없으면 Anthropic → 데모·테스트용 결정적 Fake
순으로 내려간다(`LLM_PROVIDER=auto`). `get_llm_provider()` 가 유일한 교체 지점이다.

Fake 는 "가짜 심사원"이지 "우회로"가 아니다. 스키마·근거 참조 규칙을 그대로 지키는
응답만 만들고, 근거 검증·unknown 강제·규칙 우선 같은 안전장치는 전부 호출 측
(`app/workers/assess.py`)에서 실제로 돌아간다. 구조화 출력(`json_schema`)도 마찬가지로
**후처리 검증을 대신하지 않는다** — 응답 형식을 거들 뿐이고 검증은 그대로 돈다.
"""

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Any, Protocol

from pydantic import BaseModel

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Anthropic 기본 판정 모델. PRD §8 은 temperature 0 을 요구하지만 claude-sonnet-5 는
# temperature/top_p 파라미터를 더 받지 않는다(400). 대신 사고 모드를 끄고 JSON
# 스키마를 강제해 재현성을 확보한다.
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 2000

# OpenAI 기본 판정 모델. 실제 기본값은 `Settings.openai_model` 이며 여기 값은
# 프로바이더를 직접 만들 때 쓰는 폴백이다.
OPENAI_DEFAULT_MODEL = "gpt-5.6"
# 출력 토큰 상한. GPT-5 계열은 추론 토큰도 이 한도를 함께 먹으므로 판정 JSON 길이
# (2천 토큰 안팎)보다 넉넉히 잡는다.
OPENAI_DEFAULT_MAX_COMPLETION_TOKENS = 4000

# 모델별 100만 토큰당 단가(USD, 입력/출력). **2026-08 기준 공개 가격**이며 언제든
# 바뀔 수 있다. 요금이 바뀌면 이 표만 고치면 된다.
PRICING_USD_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5": (Decimal("5.00"), Decimal("25.00")),
    "claude-sonnet-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
}
# 표에 없는 모델을 쓰면 과소 집계보다 과대 집계가 안전하다(상한 차단이 먼저 걸린다).
FALLBACK_PRICE_USD_PER_MTOK = (Decimal("5.00"), Decimal("25.00"))

# OpenAI 단가표. 위와 같은 규칙 — **2026-08 기준 공개 가격**이고 언제든 바뀐다.
OPENAI_PRICING_USD_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "gpt-5.6": (Decimal("2.00"), Decimal("12.00")),
    "gpt-5.6-terra": (Decimal("2.00"), Decimal("12.00")),
    "gpt-5.6-luna": (Decimal("0.20"), Decimal("1.20")),
    "gpt-5.5": (Decimal("5.00"), Decimal("30.00")),
}
# OpenAI 쪽 미지 모델 폴백. 표에서 가장 비싼 값을 쓴다(과대 집계가 안전하다).
OPENAI_FALLBACK_PRICE_USD_PER_MTOK = (Decimal("5.00"), Decimal("30.00"))

_MTOK = Decimal("1000000")


class LLMProviderError(RuntimeError):
    """프로바이더 설정이 잘못됐거나 응답을 쓸 수 없을 때."""


@dataclass(frozen=True)
class LLMResult:
    """LLM 호출 1회의 결과와 비용."""

    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


@dataclass(frozen=True)
class JsonSchemaSpec:
    """구조화 출력에 쓸 JSON 스키마 명세.

    지원하는 프로바이더(OpenAI)만 쓰고 나머지는 무시한다. **이걸 준다고 응답 검증을
    건너뛰지 않는다** — 호출 측의 스키마·근거 검증은 그대로 돈다.
    """

    name: str
    schema: dict[str, Any]

    @classmethod
    def from_model(cls, model: type[BaseModel], *, name: str | None = None) -> "JsonSchemaSpec":
        """pydantic 모델에서 strict 구조화 출력용 스키마를 만든다."""
        return cls(name=name or model.__name__, schema=to_strict_schema(model.model_json_schema()))


def to_strict_schema(node: Any) -> Any:
    """JSON 스키마를 OpenAI strict 구조화 출력 규격에 맞게 다듬는다.

    strict 모드는 모든 객체가 `additionalProperties: false` 이고 모든 속성이 `required`
    에 들어 있기를 요구한다. pydantic 은 기본값이 있는 필드를 `required` 에서 빼고
    `default` 키를 넣으므로 여기서 되돌린다(선택 필드는 `str | None` 처럼 null 을 허용하는
    `anyOf` 로 이미 표현돼 있어 의미가 달라지지 않는다).
    """
    if isinstance(node, list):
        return [to_strict_schema(item) for item in node]
    if not isinstance(node, dict):
        return node

    # `default` 는 strict 모드가 받지 않는다.
    result: dict[str, Any] = {
        key: to_strict_schema(value) for key, value in node.items() if key != "default"
    }
    properties = result.get("properties")
    if isinstance(properties, dict):
        result["additionalProperties"] = False
        result["required"] = list(properties)
    return result


def _lookup_price(
    model: str,
    pricing: Mapping[str, tuple[Decimal, Decimal]],
    fallback: tuple[Decimal, Decimal],
) -> tuple[Decimal, Decimal]:
    """모델 단가를 찾는다. 정확히 없으면 가장 긴 접두사 일치(날짜 스냅샷 대응)."""
    exact = pricing.get(model)
    if exact is not None:
        return exact
    candidates = [key for key in pricing if model.startswith(key)]
    if not candidates:
        return fallback
    return pricing[max(candidates, key=len)]


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    pricing: Mapping[str, tuple[Decimal, Decimal]] | None = None,
    fallback: tuple[Decimal, Decimal] | None = None,
) -> Decimal:
    """토큰 수를 USD 비용으로 환산한다. 단가표를 안 주면 Anthropic 표를 쓴다."""
    input_price, output_price = _lookup_price(
        model,
        PRICING_USD_PER_MTOK if pricing is None else pricing,
        FALLBACK_PRICE_USD_PER_MTOK if fallback is None else fallback,
    )
    cost = (Decimal(input_tokens) * input_price + Decimal(output_tokens) * output_price) / _MTOK
    # 소수 6자리까지만 들고 다닌다(DB 는 4자리로 반올림해 저장한다).
    return cost.quantize(Decimal("0.000001"))


def estimate_openai_cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """OpenAI 단가표로 비용을 환산한다."""
    return estimate_cost_usd(
        model,
        input_tokens,
        output_tokens,
        pricing=OPENAI_PRICING_USD_PER_MTOK,
        fallback=OPENAI_FALLBACK_PRICE_USD_PER_MTOK,
    )


class LLMProvider(Protocol):
    """LLM 프로바이더 인터페이스."""

    @property
    def model(self) -> str:
        """모델 식별자. 모의심사 실행 기록에 남는다."""
        ...

    def complete(
        self, system: str, user: str, *, json_schema: JsonSchemaSpec | None = None
    ) -> LLMResult:
        """시스템·사용자 프롬프트를 넣고 텍스트 응답을 받는다.

        `json_schema` 는 구조화 출력을 지원하는 프로바이더만 쓰는 **선택** 힌트다.
        무시해도 계약은 지켜진다(응답은 어차피 호출 측에서 다시 검증한다).
        """
        ...


class OpenAIProvider:
    """OpenAI Chat Completions API 프로바이더(기본 프로바이더).

    `json_schema` 를 주면 strict 구조화 출력으로 판정 JSON 형식을 강제한다. 형식만
    거들 뿐이고 근거 실존 검증·unknown 강제는 호출 측에서 그대로 돈다.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = OPENAI_DEFAULT_MODEL,
        max_completion_tokens: int = OPENAI_DEFAULT_MAX_COMPLETION_TOKENS,
    ) -> None:
        # 지연 임포트: 키가 없는 환경에서는 SDK 를 부를 일이 없다.
        import openai

        # SDK 호출을 kwargs 로 조립하므로 타입은 Any 로 둔다.
        self._client: Any = openai.OpenAI(api_key=api_key)
        self._model = model
        self._max_completion_tokens = max_completion_tokens

    @property
    def model(self) -> str:
        """모델 식별자."""
        return self._model

    def complete(
        self, system: str, user: str, *, json_schema: JsonSchemaSpec | None = None
    ) -> LLMResult:
        """판정 1건을 요청한다. 재시도는 호출 측에서 한다."""
        request: dict[str, Any] = {
            "model": self._model,
            # GPT-5 계열은 시스템 지시를 developer 역할로 받는다.
            "messages": [
                {"role": "developer", "content": system},
                {"role": "user", "content": user},
            ],
            # 추론 토큰까지 포함한 출력 상한.
            "max_completion_tokens": self._max_completion_tokens,
            # temperature 는 **보내지 않는다** — GPT-5 계열이 받지 않는 파라미터라 400 이 난다.
            # 재현성은 구조화 출력과 호출 측 후처리로 확보한다(PRD §8 원칙 5).
        }
        if json_schema is not None:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": json_schema.name,
                    "strict": True,
                    "schema": json_schema.schema,
                },
            }

        response = self._client.chat.completions.create(**request)

        choices = response.choices or []
        if not choices:
            raise LLMProviderError("OpenAI 응답에 choices 가 없다")
        message = choices[0].message
        refusal = getattr(message, "refusal", None)
        if refusal:
            # 거부는 조용히 빈 응답으로 넘기지 않는다. 호출 측 재시도 사유가 된다.
            raise LLMProviderError(f"모델이 응답을 거부했다: {refusal}")
        text = message.content or ""
        if not text and getattr(choices[0], "finish_reason", None) == "length":
            # 추론 토큰이 상한을 다 먹으면 본문이 비어서 온다. "JSON 을 못 찾았다"로
            # 넘기면 원인을 못 찾으므로 여기서 이유를 그대로 알린다.
            raise LLMProviderError(
                "출력 토큰 상한에 걸려 응답 본문이 비었다"
                f"(max_completion_tokens={self._max_completion_tokens})"
            )

        usage = getattr(response, "usage", None)
        if usage is None:
            # 토큰 수를 못 받으면 비용을 0 으로 잡을 수밖에 없다. 상한 계산이 과소평가되므로
            # 조용히 넘기지 않고 남긴다.
            logger.warning(
                "OpenAI 응답에 usage 가 없어 비용을 0 으로 집계한다: 모델=%s", self._model
            )
            input_tokens = 0
            output_tokens = 0
        else:
            input_tokens = int(usage.prompt_tokens)
            output_tokens = int(usage.completion_tokens)

        return LLMResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=estimate_openai_cost_usd(self._model, input_tokens, output_tokens),
        )


class AnthropicProvider:
    """Anthropic Messages API 프로바이더."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        # 지연 임포트: 키가 없는 환경에서는 SDK 를 부를 일이 없다.
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    @property
    def model(self) -> str:
        """모델 식별자."""
        return self._model

    def complete(
        self, system: str, user: str, *, json_schema: JsonSchemaSpec | None = None
    ) -> LLMResult:
        """판정 1건을 요청한다. 재시도는 호출 측에서 한다.

        `json_schema` 는 쓰지 않는다 — 이 경로는 프롬프트의 출력 형식 지시와 호출 측
        스키마 검증으로 형식을 맞춘다.
        """
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            # 판정은 재현 가능해야 한다(PRD §8 원칙 5). 사고 모드를 끄면 출력이
            # 짧아지고 흔들림이 줄어든다. temperature 는 이 모델에서 더는 못 쓴다.
            thinking={"type": "disabled"},
        )
        # 응답 블록은 text 말고도 여러 종류가 올 수 있다. 판정 JSON 은 text 에만 있다.
        text = "".join(block.text for block in response.content if block.type == "text")
        input_tokens = int(response.usage.input_tokens)
        output_tokens = int(response.usage.output_tokens)
        return LLMResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=estimate_cost_usd(self._model, input_tokens, output_tokens),
        )


# ---------------------------------------------------------------------------
# 결정적 Fake
# ---------------------------------------------------------------------------

# 프롬프트에서 근거 참조를 뽑는 정규식. 실제로 제시된 id 만 인용하기 위해 쓴다.
_CHUNK_REF_RE = re.compile(r"\[chunk:(c_[0-9A-Za-z_-]+)\s*\|")
_EVIDENCE_REF_RE = re.compile(r"\[evidence:(e_[0-9A-Za-z_-]+)\s*\|")
_HEADING_RE = re.compile(r"^##\s*항목\s+(\S+)\s+(.*)$", re.MULTILINE)
_RULE_SECTION_RE = re.compile(r"##\s*규칙 판정 결과\s*\n(.*?)(?=\n##\s|\Z)", re.DOTALL)

# 근거가 있을 때 항목 코드 해시로 고르는 판정. 10칸 중 met 5 / partial 3 / unmet 2 로
# 데모 분포가 한쪽으로 쏠리지 않게 섞는다.
_STATUS_WHEEL = (
    "met",
    "met",
    "met",
    "met",
    "met",
    "partial",
    "partial",
    "partial",
    "unmet",
    "unmet",
)

# 인용할 근거 최대 개수.
_MAX_CITED_CHUNKS = 3
_MAX_CITED_EVIDENCE = 2

# 토큰 수 근사(문자 수 ÷ 4). 실제 토크나이저를 부르지 않는다.
_CHARS_PER_TOKEN = 4


class FakeProvider:
    """API 키 없이 도는 **결정적** 모의 심사원.

    같은 프롬프트에는 언제나 같은 JSON 을 돌려준다(재실행 판정 일치 테스트의 근거).
    판정 규칙:

    1. 규칙 판정에 fail 이 있으면 `unmet`.
    2. 근거가 하나도 없으면 `unknown` + 빈 참조.
    3. 그 외에는 항목 코드 해시로 met/partial/unmet 중 하나를 고른다.

    인용하는 참조 id 는 **프롬프트에 실제로 제시된 것만** 쓴다. 없는 id 를 지어내지
    않는다(그런 응답을 만드는 건 환각 검증 테스트용 스텁의 몫이다).
    """

    model_name = "fake-deterministic-auditor"

    @property
    def model(self) -> str:
        """모델 식별자."""
        return self.model_name

    @staticmethod
    def _bucket(code: str) -> int:
        """항목 코드에서 결정적 버킷(0~9)을 만든다. 파이썬 `hash()` 는 시드가 달라 못 쓴다."""
        digest = hashlib.blake2b(code.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % len(_STATUS_WHEEL)

    def complete(
        self, system: str, user: str, *, json_schema: JsonSchemaSpec | None = None
    ) -> LLMResult:
        """프롬프트를 읽고 판정 JSON 을 만든다.

        `json_schema` 는 쓰지 않는다 — 여기서 만드는 JSON 은 이미 판정 스키마를 따른다.
        """
        heading = _HEADING_RE.search(user)
        code = heading.group(1) if heading else "0.0.0"
        title = heading.group(2).strip() if heading else "항목"

        chunk_refs = _CHUNK_REF_RE.findall(user)[:_MAX_CITED_CHUNKS]
        evidence_refs = _EVIDENCE_REF_RE.findall(user)[:_MAX_CITED_EVIDENCE]

        rule_block = _RULE_SECTION_RE.search(user)
        rule_text = rule_block.group(1) if rule_block else ""
        rule_failed = "fail" in rule_text and "종합: fail" in rule_text

        payload = self._decide(
            code=code,
            title=title,
            chunk_refs=chunk_refs,
            evidence_refs=evidence_refs,
            rule_failed=rule_failed,
        )
        text = json.dumps(payload, ensure_ascii=False, indent=2)

        input_tokens = max(1, (len(system) + len(user)) // _CHARS_PER_TOKEN)
        output_tokens = max(1, len(text) // _CHARS_PER_TOKEN)
        return LLMResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            # 로컬 Fake 는 돈이 들지 않는다.
            cost_usd=Decimal("0"),
        )

    def _decide(
        self,
        *,
        code: str,
        title: str,
        chunk_refs: list[str],
        evidence_refs: list[str],
        rule_failed: bool,
    ) -> dict[str, object]:
        """판정 JSON 본문을 만든다."""
        citation = self._citation_text(chunk_refs, evidence_refs)

        if not chunk_refs and not evidence_refs:
            return {
                "criterion_code": code,
                "status": "unknown",
                "confidence": 0.2,
                "rationale": (
                    f"제출된 문서와 증적에서 '{title}' 관련 내용을 찾지 못해 "
                    f"{code} 항목의 이행 여부를 판단할 수 없다."
                ),
                "evidence_chunk_ids": [],
                "evidence_ids": [],
                "predicted_defect": None,
                "recommendation": (
                    f"'{title}' 관련 정책·절차 문서와 이행 증적을 제출해 재심사를 받는다."
                ),
                "missing_info": [f"{title} 관련 정책·절차 문서", f"{title} 이행 기록"],
            }

        bucket = self._bucket(code)
        status = "unmet" if rule_failed else _STATUS_WHEEL[bucket]
        confidence = round(0.62 + bucket * 0.03, 2)

        if status == "met":
            rationale = (
                f"'{title}' 요구사항을 다루는 근거가 확인된다{citation}. "
                f"문서에 정의된 내용이 {code} 인증기준의 확인사항과 어긋나지 않는다."
            )
            predicted_defect = None
            recommendation = f"현재 수준을 유지하고 '{title}' 이행 기록을 주기적으로 갱신한다."
        elif status == "partial":
            rationale = (
                f"'{title}' 관련 문서는 확인되나{citation} 이행 주기·범위를 확인할 수 있는 "
                f"기록이 충분하지 않아 {code} 항목을 부분충족으로 본다."
            )
            predicted_defect = f"'{title}' 절차는 수립돼 있으나 이행 증적이 일부 누락돼 있음"
            recommendation = f"'{title}' 이행 결과를 기록으로 남기고 최신 증적을 확보한다."
        else:
            reason = (
                "클라우드 증적 점검이 fail 로 나와"
                if rule_failed
                else "제출된 근거가 인증기준 요구 수준에 미치지 못해"
            )
            rationale = (
                f"{reason} {code} 항목을 미충족으로 본다{citation}. "
                f"'{title}' 요구사항을 만족한다고 볼 근거가 없다."
            )
            predicted_defect = f"'{title}' 인증기준을 충족하는 정책 또는 이행 증적이 확인되지 않음"
            recommendation = f"'{title}' 요구사항을 반영한 절차를 보완하고 이행 증적을 재수집한다."

        return {
            "criterion_code": code,
            "status": status,
            "confidence": confidence,
            "rationale": rationale,
            "evidence_chunk_ids": list(chunk_refs),
            "evidence_ids": list(evidence_refs),
            "predicted_defect": predicted_defect,
            "recommendation": recommendation,
            "missing_info": [],
        }

    @staticmethod
    def _citation_text(chunk_refs: list[str], evidence_refs: list[str]) -> str:
        """rationale 안에 넣을 인용 문구를 만든다."""
        parts = [f"chunk:{ref}" for ref in chunk_refs]
        parts.extend(f"evidence:{ref}" for ref in evidence_refs)
        if not parts:
            return ""
        return "(" + ", ".join(parts) + ")"


@lru_cache
def get_llm_provider() -> LLMProvider:
    """LLM 프로바이더 팩토리. 프로바이더를 갈아끼우는 유일한 지점이다.

    `LLM_PROVIDER` 설정을 따른다:

    - `auto`(기본): OpenAI 키 → Anthropic 키 → Fake 순으로 내려간다.
    - `openai` / `anthropic` / `fake`: 그대로 쓴다. 키가 필요한데 없으면 예외로 알린다
      (조용히 Fake 로 떨어지면 실 판정인 줄 알고 결과를 믿게 된다).
    """
    settings = get_settings()
    requested = (settings.llm_provider or "auto").strip().lower()

    choice = requested
    if choice == "auto":
        if settings.openai_api_key:
            choice = "openai"
        elif settings.anthropic_api_key:
            choice = "anthropic"
        else:
            choice = "fake"

    if choice == "openai":
        if not settings.openai_api_key:
            raise LLMProviderError("LLM_PROVIDER=openai 인데 OPENAI_API_KEY 가 비어 있다")
        logger.info(
            "LLM 프로바이더: OpenAI(%s) [LLM_PROVIDER=%s]", settings.openai_model, requested
        )
        return OpenAIProvider(settings.openai_api_key, model=settings.openai_model)

    if choice == "anthropic":
        if not settings.anthropic_api_key:
            raise LLMProviderError("LLM_PROVIDER=anthropic 인데 ANTHROPIC_API_KEY 가 비어 있다")
        logger.info("LLM 프로바이더: Anthropic(%s) [LLM_PROVIDER=%s]", DEFAULT_MODEL, requested)
        return AnthropicProvider(settings.anthropic_api_key)

    if choice == "fake":
        logger.info(
            "LLM 프로바이더: API 키가 없어 결정적 Fake 로 동작한다 [LLM_PROVIDER=%s]", requested
        )
        return FakeProvider()

    raise LLMProviderError(
        f"알 수 없는 LLM_PROVIDER: {settings.llm_provider!r} "
        "(auto | openai | anthropic | fake 중 하나여야 한다)"
    )

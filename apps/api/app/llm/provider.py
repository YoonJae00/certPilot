"""LLM 프로바이더 추상화.

키를 넣으면 실 LLM(Anthropic), 없으면 데모·테스트용 결정적 Fake 가 뜬다.
`get_llm_provider()` 가 유일한 교체 지점이다.

Fake 는 "가짜 심사원"이지 "우회로"가 아니다. 스키마·근거 참조 규칙을 그대로 지키는
응답만 만들고, 근거 검증·unknown 강제·규칙 우선 같은 안전장치는 전부 호출 측
(`app/workers/assess.py`)에서 실제로 돌아간다.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# 기본 판정 모델. PRD §8 은 temperature 0 을 요구하지만 claude-sonnet-5 는
# temperature/top_p 파라미터를 더 받지 않는다(400). 대신 사고 모드를 끄고 JSON
# 스키마를 강제해 재현성을 확보한다.
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 2000

# 모델별 100만 토큰당 단가(USD, 입력/출력). **2026-08 기준 공개 가격**이며 언제든
# 바뀔 수 있다. 요금이 바뀌면 이 표만 고치면 된다.
PRICING_USD_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5": (Decimal("5.00"), Decimal("25.00")),
    "claude-sonnet-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
}
# 표에 없는 모델을 쓰면 과소 집계보다 과대 집계가 안전하다(상한 차단이 먼저 걸린다).
FALLBACK_PRICE_USD_PER_MTOK = (Decimal("5.00"), Decimal("25.00"))

_MTOK = Decimal("1000000")


@dataclass(frozen=True)
class LLMResult:
    """LLM 호출 1회의 결과와 비용."""

    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


class LLMProvider(Protocol):
    """LLM 프로바이더 인터페이스."""

    @property
    def model(self) -> str:
        """모델 식별자. 모의심사 실행 기록에 남는다."""
        ...

    def complete(self, system: str, user: str) -> LLMResult:
        """시스템·사용자 프롬프트를 넣고 텍스트 응답을 받는다."""
        ...


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """토큰 수를 USD 비용으로 환산한다."""
    input_price, output_price = PRICING_USD_PER_MTOK.get(model, FALLBACK_PRICE_USD_PER_MTOK)
    cost = (Decimal(input_tokens) * input_price + Decimal(output_tokens) * output_price) / _MTOK
    # 소수 6자리까지만 들고 다닌다(DB 는 4자리로 반올림해 저장한다).
    return cost.quantize(Decimal("0.000001"))


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

    def complete(self, system: str, user: str) -> LLMResult:
        """판정 1건을 요청한다. 재시도는 호출 측에서 한다."""
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

    def complete(self, system: str, user: str) -> LLMResult:
        """프롬프트를 읽고 판정 JSON 을 만든다."""
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
    """LLM 프로바이더 팩토리.

    `ANTHROPIC_API_KEY` 가 있으면 실제 Anthropic 모델을, 없으면 데모·테스트용
    결정적 Fake 를 돌려준다. 프로바이더를 갈아끼우는 유일한 지점이다.
    """
    settings = get_settings()
    if settings.anthropic_api_key:
        logger.info("LLM 프로바이더: Anthropic(%s)", DEFAULT_MODEL)
        return AnthropicProvider(settings.anthropic_api_key)
    logger.info("LLM 프로바이더: ANTHROPIC_API_KEY 가 없어 결정적 Fake 로 동작한다")
    return FakeProvider()

"""OpenAI LLM·임베딩 프로바이더 테스트.

**실제 API 를 절대 부르지 않는다.** `openai.OpenAI` 를 대역으로 갈아끼우므로
`OPENAI_API_KEY` 가 없는 환경에서도 전부 통과해야 한다.
"""

import json
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.llm.assess_prompt import LLMFinding
from app.llm.embeddings import (
    HASHING_MIN_SIMILARITY,
    OPENAI_MIN_SIMILARITY,
    EmbeddingProviderError,
    HashingEmbeddingProvider,
    OpenAIEmbeddingProvider,
    get_embedding_provider,
)
from app.llm.provider import (
    OPENAI_FALLBACK_PRICE_USD_PER_MTOK,
    AnthropicProvider,
    FakeProvider,
    JsonSchemaSpec,
    LLMProviderError,
    OpenAIProvider,
    estimate_openai_cost_usd,
    get_llm_provider,
)
from app.models import EMBEDDING_DIM
from app.workers.assess import FINDING_JSON_SCHEMA, min_chunk_similarity

FAKE_OPENAI_KEY = "sk-test-not-a-real-key"
FAKE_ANTHROPIC_KEY = "sk-ant-test-not-a-real-key"


# ---------------------------------------------------------------------------
# 대역(stub) 도구
# ---------------------------------------------------------------------------


def install_stub_openai(monkeypatch, *, completion=None, embedding=None) -> list[dict]:
    """`openai.OpenAI` 를 대역으로 바꾸고, 오간 요청 kwargs 목록을 돌려준다."""
    calls: list[dict] = []

    def _respond(handler, kwargs, what):
        calls.append(kwargs)
        if handler is None:
            raise AssertionError(f"이 테스트는 {what} 호출을 예상하지 않는다: {kwargs}")
        return handler(kwargs) if callable(handler) else handler

    class _Completions:
        def create(self, **kwargs):
            return _respond(completion, kwargs, "chat.completions")

    class _Embeddings:
        def create(self, **kwargs):
            return _respond(embedding, kwargs, "embeddings")

    class _Client:
        def __init__(self, api_key=None, **kwargs):
            self.api_key = api_key
            self.chat = SimpleNamespace(completions=_Completions())
            self.embeddings = _Embeddings()

    import openai

    monkeypatch.setattr(openai, "OpenAI", _Client)
    return calls


def make_completion(text, *, prompt_tokens=1000, completion_tokens=500, refusal=None):
    """Chat Completions 응답 대역."""
    message = SimpleNamespace(content=text, refusal=refusal)
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def make_settings(**overrides) -> Settings:
    """설정 대역. `.env` 값이 새지 않게 관련 필드를 전부 명시한다."""
    values = {
        "llm_provider": "auto",
        "openai_api_key": None,
        "anthropic_api_key": None,
        "openai_model": "gpt-5.6",
        "embedding_provider": "auto",
        "openai_embedding_model": "text-embedding-3-small",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(autouse=True)
def _clear_provider_caches():
    """프로바이더 팩토리는 lru_cache 라 테스트 사이에 반드시 비운다."""
    get_llm_provider.cache_clear()
    get_embedding_provider.cache_clear()
    yield
    get_llm_provider.cache_clear()
    get_embedding_provider.cache_clear()


# ---------------------------------------------------------------------------
# OpenAIProvider — 요청 조립과 응답 파싱
# ---------------------------------------------------------------------------


def test_complete_sends_developer_role_and_json_schema(monkeypatch):
    """developer/user 메시지와 strict json_schema 를 그대로 보낸다."""
    payload = {"criterion_code": "1.1.1", "status": "met"}
    calls = install_stub_openai(
        monkeypatch, completion=make_completion(json.dumps(payload, ensure_ascii=False))
    )
    provider = OpenAIProvider(FAKE_OPENAI_KEY, model="gpt-5.6")

    result = provider.complete("시스템 지시", "항목 본문", json_schema=FINDING_JSON_SCHEMA)

    assert len(calls) == 1
    request = calls[0]
    assert request["model"] == "gpt-5.6"
    assert [message["role"] for message in request["messages"]] == ["developer", "user"]
    assert request["messages"][0]["content"] == "시스템 지시"
    assert request["messages"][1]["content"] == "항목 본문"

    response_format = request["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == FINDING_JSON_SCHEMA.name
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"] == FINDING_JSON_SCHEMA.schema

    assert json.loads(result.text) == payload
    assert result.input_tokens == 1000
    assert result.output_tokens == 500


def test_complete_never_sends_temperature(monkeypatch):
    """GPT-5 계열은 temperature 를 받지 않는다 — 보내면 400 이다."""
    calls = install_stub_openai(monkeypatch, completion=make_completion("{}"))
    OpenAIProvider(FAKE_OPENAI_KEY).complete("s", "u", json_schema=FINDING_JSON_SCHEMA)

    assert "temperature" not in calls[0]
    assert "top_p" not in calls[0]
    assert calls[0]["max_completion_tokens"] > 0


def test_complete_without_schema_omits_response_format(monkeypatch):
    """스키마를 안 주면 response_format 자체를 보내지 않는다."""
    calls = install_stub_openai(monkeypatch, completion=make_completion("본문"))
    result = OpenAIProvider(FAKE_OPENAI_KEY).complete("s", "u")

    assert "response_format" not in calls[0]
    assert result.text == "본문"


def test_complete_raises_on_refusal(monkeypatch):
    """거부 응답을 빈 텍스트로 삼키지 않는다."""
    install_stub_openai(
        monkeypatch, completion=make_completion(None, refusal="정책상 답변할 수 없다")
    )
    with pytest.raises(LLMProviderError, match="거부"):
        OpenAIProvider(FAKE_OPENAI_KEY).complete("s", "u")


def test_complete_raises_when_truncated_by_token_limit(monkeypatch):
    """추론 토큰이 상한을 다 먹어 본문이 비면 이유를 그대로 알린다."""
    truncated = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="", refusal=None), finish_reason="length"
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4000),
    )
    install_stub_openai(monkeypatch, completion=truncated)

    with pytest.raises(LLMProviderError, match="상한"):
        OpenAIProvider(FAKE_OPENAI_KEY).complete("s", "u")


def test_complete_raises_on_empty_choices(monkeypatch):
    """choices 가 비면 명확히 실패한다."""
    empty = SimpleNamespace(choices=[], usage=None)
    install_stub_openai(monkeypatch, completion=empty)
    with pytest.raises(LLMProviderError, match="choices"):
        OpenAIProvider(FAKE_OPENAI_KEY).complete("s", "u")


def test_complete_without_usage_costs_zero(monkeypatch):
    """usage 가 없으면 토큰·비용을 0 으로 집계한다(호출은 성공)."""
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}", refusal=None))],
        usage=None,
    )
    install_stub_openai(monkeypatch, completion=response)
    result = OpenAIProvider(FAKE_OPENAI_KEY).complete("s", "u")

    assert (result.input_tokens, result.output_tokens) == (0, 0)
    assert result.cost_usd == 0


# ---------------------------------------------------------------------------
# 비용 계산
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-5.6", "2.000000"),
        ("gpt-5.6-terra", "2.000000"),
        ("gpt-5.6-luna", "0.200000"),
        ("gpt-5.5", "5.000000"),
    ],
)
def test_openai_input_price_by_model(model, expected):
    """입력 100만 토큰 단가가 모델별 표와 맞는다."""
    assert str(estimate_openai_cost_usd(model, 1_000_000, 0)) == expected


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-5.6", "12.000000"),
        ("gpt-5.6-luna", "1.200000"),
        ("gpt-5.5", "30.000000"),
    ],
)
def test_openai_output_price_by_model(model, expected):
    """출력 100만 토큰 단가가 모델별 표와 맞는다."""
    assert str(estimate_openai_cost_usd(model, 0, 1_000_000)) == expected


def test_openai_unknown_model_uses_conservative_fallback():
    """표에 없는 모델은 보수적 폴백 단가로 과대 집계한다."""
    input_price, output_price = OPENAI_FALLBACK_PRICE_USD_PER_MTOK
    assert estimate_openai_cost_usd("gpt-9-unknown", 1_000_000, 0) == input_price
    assert estimate_openai_cost_usd("gpt-9-unknown", 0, 1_000_000) == output_price


def test_openai_dated_snapshot_matches_longest_prefix():
    """날짜 스냅샷 모델명은 가장 긴 접두사 단가를 쓴다."""
    snapshot = estimate_openai_cost_usd("gpt-5.6-luna-2026-08-01", 1_000_000, 0)
    assert snapshot == estimate_openai_cost_usd("gpt-5.6-luna", 1_000_000, 0)


def test_provider_cost_uses_model_pricing(monkeypatch):
    """complete() 가 돌려주는 비용이 모델 단가와 맞는다."""
    install_stub_openai(
        monkeypatch,
        completion=make_completion("{}", prompt_tokens=1_000_000, completion_tokens=1_000_000),
    )
    result = OpenAIProvider(FAKE_OPENAI_KEY, model="gpt-5.6-luna").complete("s", "u")

    assert result.cost_usd == estimate_openai_cost_usd("gpt-5.6-luna", 1_000_000, 1_000_000)


# ---------------------------------------------------------------------------
# JSON 스키마
# ---------------------------------------------------------------------------


def test_finding_schema_is_strict_compatible():
    """strict 모드 요구(모든 속성 required + additionalProperties=false)를 만족한다."""
    schema = FINDING_JSON_SCHEMA.schema

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert set(schema["properties"]) == set(LLMFinding.model_fields)


def test_finding_schema_drops_defaults():
    """strict 모드가 받지 않는 `default` 키는 남기지 않는다."""

    def _walk(node) -> None:
        if isinstance(node, dict):
            assert "default" not in node
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(FINDING_JSON_SCHEMA.schema)


def test_json_schema_spec_name_defaults_to_model_name():
    """이름을 안 주면 모델 클래스명을 쓴다."""
    assert JsonSchemaSpec.from_model(LLMFinding).name == "LLMFinding"
    assert FINDING_JSON_SCHEMA.name == "llm_finding"


# ---------------------------------------------------------------------------
# get_llm_provider 선택 로직
# ---------------------------------------------------------------------------


def _use_settings(monkeypatch, module: str, **overrides) -> None:
    """해당 모듈이 보는 설정을 대역으로 바꾼다."""
    settings = make_settings(**overrides)
    monkeypatch.setattr(f"{module}.get_settings", lambda: settings)


def test_auto_prefers_openai_when_key_present(monkeypatch):
    """auto + OpenAI 키 → OpenAI."""
    install_stub_openai(monkeypatch, completion=make_completion("{}"))
    _use_settings(
        monkeypatch,
        "app.llm.provider",
        openai_api_key=FAKE_OPENAI_KEY,
        anthropic_api_key=FAKE_ANTHROPIC_KEY,
        openai_model="gpt-5.6-luna",
    )

    provider = get_llm_provider()

    assert isinstance(provider, OpenAIProvider)
    assert provider.model == "gpt-5.6-luna"


def test_auto_falls_back_to_anthropic(monkeypatch):
    """auto + Anthropic 키만 → Anthropic."""
    _use_settings(monkeypatch, "app.llm.provider", anthropic_api_key=FAKE_ANTHROPIC_KEY)

    assert isinstance(get_llm_provider(), AnthropicProvider)


def test_auto_falls_back_to_fake(monkeypatch):
    """auto + 키 없음 → 결정적 Fake."""
    _use_settings(monkeypatch, "app.llm.provider")

    assert isinstance(get_llm_provider(), FakeProvider)


def test_explicit_openai_without_key_raises(monkeypatch):
    """명시적으로 openai 를 골랐는데 키가 없으면 조용히 Fake 로 떨어지지 않는다."""
    _use_settings(monkeypatch, "app.llm.provider", llm_provider="openai")

    with pytest.raises(LLMProviderError, match="OPENAI_API_KEY"):
        get_llm_provider()


def test_explicit_anthropic_without_key_raises(monkeypatch):
    """명시적으로 anthropic 을 골랐는데 키가 없으면 예외."""
    _use_settings(
        monkeypatch, "app.llm.provider", llm_provider="anthropic", openai_api_key=FAKE_OPENAI_KEY
    )

    with pytest.raises(LLMProviderError, match="ANTHROPIC_API_KEY"):
        get_llm_provider()


def test_explicit_fake_wins_over_keys(monkeypatch):
    """명시값 fake 는 키가 있어도 Fake 를 쓴다."""
    _use_settings(
        monkeypatch, "app.llm.provider", llm_provider="fake", openai_api_key=FAKE_OPENAI_KEY
    )

    assert isinstance(get_llm_provider(), FakeProvider)


def test_unknown_llm_provider_raises(monkeypatch):
    """알 수 없는 값은 예외로 알린다."""
    _use_settings(monkeypatch, "app.llm.provider", llm_provider="gemini")

    with pytest.raises(LLMProviderError, match="LLM_PROVIDER"):
        get_llm_provider()


# ---------------------------------------------------------------------------
# OpenAI 임베딩
# ---------------------------------------------------------------------------


def _embedding_response(kwargs, *, dimension: int = EMBEDDING_DIM):
    """입력 개수만큼 벡터를 만들어 돌려준다. 순서는 일부러 뒤집는다."""
    texts = kwargs["input"]
    data = [
        SimpleNamespace(index=index, embedding=[float(index)] * dimension)
        for index in range(len(texts))
    ]
    return SimpleNamespace(data=list(reversed(data)))


def test_embed_returns_1536_dimension_vectors(monkeypatch):
    """기본 차원은 1536(Vector(1536) 과 일치)."""
    install_stub_openai(monkeypatch, embedding=_embedding_response)
    provider = OpenAIEmbeddingProvider(FAKE_OPENAI_KEY, model="text-embedding-3-small")

    vectors = provider.embed(["가", "나"])

    assert provider.dimension == EMBEDDING_DIM == 1536
    assert [len(vector) for vector in vectors] == [EMBEDDING_DIM, EMBEDDING_DIM]


def test_embed_preserves_input_order(monkeypatch):
    """응답이 뒤섞여 와도 index 로 다시 맞춘다."""
    install_stub_openai(monkeypatch, embedding=_embedding_response)
    provider = OpenAIEmbeddingProvider(FAKE_OPENAI_KEY, model="text-embedding-3-small")

    vectors = provider.embed(["가", "나", "다"])

    assert [vector[0] for vector in vectors] == [0.0, 1.0, 2.0]


def test_embed_splits_into_batches_of_100(monkeypatch):
    """한 번에 최대 100개씩 나눠 보낸다."""
    calls = install_stub_openai(monkeypatch, embedding=_embedding_response)
    provider = OpenAIEmbeddingProvider(FAKE_OPENAI_KEY, model="text-embedding-3-small")

    vectors = provider.embed([f"문장 {index}" for index in range(250)])

    assert [len(call["input"]) for call in calls] == [100, 100, 50]
    assert len(vectors) == 250


def test_embed_empty_input_skips_api(monkeypatch):
    """빈 입력이면 API 를 부르지 않는다."""
    calls = install_stub_openai(monkeypatch)
    provider = OpenAIEmbeddingProvider(FAKE_OPENAI_KEY, model="text-embedding-3-small")

    assert provider.embed([]) == []
    assert calls == []


def test_embed_rejects_wrong_dimension(monkeypatch):
    """차원이 1536 이 아니면 DB 에 넣기 전에 실패한다."""
    install_stub_openai(
        monkeypatch, embedding=lambda kwargs: _embedding_response(kwargs, dimension=768)
    )
    provider = OpenAIEmbeddingProvider(FAKE_OPENAI_KEY, model="text-embedding-3-large")

    with pytest.raises(EmbeddingProviderError, match="차원"):
        provider.embed(["가"])


def test_embed_rejects_count_mismatch(monkeypatch):
    """응답 개수가 입력과 다르면 실패한다."""
    install_stub_openai(monkeypatch, embedding=SimpleNamespace(data=[]))
    provider = OpenAIEmbeddingProvider(FAKE_OPENAI_KEY, model="text-embedding-3-small")

    with pytest.raises(EmbeddingProviderError, match="개수"):
        provider.embed(["가", "나"])


# ---------------------------------------------------------------------------
# get_embedding_provider 선택 로직 · 유사도 하한
# ---------------------------------------------------------------------------


def test_embedding_auto_prefers_openai(monkeypatch):
    """auto + OpenAI 키 → OpenAI 임베딩."""
    install_stub_openai(monkeypatch)
    _use_settings(monkeypatch, "app.llm.embeddings", openai_api_key=FAKE_OPENAI_KEY)

    provider = get_embedding_provider()

    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert provider.model == "text-embedding-3-small"


def test_embedding_auto_falls_back_to_hashing(monkeypatch):
    """auto + 키 없음 → 해싱 임베딩."""
    _use_settings(monkeypatch, "app.llm.embeddings")

    assert isinstance(get_embedding_provider(), HashingEmbeddingProvider)


def test_embedding_explicit_openai_without_key_raises(monkeypatch):
    """명시적으로 openai 를 골랐는데 키가 없으면 예외."""
    _use_settings(monkeypatch, "app.llm.embeddings", embedding_provider="openai")

    with pytest.raises(EmbeddingProviderError, match="OPENAI_API_KEY"):
        get_embedding_provider()


def test_embedding_explicit_hashing_wins_over_key(monkeypatch):
    """명시값 hashing 은 키가 있어도 해싱을 쓴다."""
    _use_settings(
        monkeypatch,
        "app.llm.embeddings",
        embedding_provider="hashing",
        openai_api_key=FAKE_OPENAI_KEY,
    )

    assert isinstance(get_embedding_provider(), HashingEmbeddingProvider)


def test_unknown_embedding_provider_raises(monkeypatch):
    """알 수 없는 값은 예외로 알린다."""
    _use_settings(monkeypatch, "app.llm.embeddings", embedding_provider="voyage")

    with pytest.raises(EmbeddingProviderError, match="EMBEDDING_PROVIDER"):
        get_embedding_provider()


def test_recommended_min_similarity_per_provider(monkeypatch):
    """유사도 하한은 프로바이더마다 다르다."""
    install_stub_openai(monkeypatch)

    assert HashingEmbeddingProvider().recommended_min_similarity == HASHING_MIN_SIMILARITY
    assert HASHING_MIN_SIMILARITY == 0.10
    openai_provider = OpenAIEmbeddingProvider(FAKE_OPENAI_KEY, model="text-embedding-3-small")
    assert openai_provider.recommended_min_similarity == OPENAI_MIN_SIMILARITY
    assert OPENAI_MIN_SIMILARITY == 0.30


@pytest.mark.parametrize(
    ("embedding_provider", "openai_api_key", "expected"),
    [
        ("hashing", None, HASHING_MIN_SIMILARITY),
        ("openai", FAKE_OPENAI_KEY, OPENAI_MIN_SIMILARITY),
    ],
)
def test_assess_uses_provider_min_similarity(
    monkeypatch, embedding_provider, openai_api_key, expected
):
    """모의심사의 검색 하한이 임베딩 프로바이더 권고값을 그대로 쓴다."""
    install_stub_openai(monkeypatch)
    _use_settings(
        monkeypatch,
        "app.llm.embeddings",
        embedding_provider=embedding_provider,
        openai_api_key=openai_api_key,
    )

    assert min_chunk_similarity() == expected

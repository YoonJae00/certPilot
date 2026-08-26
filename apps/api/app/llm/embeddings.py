"""임베딩 프로바이더.

`OPENAI_API_KEY` 가 있으면 OpenAI 임베딩(text-embedding-3-small, 1536차원), 없으면
외부 API 키 없이 도는 로컬 해싱 임베딩으로 내려간다. 해싱 임베딩은 **의미 품질이
낮다** — 어휘가 겹치는 문서만 잘 찾고 동의어·환언은 못 잡는다. 데모·테스트를 위한
자리표시자이며, `get_embedding_provider()` 가 **프로바이더 교체 지점**이다.

교체 시 주의: `chunks.embedding` 은 `Vector(1536)` 으로 고정돼 있으므로 차원이 다른
모델로 갈아타려면 마이그레이션이 필요하다. 차원이 같아도 **벡터 공간이 다르므로**
프로바이더를 바꾸면 기존 청크를 전부 재인제스트해야 한다(`make demo`).
"""

import hashlib
import logging
import math
import re
from functools import lru_cache
from typing import Any, Protocol

from app.core.config import get_settings
from app.models import EMBEDDING_DIM

logger = logging.getLogger(__name__)

# 어절 분리용. 한글·영숫자만 남기고 나머지는 경계로 본다.
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")

# 해싱 임베딩이 쓰는 n-gram 크기. 1-gram 은 어휘, 2-gram 은 짧은 구를 잡는다.
_NGRAM_SIZES = (1, 2)

# 근거로 인정할 최소 코사인 유사도 권고값. 임베딩마다 유사도 스케일이 달라
# 프로바이더가 자기 값을 들고 있고, 모의심사가 이 값을 검색 하한으로 쓴다.
#
# 해싱 0.10: 0.05/0.10/…/0.30 스윕(데모 시드 + 골든셋 20케이스) 결과 최적이었다.
# 판단불가 1→34개로 D2 복원, 골든셋 일치율 0.55→0.70(최고), unmet 정밀도·재현율 불변.
HASHING_MIN_SIMILARITY = 0.10
# OpenAI 0.30: **초기 추정값**이다. 임베딩 API 키를 확보한 뒤 `make eval` 스윕으로
# 반드시 재튜닝해야 한다(해싱과 유사도 분포가 달라 그대로 쓰면 하한이 틀어진다).
OPENAI_MIN_SIMILARITY = 0.30

# OpenAI 임베딩 1회 요청에 넣을 최대 텍스트 수. 요청 크기 상한에 걸리지 않게 나눈다.
OPENAI_EMBEDDING_BATCH_SIZE = 100


class EmbeddingProviderError(RuntimeError):
    """임베딩 프로바이더 설정이 잘못됐거나 응답을 쓸 수 없을 때."""


class EmbeddingProvider(Protocol):
    """임베딩 프로바이더 인터페이스."""

    @property
    def dimension(self) -> int:
        """벡터 차원."""
        ...

    @property
    def recommended_min_similarity(self) -> float:
        """근거로 인정할 최소 코사인 유사도 권고값."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """여러 텍스트를 한 번에 임베딩한다. 입력 순서와 출력 순서가 같다."""
        ...


def tokenize(text: str) -> list[str]:
    """소문자 어절 토큰. 토큰 수는 청킹의 토큰 근사에도 쓰인다."""
    return _TOKEN_RE.findall(text.lower())


class HashingEmbeddingProvider:
    """어절 n-gram 해싱 기반 결정적 임베딩.

    같은 입력에 대해 항상 같은 벡터를 만든다(파이썬 `hash()` 대신 blake2b 를 쓰는
    이유다 — 내장 해시는 프로세스마다 시드가 달라 결정적이지 않다).
    """

    def __init__(self, dimension: int = EMBEDDING_DIM) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        """벡터 차원."""
        return self._dimension

    @property
    def recommended_min_similarity(self) -> float:
        """해싱 임베딩 기준 유사도 하한(스윕 결과)."""
        return HASHING_MIN_SIMILARITY

    def _bucket_and_sign(self, feature: str) -> tuple[int, float]:
        """특성 문자열을 (차원 인덱스, 부호)로 사상한다."""
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        bucket = value % self._dimension
        sign = 1.0 if (value >> 63) & 1 else -1.0
        return bucket, sign

    def embed_one(self, text: str) -> list[float]:
        """텍스트 1개를 L2 정규화된 벡터로 만든다."""
        vector = [0.0] * self._dimension
        tokens = tokenize(text)
        for size in _NGRAM_SIZES:
            if len(tokens) < size:
                continue
            for start in range(len(tokens) - size + 1):
                feature = f"{size}:" + " ".join(tokens[start : start + size])
                bucket, sign = self._bucket_and_sign(feature)
                vector[bucket] += sign

        norm = math.sqrt(sum(component * component for component in vector))
        if norm == 0.0:
            # 빈 텍스트. 0 벡터는 코사인 거리가 정의되지 않으므로 0번 축을 세운다.
            vector[0] = 1.0
            return vector
        return [component / norm for component in vector]

    def embed(self, texts: list[str]) -> list[list[float]]:
        """여러 텍스트를 임베딩한다."""
        return [self.embed_one(text) for text in texts]


class OpenAIEmbeddingProvider:
    """OpenAI Embeddings API 프로바이더.

    `text-embedding-3-small` 기본 1536차원이라 `chunks.embedding`(Vector(1536))과
    그대로 맞는다. 차원이 다르게 오면 DB 에 넣기 전에 **여기서** 실패시킨다.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        dimension: int = EMBEDDING_DIM,
        batch_size: int = OPENAI_EMBEDDING_BATCH_SIZE,
    ) -> None:
        # 지연 임포트: 키가 없는 환경에서는 SDK 를 부를 일이 없다.
        import openai

        # SDK 응답을 덕 타이핑으로 읽으므로 타입은 Any 로 둔다.
        self._client: Any = openai.OpenAI(api_key=api_key)
        self._model = model
        self._dimension = dimension
        self._batch_size = max(1, batch_size)

    @property
    def dimension(self) -> int:
        """벡터 차원."""
        return self._dimension

    @property
    def recommended_min_similarity(self) -> float:
        """OpenAI 임베딩 기준 유사도 하한(초기 추정 — 스윕으로 재튜닝 필요)."""
        return OPENAI_MIN_SIMILARITY

    @property
    def model(self) -> str:
        """모델 식별자."""
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """여러 텍스트를 배치로 나눠 임베딩한다. 입력 순서를 그대로 지킨다."""
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            response = self._client.embeddings.create(model=self._model, input=batch)
            # 응답 순서를 신뢰하지 않고 index 로 다시 정렬한다.
            items = sorted(response.data, key=lambda item: item.index)
            if len(items) != len(batch):
                raise EmbeddingProviderError(
                    f"임베딩 개수가 입력과 다르다: 요청={len(batch)} 응답={len(items)}"
                )
            for item in items:
                vector = [float(value) for value in item.embedding]
                if len(vector) != self._dimension:
                    raise EmbeddingProviderError(
                        f"임베딩 차원이 다르다: 모델={self._model} "
                        f"기대={self._dimension} 실제={len(vector)}"
                    )
                vectors.append(vector)
        return vectors


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    """임베딩 프로바이더 팩토리. 프로바이더를 갈아끼우는 유일한 지점이다.

    `EMBEDDING_PROVIDER` 설정을 따른다:

    - `auto`(기본): `OPENAI_API_KEY` 가 있으면 openai, 없으면 hashing.
    - `openai` / `hashing`: 그대로 쓴다. 키가 필요한데 없으면 예외로 알린다
      (조용히 해싱으로 떨어지면 벡터 공간이 섞여 검색 결과가 조용히 망가진다).
    """
    settings = get_settings()
    requested = (settings.embedding_provider or "auto").strip().lower()

    choice = requested
    if choice == "auto":
        choice = "openai" if settings.openai_api_key else "hashing"

    if choice == "openai":
        if not settings.openai_api_key:
            raise EmbeddingProviderError(
                "EMBEDDING_PROVIDER=openai 인데 OPENAI_API_KEY 가 비어 있다"
            )
        logger.info(
            "임베딩 프로바이더: OpenAI(%s) [EMBEDDING_PROVIDER=%s]",
            settings.openai_embedding_model,
            requested,
        )
        return OpenAIEmbeddingProvider(
            settings.openai_api_key, model=settings.openai_embedding_model
        )

    if choice == "hashing":
        logger.info("임베딩 프로바이더: 로컬 해싱 [EMBEDDING_PROVIDER=%s]", requested)
        return HashingEmbeddingProvider()

    raise EmbeddingProviderError(
        f"알 수 없는 EMBEDDING_PROVIDER: {settings.embedding_provider!r} "
        "(auto | openai | hashing 중 하나여야 한다)"
    )

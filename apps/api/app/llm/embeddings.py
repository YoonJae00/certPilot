"""임베딩 프로바이더.

시제품 기본값은 외부 API 키 없이 도는 로컬 해싱 임베딩이다. **의미 품질이 낮다** —
어휘가 겹치는 문서만 잘 찾고 동의어·환언은 못 잡는다. 데모·테스트를 위한 자리
표시자이며, `get_embedding_provider()` 가 **프로바이더 교체 지점**이다. 실제 임베딩
API(예: OpenAI text-embedding-3-small, Voyage)를 붙일 때 여기만 바꾸면 된다.

교체 시 주의: `chunks.embedding` 은 `Vector(1536)` 으로 고정돼 있으므로 차원이 다른
모델로 갈아타려면 마이그레이션과 전체 재인제스트가 필요하다.
"""

import hashlib
import math
import re
from functools import lru_cache
from typing import Protocol

from app.models import EMBEDDING_DIM

# 어절 분리용. 한글·영숫자만 남기고 나머지는 경계로 본다.
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")

# 해싱 임베딩이 쓰는 n-gram 크기. 1-gram 은 어휘, 2-gram 은 짧은 구를 잡는다.
_NGRAM_SIZES = (1, 2)


class EmbeddingProvider(Protocol):
    """임베딩 프로바이더 인터페이스."""

    @property
    def dimension(self) -> int:
        """벡터 차원."""
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


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    """임베딩 프로바이더 팩토리. 프로바이더를 갈아끼우는 유일한 지점이다."""
    return HashingEmbeddingProvider()

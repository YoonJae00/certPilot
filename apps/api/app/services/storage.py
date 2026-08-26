"""오브젝트 스토리지(S3 / 로컬 MinIO).

업로드 **원문은 여기에만** 저장한다. DB 에는 마스킹된 청크 텍스트만 들어간다
(PRD §7 F2).
"""

import logging
from functools import lru_cache
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class StorageError(RuntimeError):
    """스토리지 접근 실패."""


class ObjectStorage:
    """S3 호환 스토리지 래퍼. 필요한 최소 동작(put/get/delete)만 노출한다."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @property
    def bucket(self) -> str:
        """버킷 이름."""
        return self._bucket

    def ensure_bucket(self) -> None:
        """버킷이 없으면 만든다. 이미 있으면 아무것도 하지 않는다."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchBucket", "NotFound", "403", "Forbidden"}:
                raise StorageError(f"버킷 확인 실패: {self._bucket}") from error

        try:
            self._client.create_bucket(Bucket=self._bucket)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            # 동시에 여러 워커가 만들면 이 코드가 온다. 실패가 아니다.
            if code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                raise StorageError(f"버킷 생성 실패: {self._bucket}") from error

    def put_object(self, key: str, data: bytes, content_type: str) -> None:
        """객체를 저장한다."""
        self.ensure_bucket()
        try:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )
        except ClientError as error:
            raise StorageError(f"객체 저장 실패: {key}") from error

    def get_object(self, key: str) -> bytes:
        """객체를 읽는다. 없으면 `StorageError`."""
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            raise StorageError(f"객체 조회 실패: {key}") from error
        body: bytes = response["Body"].read()
        return body

    def delete_object(self, key: str) -> None:
        """객체를 지운다. 없어도 성공으로 본다(S3 의 기본 동작)."""
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            raise StorageError(f"객체 삭제 실패: {key}") from error


def build_storage() -> ObjectStorage:
    """설정값으로 스토리지 클라이언트를 만든다(캐시하지 않는다).

    리전은 SigV4 서명에만 쓰인다. MinIO 는 어떤 값이든 통과시키지만 실제 S3 나
    오라클 Object Storage 의 S3 호환 API 는 버킷 리전과 일치해야 한다
    (불일치하면 `AuthorizationHeaderMalformed`). `S3_REGION` 으로 맞춘다.
    """
    settings = get_settings()
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return ObjectStorage(client, settings.s3_bucket)


@lru_cache
def get_storage() -> ObjectStorage:
    """스토리지 싱글턴. 테스트에서 갈아끼울 때는 `get_storage.cache_clear()` 를 부른다."""
    return build_storage()

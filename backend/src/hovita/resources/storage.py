from __future__ import annotations

import hashlib
import shutil
from io import BytesIO
from pathlib import Path
from typing import Protocol

from hovita.config.settings import get_settings


class ObjectStorage(Protocol):
    def put_bytes(self, object_key: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...

    def put_file(self, object_key: str, local_path: Path) -> str: ...

    def get_bytes(self, object_key: str) -> bytes: ...

    def resolve_path(self, object_key: str) -> Path: ...


class LocalObjectStorage:
    """Filesystem object store compatible with S3 key paths (for thin/dev/single-VPS)."""

    def __init__(self, root_dir: Path | None = None) -> None:
        settings = get_settings()
        self.root_dir = (root_dir or settings.local_storage_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _safe_target(self, object_key: str) -> Path:
        clean = object_key.lstrip("/").replace("..", "__")
        target = (self.root_dir / clean).resolve()
        if not str(target).startswith(str(self.root_dir)):
            raise ValueError(f"Unsafe object key traversal: {object_key}")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def put_bytes(
        self, object_key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        _ = content_type
        target = self._safe_target(object_key)
        target.write_bytes(data)
        return str(target)

    def put_file(self, object_key: str, local_path: Path) -> str:
        target = self._safe_target(object_key)
        if local_path.resolve() != target:
            shutil.copy2(local_path, target)
        return str(target)

    def get_bytes(self, object_key: str) -> bytes:
        target = self._safe_target(object_key)
        return target.read_bytes()

    def resolve_path(self, object_key: str) -> Path:
        return self._safe_target(object_key)


class MinioObjectStorage:
    """S3/MinIO object storage adapter with local cache path fallback."""

    def __init__(self) -> None:
        from minio import Minio

        settings = get_settings()
        self.bucket = settings.minio_bucket
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key.get_secret_value(),
            secret_key=settings.minio_secret_key.get_secret_value(),
            secure=settings.minio_secure,
        )
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)
        self._cache = LocalObjectStorage()

    def put_bytes(
        self, object_key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        self.client.put_object(
            self.bucket, object_key, BytesIO(data), length=len(data), content_type=content_type
        )
        return self._cache.put_bytes(object_key, data, content_type)

    def put_file(self, object_key: str, local_path: Path) -> str:
        self.client.fput_object(self.bucket, object_key, str(local_path))
        return self._cache.put_file(object_key, local_path)

    def get_bytes(self, object_key: str) -> bytes:
        resp = self.client.get_object(self.bucket, object_key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def resolve_path(self, object_key: str) -> Path:
        return self._cache.resolve_path(object_key)


def get_object_storage() -> LocalObjectStorage | MinioObjectStorage:
    settings = get_settings()
    if settings.storage_backend == "minio":
        return MinioObjectStorage()
    return LocalObjectStorage()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

"""Thin wrapper around the MinIO Python client.

Stores raw object bytes plus a small JSON sidecar (``<id>.meta.json``) holding
the original filename, content type, owner and creation timestamp so that
GetDocument can return them without relying on MinIO user-metadata quirks.
"""

from __future__ import annotations

import io
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from minio import Minio
from minio.error import S3Error

from app.config import settings


@dataclass
class DocumentMeta:
    document_id: str
    user_id: str
    filename: str
    content_type: str
    size_bytes: int
    created_at: str


def _meta_object_name(document_id: str) -> str:
    return f"{document_id}.meta.json"


class MinioStore:
    def __init__(self) -> None:
        self._client: Optional[Minio] = None
        self._ensured: set[str] = set()

    @property
    def client(self) -> Minio:
        if self._client is None:
            self._client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
            )
        return self._client

    def _ensure_bucket(self, bucket: str) -> None:
        if bucket in self._ensured:
            return
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)
        self._ensured.add(bucket)

    # ---- document storage -------------------------------------------------
    def store_document(
        self,
        *,
        user_id: str,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> DocumentMeta:
        bucket = settings.minio_bucket_docs
        self._ensure_bucket(bucket)
        document_id = uuid.uuid4().hex
        meta = DocumentMeta(
            document_id=document_id,
            user_id=user_id or "",
            filename=filename or document_id,
            content_type=content_type or "application/octet-stream",
            size_bytes=len(data),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.client.put_object(
            bucket,
            document_id,
            io.BytesIO(data),
            length=len(data),
            content_type=meta.content_type,
        )
        meta_bytes = json.dumps(asdict(meta), ensure_ascii=False).encode("utf-8")
        self.client.put_object(
            bucket,
            _meta_object_name(document_id),
            io.BytesIO(meta_bytes),
            length=len(meta_bytes),
            content_type="application/json",
        )
        return meta

    def get_document_bytes(self, document_id: str) -> bytes:
        bucket = settings.minio_bucket_docs
        self._ensure_bucket(bucket)
        response = self.client.get_object(bucket, document_id)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def get_document_meta(self, document_id: str) -> DocumentMeta:
        bucket = settings.minio_bucket_docs
        self._ensure_bucket(bucket)
        response = self.client.get_object(bucket, _meta_object_name(document_id))
        try:
            raw = json.loads(response.read().decode("utf-8"))
        finally:
            response.close()
            response.release_conn()
        return DocumentMeta(**raw)

    def document_exists(self, document_id: str) -> bool:
        bucket = settings.minio_bucket_docs
        self._ensure_bucket(bucket)
        try:
            self.client.stat_object(bucket, document_id)
            return True
        except S3Error:
            return False

    def delete_document(self, document_id: str) -> bool:
        bucket = settings.minio_bucket_docs
        self._ensure_bucket(bucket)
        if not self.document_exists(document_id):
            return False
        self.client.remove_object(bucket, document_id)
        try:
            self.client.remove_object(bucket, _meta_object_name(document_id))
        except S3Error:
            pass
        return True

    def presigned_document_url(self, document_id: str) -> str:
        bucket = settings.minio_bucket_docs
        self._ensure_bucket(bucket)
        return self.client.presigned_get_object(
            bucket,
            document_id,
            expires=timedelta(seconds=settings.presigned_url_ttl_seconds),
        )

    # ---- report storage ---------------------------------------------------
    def store_report(
        self,
        *,
        report_id: str,
        data: bytes,
        content_type: str,
    ) -> str:
        bucket = settings.minio_bucket_reports
        self._ensure_bucket(bucket)
        object_name = report_id
        self.client.put_object(
            bucket,
            object_name,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        return f"{bucket}/{object_name}"

    def presigned_report_url(self, report_id: str) -> str:
        bucket = settings.minio_bucket_reports
        self._ensure_bucket(bucket)
        return self.client.presigned_get_object(
            bucket,
            report_id,
            expires=timedelta(seconds=settings.presigned_url_ttl_seconds),
        )


store = MinioStore()

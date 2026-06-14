from __future__ import annotations

import os
import sys
from typing import Any

import grpc
from minio.error import S3Error

from app.config import settings
from app.minio_client import store
from app.report_generator import generate_report
from app.text_extraction import extract_text

PROTO_GEN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "proto_gen"))
if PROTO_GEN_DIR not in sys.path:
    sys.path.insert(0, PROTO_GEN_DIR)

try:
    import document_pb2
except ImportError:  # pragma: no cover - generated stubs may not exist in local smoke tests yet.
    document_pb2 = None


def _message_or_dict(message_name: str, data: dict[str, Any]):
    if document_pb2 is None:
        return data
    return getattr(document_pb2, message_name)(**data)


def _not_found(context: Any, document_id: str) -> None:
    context.set_code(grpc.StatusCode.NOT_FOUND)
    context.set_details(f"document not found: {document_id}")


class DocumentServicer:
    """Implements document.proto DocumentService business logic."""

    async def StoreDocument(self, request, context):
        if not request.data:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("data is required")
            return _message_or_dict("StoreDocumentResponse", {})
        try:
            meta = store.store_document(
                user_id=request.user_id,
                filename=request.filename,
                content_type=request.content_type,
                data=bytes(request.data),
            )
        except S3Error as exc:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(f"object storage error: {exc}")
            return _message_or_dict("StoreDocumentResponse", {})
        except Exception as exc:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(exc))
            return _message_or_dict("StoreDocumentResponse", {})

        return _message_or_dict(
            "StoreDocumentResponse",
            {
                "document_id": meta.document_id,
                "filename": meta.filename,
                "size_bytes": meta.size_bytes,
                "storage_path": f"{settings.minio_bucket_docs}/{meta.document_id}",
            },
        )

    async def ExtractText(self, request, context):
        document_id = request.document_id
        try:
            data = store.get_document_bytes(document_id)
        except S3Error:
            _not_found(context, document_id)
            return _message_or_dict("ExtractTextResponse", {"document_id": document_id})
        except Exception as exc:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(exc))
            return _message_or_dict("ExtractTextResponse", {"document_id": document_id})

        content_type = ""
        filename = ""
        try:
            meta = store.get_document_meta(document_id)
            content_type = meta.content_type
            filename = meta.filename
        except Exception:
            pass  # Metadata sidecar optional; extraction sniffs the bytes anyway.

        result = extract_text(data=data, content_type=content_type, filename=filename)
        return _message_or_dict(
            "ExtractTextResponse",
            {
                "document_id": document_id,
                "extracted_text": result.extracted_text,
                "doc_type": result.doc_type,
                "page_count": result.page_count,
                "extraction_method": result.extraction_method,
            },
        )

    async def GetDocument(self, request, context):
        document_id = request.document_id
        if not store.document_exists(document_id):
            _not_found(context, document_id)
            return _message_or_dict("GetDocumentResponse", {"document_id": document_id})
        try:
            meta = store.get_document_meta(document_id)
            download_url = store.presigned_document_url(document_id)
        except S3Error as exc:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(f"object storage error: {exc}")
            return _message_or_dict("GetDocumentResponse", {"document_id": document_id})
        except Exception as exc:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(exc))
            return _message_or_dict("GetDocumentResponse", {"document_id": document_id})

        return _message_or_dict(
            "GetDocumentResponse",
            {
                "document_id": meta.document_id,
                "user_id": meta.user_id,
                "filename": meta.filename,
                "size_bytes": meta.size_bytes,
                "content_type": meta.content_type,
                "download_url": download_url,
                "created_at": meta.created_at,
            },
        )

    async def DeleteDocument(self, request, context):
        try:
            success = store.delete_document(request.document_id)
        except Exception as exc:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(exc))
            return _message_or_dict("DeleteDocumentResponse", {"success": False})
        return _message_or_dict("DeleteDocumentResponse", {"success": success})

    async def GenerateReport(self, request, context):
        try:
            result = generate_report(
                check_id=request.check_id,
                report_json=request.report_json,
                fmt=request.format,
            )
        except S3Error as exc:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(f"object storage error: {exc}")
            return _message_or_dict("GenerateReportResponse", {})
        except Exception as exc:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(exc))
            return _message_or_dict("GenerateReportResponse", {})

        return _message_or_dict(
            "GenerateReportResponse",
            {
                "report_id": result.report_id,
                "download_url": result.download_url,
            },
        )

from __future__ import annotations

import os

try:
    from pydantic_settings import BaseSettings
except ImportError:  # Allows local parser scripts to run before service deps are installed.
    BaseSettings = None


if BaseSettings is not None:
    class Settings(BaseSettings):
        minio_endpoint: str = "minio:9000"
        minio_access_key: str = "minioadmin"
        minio_secret_key: str = "minioadmin"
        minio_bucket_docs: str = "documents"
        minio_bucket_reports: str = "reports"
        minio_secure: bool = False
        presigned_url_ttl_seconds: int = 7 * 24 * 3600
        ocr_lang: str = "rus+eng"
        # Below how many extracted characters a PDF is treated as scanned and routed to OCR.
        ocr_min_text_chars: int = 32
        # Rasterization caps for scanned-PDF OCR: bound memory/CPU per request.
        ocr_pdf_dpi: int = 200
        ocr_pdf_max_pages: int = 30

        model_config = {"env_file": ".env", "extra": "ignore"}
else:
    def _as_bool(value: str | None, default: bool) -> bool:
        if value is None:
            return default
        return value.lower() in {"1", "true", "yes"}

    class Settings:
        minio_endpoint: str = os.getenv("MINIO_ENDPOINT", "minio:9000")
        minio_access_key: str = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
        minio_secret_key: str = os.getenv("MINIO_SECRET_KEY", "minioadmin")
        minio_bucket_docs: str = os.getenv("MINIO_BUCKET_DOCS", "documents")
        minio_bucket_reports: str = os.getenv("MINIO_BUCKET_REPORTS", "reports")
        minio_secure: bool = _as_bool(os.getenv("MINIO_SECURE"), False)
        presigned_url_ttl_seconds: int = int(os.getenv("PRESIGNED_URL_TTL_SECONDS", str(7 * 24 * 3600)))
        ocr_lang: str = os.getenv("OCR_LANG", "rus+eng")
        ocr_min_text_chars: int = int(os.getenv("OCR_MIN_TEXT_CHARS", "32"))
        ocr_pdf_dpi: int = int(os.getenv("OCR_PDF_DPI", "200"))
        ocr_pdf_max_pages: int = int(os.getenv("OCR_PDF_MAX_PAGES", "30"))


settings = Settings()

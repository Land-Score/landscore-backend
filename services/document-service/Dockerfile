FROM python:3.12-slim
RUN apt-get update && apt-get install -y \
    tesseract-ocr tesseract-ocr-rus \
    libpango-1.0-0 libpangoft2-1.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY services/document-service/pyproject.toml .
RUN uv pip install --system -r pyproject.toml
COPY services/document-service/app/ app/
COPY proto/ proto/
COPY scripts/gen_protos.py gen_protos.py
RUN python gen_protos.py proto app/proto_gen proto/document.proto
EXPOSE 50054
CMD ["python", "-m", "app.main"]

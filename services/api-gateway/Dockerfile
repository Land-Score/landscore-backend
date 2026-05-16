FROM python:3.12-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install Python deps (cache layer)
COPY services/api-gateway/pyproject.toml .
RUN uv pip install --system -r pyproject.toml

# Copy app source
COPY services/api-gateway/app/ app/

# Generate gRPC stubs from proto contracts
COPY proto/ proto/
COPY scripts/gen_protos.py gen_protos.py
RUN python gen_protos.py proto app/proto_gen \
    proto/auth.proto \
    proto/check.proto \
    proto/search.proto \
    proto/document.proto \
    proto/data_collector.proto

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]

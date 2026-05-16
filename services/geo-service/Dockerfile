FROM python:3.12-slim
RUN apt-get update && apt-get install -y libgdal-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY services/geo-service/pyproject.toml .
RUN uv pip install --system -r pyproject.toml
COPY services/geo-service/app/ app/
COPY proto/ proto/
COPY scripts/gen_protos.py gen_protos.py
RUN python gen_protos.py proto app/proto_gen proto/geo.proto
EXPOSE 50057
CMD ["python", "-m", "app.main"]

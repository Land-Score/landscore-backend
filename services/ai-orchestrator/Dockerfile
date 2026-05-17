FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY services/ai-orchestrator/pyproject.toml .
RUN uv pip install --system -r pyproject.toml
COPY services/ai-orchestrator/app/ app/
COPY proto/ proto/
COPY scripts/gen_protos.py gen_protos.py
RUN python gen_protos.py proto app/proto_gen proto/data_collector.proto proto/geo.proto proto/check.proto proto/search.proto
EXPOSE 50055
CMD ["python", "-m", "app.main"]

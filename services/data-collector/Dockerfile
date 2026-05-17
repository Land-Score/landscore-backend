FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY services/data-collector/pyproject.toml .
RUN uv pip install --system -r pyproject.toml
COPY services/data-collector/app/ app/
COPY proto/ proto/
COPY scripts/gen_protos.py gen_protos.py
RUN python gen_protos.py proto app/proto_gen proto/data_collector.proto
EXPOSE 50056
CMD ["python", "-m", "app.main"]

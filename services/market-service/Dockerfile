FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY services/market-service/pyproject.toml .
RUN uv pip install --system -r pyproject.toml
COPY services/market-service/app/ app/
COPY proto/ proto/
COPY scripts/gen_protos.py gen_protos.py
RUN python gen_protos.py proto app/proto_gen proto/market.proto
EXPOSE 50058
CMD ["python", "-m", "app.main"]

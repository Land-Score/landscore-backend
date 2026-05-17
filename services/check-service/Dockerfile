FROM python:3.12-slim
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY services/check-service/pyproject.toml .
RUN uv pip install --system -r pyproject.toml

COPY services/check-service/app/ app/
COPY services/check-service/alembic/ alembic/
COPY services/check-service/alembic.ini alembic.ini
COPY services/check-service/start.sh start.sh

COPY proto/ proto/
COPY scripts/gen_protos.py gen_protos.py
RUN python gen_protos.py proto app/proto_gen proto/check.proto && chmod +x start.sh

EXPOSE 50052
CMD ["./start.sh"]

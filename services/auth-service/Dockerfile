FROM python:3.12-slim
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY services/auth-service/pyproject.toml .
RUN uv pip install --system -r pyproject.toml

COPY services/auth-service/app/ app/
COPY services/auth-service/alembic/ alembic/
COPY services/auth-service/alembic.ini alembic.ini
COPY services/auth-service/start.sh start.sh

COPY proto/ proto/
COPY scripts/gen_protos.py gen_protos.py
RUN python gen_protos.py proto app/proto_gen proto/auth.proto && chmod +x start.sh

EXPOSE 50051
CMD ["./start.sh"]

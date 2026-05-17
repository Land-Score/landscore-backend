FROM python:3.12-slim
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY services/search-service/pyproject.toml .
RUN uv pip install --system -r pyproject.toml

COPY services/search-service/app/ app/
COPY services/search-service/alembic/ alembic/
COPY services/search-service/alembic.ini alembic.ini
COPY services/search-service/start.sh start.sh

COPY proto/ proto/
COPY scripts/gen_protos.py gen_protos.py
RUN python gen_protos.py proto app/proto_gen proto/search.proto && chmod +x start.sh

EXPOSE 50053
CMD ["./start.sh"]

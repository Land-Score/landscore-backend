FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY services/auth-service/pyproject.toml .
RUN uv pip install --system -r pyproject.toml
COPY services/auth-service/app/ app/
EXPOSE 50051
CMD ["python", "-m", "app.main"]

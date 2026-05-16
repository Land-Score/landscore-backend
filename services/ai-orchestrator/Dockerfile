FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY services/ai-orchestrator/pyproject.toml .
RUN uv pip install --system -r pyproject.toml
COPY services/ai-orchestrator/app/ app/
EXPOSE 50055
CMD ["python", "-m", "app.main"]

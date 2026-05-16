FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY services/data-collector/pyproject.toml .
RUN uv pip install --system -r pyproject.toml
COPY services/data-collector/app/ app/
EXPOSE 50056
CMD ["python", "-m", "app.main"]

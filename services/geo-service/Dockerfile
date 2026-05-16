FROM python:3.12-slim
RUN apt-get update && apt-get install -y libgdal-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml .
RUN uv pip install --system -r pyproject.toml
COPY app/ app/
EXPOSE 50057
CMD ["python", "-m", "app.main"]

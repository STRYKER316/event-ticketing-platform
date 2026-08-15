# Build context is /services (the uv workspace root) — see infra/docker-compose.yml.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY search-service ./search-service

RUN uv sync --frozen --no-dev --package search-service

WORKDIR /workspace/search-service

EXPOSE 8002

CMD ["/workspace/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]

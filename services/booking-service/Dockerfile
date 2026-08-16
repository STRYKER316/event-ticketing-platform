# Build context is /services (the uv workspace root) — see infra/docker-compose.yml.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY _shared/auth ./_shared/auth
COPY booking-service ./booking-service

RUN uv sync --frozen --no-dev --package booking-service

WORKDIR /workspace/booking-service

EXPOSE 8003

CMD ["/workspace/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]

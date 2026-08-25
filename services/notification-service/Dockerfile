# Build context is /services (the uv workspace root) — see infra/docker-compose.yml.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

RUN groupadd --system appuser && useradd --system --gid appuser --no-create-home --shell /usr/sbin/nologin appuser

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY notification-service ./notification-service

RUN uv sync --frozen --no-dev --package notification-service

WORKDIR /workspace/notification-service

# Own the tree only after uv sync, not before — the venv/build artifacts are created as root.
RUN chown -R appuser:appuser /workspace
USER appuser

EXPOSE 8005

CMD ["/workspace/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8005"]

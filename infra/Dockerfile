FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY infra/requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app

COPY drift_guard ./drift_guard
USER app

EXPOSE 8080
CMD ["sh", "-c", "uvicorn drift_guard.main:app --host 0.0.0.0 --port ${PORT}"]

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# requirements.lock pins the full resolved set (regenerate with
# `uv pip compile requirements.txt -o requirements.lock --python-version 3.12`)
# so image rebuilds can't silently pick up new dependency versions.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY app ./app
COPY config ./config

# The container writes nothing (config is an :ro mount, the DB is
# Postgres) — run unprivileged.
RUN useradd --create-home --uid 1000 sportsdash
USER sportsdash

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

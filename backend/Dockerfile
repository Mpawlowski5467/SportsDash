FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config

# The container writes nothing (config is an :ro mount, the DB is
# Postgres) — run unprivileged.
RUN useradd --create-home --uid 1000 sportsdash
USER sportsdash

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

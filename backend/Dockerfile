FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
# Esta imagem ainda é a de desenvolvimento (docker-compose.yml roda runserver, não gunicorn) —
# instala requirements-dev.txt (que já inclui requirements.txt) pra manter pytest/black/ruff
# disponíveis via `docker compose exec backend ...`, como documentado no CLAUDE.md. Quando existir
# uma imagem de produção separada, ela deve instalar só requirements.txt.
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

EXPOSE 8000

# Dependências antes dos fontes para reaproveitar o cache de build.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; print('\n'.join(p['dependencies']))" > /tmp/requirements.txt && \
    pip install -r /tmp/requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# Migrations na subida porque o plano grátis do Render não tem pre-deploy. A porta vem de
# $PORT (o Render injeta a dele) e o IP/esquema do cliente, do proxy à frente.
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]

# O Compose de desenvolvimento usa este target para testes e ferramentas.
FROM base AS development
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; print('\n'.join(p['optional-dependencies']['dev']))" > /tmp/dev-requirements.txt && \
    pip install -r /tmp/dev-requirements.txt
COPY tests ./tests

# Target padrão: apenas código, migrations e dependências de execução.
FROM base AS production

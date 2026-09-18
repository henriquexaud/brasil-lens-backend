# ---------------------------------------------------------------------------
# Imagem da API.
#
# O projeto **não** é instalado como pacote: o código vive em /app (WORKDIR) e
# é importado de lá — é o que `pythonpath = ["."]` no pytest e
# `prepend_sys_path = .` no alembic.ini já pressupõem. Por isso o passo abaixo
# extrai as dependências do pyproject.toml e instala só elas: `pip install .`
# tentaria construir o wheel do projeto e falharia, porque nesta camada o
# diretório `app/` ainda não existe (ele entra depois, de propósito, para o
# cache das dependências sobreviver a cada edição de código).
#
# O pyproject.toml continua sendo a única fonte das versões — não há
# requirements.txt para sair de sincronia.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; print('\n'.join(p['dependencies'] + p['optional-dependencies']['dev']))" > /tmp/requirements.txt && \
    pip install -r /tmp/requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

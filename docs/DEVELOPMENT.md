# Desenvolvimento do Backend

O backend serve dados geográficos e ambientais ao mapa do Brasil Lens. A estrutura de `app/` separa rotas HTTP (`api/v1`), configuração e cache (`core`), persistência espacial e ambiental (`models`, `repositories`), fontes (`providers`), ingestão (`jobs`) e contratos HTTP (`schemas`).

## Desenvolvimento local

Docker e Docker Compose v2 executam toda a stack sem dependências Python no host. Para executar a API diretamente, use Python 3.12 (versão usada e validada no Docker) e PostgreSQL/PostGIS; Redis é opcional. A instalação e configuração estão no [README](../README.md). Com Docker Compose:

```bash
make up-api
make ingest
make test
make lint
```

O Dockerfile separa os targets `production` e `development`; `make test`, `make lint` e `make dev` selecionam o segundo automaticamente. Os testes rodam com o código local montado, não com uma cópia antiga dos fontes.

`make ingest` importa territórios e geometrias do IBGE para suportar o mapa, busca e recortes espaciais. As credenciais e endereços de fontes ambientais são configurados pelas variáveis descritas no README.

## Alterações de dados

Use migrations Alembic para mudanças de schema e preserve os códigos territoriais e geometrias consumidos pelas camadas ambientais. Ao adicionar uma fonte, implemente um adaptador em `app/providers/`, persista apenas os dados necessários ao domínio ambiental e exponha contratos por schema Pydantic.

## Verificações

```bash
make test
make lint
make smoke
```

O smoke test consulta disponibilidade da API e a camada geográfica do mapa.

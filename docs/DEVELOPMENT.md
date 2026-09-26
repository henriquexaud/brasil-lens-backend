# Desenvolvimento do Backend

O backend serve dados geográficos e ambientais ao mapa do Brasil Lens. A estrutura de `app/` separa rotas HTTP (`api/v1`), configuração e cache (`core`), persistência espacial e ambiental (`models`, `repositories`), fontes (`providers`), ingestão (`jobs`) e contratos HTTP (`schemas`).

## Desenvolvimento local

Python 3.12, PostgreSQL com PostGIS e Redis são necessários para executar a stack. Com Docker Compose:

```bash
make up-api
make ingest
make test
make lint
```

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

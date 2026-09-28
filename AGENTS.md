# Brasil Lens — backend

API FastAPI (Python 3.12, SQLAlchemy async, PostGIS, Redis opcional) que normaliza fontes públicas de clima e meio ambiente para o mapa. Este repositório guarda a documentação de sistema; o frontend fica em outro repositório (`../frontend` no workspace).

Antes de mudar comportamento, leia [docs/invariants.md](docs/invariants.md). Visão geral, princípios, contrato e fluxos: [docs/architecture.md](docs/architecture.md).

## Onde buscar contexto

| Vai mexer em | Leia antes e atualize junto |
|---|---|
| clima: `open_meteo.py`, `weather_forecast.py`, `spatial_interpolation.py` | [docs/weather.md](docs/weather.md) |
| alertas: `providers/inmet`, `providers/cemaden`, `jobs/*alerts*`, `services/weather.py` | [docs/alerts.md](docs/alerts.md) |
| focos: `providers/inpe.py`, `services/fire_*.py` | [docs/fire.md](docs/fire.md) |
| território, `/map`, IBGE, hidrografia, migrations | [docs/geography.md](docs/geography.md) |
| `api/`, `schemas/`, erros, cache (`core/`), fluxo entre repositórios | [docs/architecture.md](docs/architecture.md) e `frontend/src/api/types.ts` |
| comandos, testes, receitas | [docs/development.md](docs/development.md) |
| decisão com alternativas reais | [docs/decisions.md](docs/decisions.md) |

## Regras

- **Doc na mesma tarefa.** Uma mudança de arquitetura, contrato, fluxo de dados, provider, cache ou regra de domínio atualiza o doc da tabela acima antes de a tarefa ser dada como pronta. Se doc e código divergirem, o código vale: corrija o doc. Edite o doc existente em vez de criar outro.
- **Camadas** em um só sentido: `api/v1` → `services` → `repositories` | `providers`. Rotas são finas.
- **Erros** são subclasses de `core/errors.DomainError`, com mensagem em pt-BR e `code` estável. Migrations nunca são editadas, só criadas.
- **Testes** não acessam a rede; regressão corrigida ganha teste. Dependência nova só quando claramente vale o custo.
- Comentário no código só para o porquê que não é evidente.

## Verificar

```sh
docker run --rm --network brasil-lens_default -v "$PWD":/app -w /app \
  -e DATABASE_URL=postgresql+asyncpg://brasil_lens:brasil_lens@db:5432/brasil_lens \
  -e WEATHER_REFRESH_ENABLED=false -e REDIS_CACHE_PREFIX=bl-test:v4 \
  brasil-lens-api-dev:latest sh -c "pytest -q && ruff check app tests && ruff format --check app tests && mypy app"
```

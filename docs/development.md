# Desenvolvimento e testes

Instalação e variáveis de ambiente: [README](../README.md), `.env.example` e `app/core/config.py`.

## Comandos

- `make up-api`: sobe banco e API (as migrations rodam no startup).
- `make dev`: stack com reload.
- `make ingest` / `make ingest-quick`: ingestão IBGE completa / sem malha municipal.
- `make revision m="..."`: nova migration.
- `make psql`: psql no banco.
- `make smoke`: confere a API e o mapa contra a API rodando.
- Sem container para a API: Python 3.12, `pip install -e '.[dev]'`, `alembic upgrade head` e `uvicorn app.main:app --reload`, com PostGIS acessível.
- Verificação sem Python no host: o comando está no [AGENTS.md](../AGENTS.md). Use a imagem `brasil-lens-api-dev` (a `brasil-lens-api` é de produção, sem pytest). Se ela estiver defasada: `docker build --target development -t brasil-lens-api-dev:latest .`. Sem banco disponível: `pytest -q -m 'not db'`.
- Atenção: os `make` usam o projeto Compose `brasil-lens`, compartilhado com a stack local, e `make reset` apaga o banco.

## Testes

- **Sem rede:** use `respx` ou fixtures reais em `tests/fixtures/` (`load_fixture`).
- **PostGIS:** marque com `pytest.mark.db`; a fixture `session` faz rollback. Rotas com banco sobrescrevem `get_session` com a sessão do teste; o modelo é `_api()` em `test_map_queries.py`.
- `conftest.py` desliga o Redis e zera os cooldowns em todo teste. Services com estado global expõem `reset_state()`/`clear_cache()`.
- O nome do teste descreve o comportamento (`test_outage_serves_last_reading_as_previous_data_and_pauses_the_source`). Regressão corrigida ganha teste.
- Mudou um provider? Cubra o parser com fixture real, campo faltante (→ `null`) e formato inesperado (→ `ProviderError`). Mudou cache ou fallback? Cubra frescor, marcação `stale`/`partial` e o caminho sem Redis.
- mypy estrito; `DeprecationWarning` de `app.*` quebra a suíte.

## Receitas

- **Fonte nova:** adaptador em `providers/` com `base.http_client`/`get_json` (retry) ou um `httpx` com timeout; `SourceCooldown` próprio; cache com TTL e fallback marcado; credencial em `core/config.py` via variável de ambiente; seção no doc do domínio.
- **Rota nova:** `api/v1/<domínio>.py` fina → service → schema `CamelModel` → `router.py` → atualizar `frontend/src/api/types.ts` e o doc do domínio.
- **Schema:** uma migration nova, sem editar as existentes. Preserve `ibge_code`, `parent_id` e os LODs.
- **Job agendado:** registrar em `weather_scheduler._JOBS`, usar `_runner.job_session`; se o frescor aparece na interface, incluir em `services/weather._SOURCE_DEFINITIONS`.

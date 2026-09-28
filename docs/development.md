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

## Deploy

Frontend na Vercel, API no Render e banco no Neon, todos no plano grátis ([ADR-09](decisions.md)).

1. **Neon:** projeto Postgres 17 na região AWS `us-east-1`, a mesma do Render. No 17 o Neon traz PostGIS 3.5; no 16, a 3.3, mais antiga que a do Compose (3.4). Use a connection string direta, sem `-pooler` no host: com uma instância e até 10 conexões, o pooler não é necessário. Cole a URL como o Neon a entrega; `core/config.py` troca o driver para asyncpg e `sslmode` por `ssl`.
2. **Ingestão**, uma vez, da sua máquina (o Render tem 0,1 de CPU): `docker run --rm -e DATABASE_URL='<url do Neon>' brasil-lens-api sh -c "alembic upgrade head && python -m app.jobs.bootstrap"`.
3. **Render:** New → Blueprint com este repositório (`render.yaml`). Preencha `DATABASE_URL` e `CORS_ORIGINS` (a URL da Vercel, sem barra no fim). A imagem roda as migrations ao subir e escuta em `$PORT`. Sem `REDIS_URL`: com uma instância, o cache em memória basta.
4. **Vercel:** importe o repositório do frontend (preset Vite) com `VITE_API_BASE_URL=https://<serviço>.onrender.com/api/v1`. O valor é fixado no build; se mudar, faça redeploy.

Limites que moldam o uso:
- O Render dorme após 15 min sem tráfego e leva ~1 min para acordar. O agendador de alertas para junto e roda assim que a API acorda; os alertas do CEMADEN expiram sozinhos (invariante 10).
- Não use pinger para manter o Render acordado: o agendador consultaria o banco a cada 10 min e esgotaria as 100 CU-h/mês do Neon, que dorme após 5 min sem consultas. O health check do Render usa `/health`, que não toca o banco.
- A instância tem 512 MB. Medido com todas as malhas municipais em cache, o processo fica em ~150 MB, porque os caches de GeoJSON guardam JSON pronto ([architecture](architecture.md)).
- O Neon tem 0,5 GB (o banco ocupa ~80 MB) e 5 GB/mês de egress; o cache do `/map` evita reler as malhas.

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

| Parte | Endereço | Publica quando |
|---|---|---|
| Frontend | https://brasil-lens.vercel.app (projeto Vercel `brasil-lens`) | push na `main` do frontend |
| API | https://brasil-lens-api.onrender.com (Swagger em `/docs`; serviço Render `srv-date5dugekts73affgeg`) | push na `main` deste repositório |
| Banco | Neon, projeto `brasil-lens` (Postgres 17, AWS `us-east-1`) | migrations na subida da API |

Configuração: no Render, `DATABASE_URL`, `CORS_ORIGINS=https://brasil-lens.vercel.app`, `APP_ENV=production`, `LOG_FORMAT=json`, `OPEN_METEO_URL=https://brasil-lens.vercel.app/api/open-meteo` e `OPEN_METEO_RELAY_KEY`, sem `REDIS_URL`; na Vercel, `VITE_API_BASE_URL=https://brasil-lens-api.onrender.com/api/v1`, fixada no build (se mudar, faça redeploy), e a mesma `OPEN_METEO_RELAY_KEY` (production e preview), que o repasse exige. O domínio da Vercel e o `CORS_ORIGINS` andam juntos.

### Montar do zero (CLIs `neon`, `render` e `vercel`)

1. **Neon:** `neon projects create --name brasil-lens --region-id aws-us-east-1 --pg-version 17 --database brasil_lens --set-context` e `export NEON_URL="$(neon connection-string --database-name brasil_lens)"`. O 17 traz PostGIS 3.5 (o 16, a 3.3). A connection string é a direta, sem `-pooler`: com uma instância e até 10 conexões, o pooler não é necessário. A URL entra como o Neon a entrega; `core/config.py` troca o driver para asyncpg e `sslmode` por `ssl`.
2. **Ingestão**, da sua máquina (o Render tem 0,1 de CPU): `docker run --rm -e DATABASE_URL="$NEON_URL" brasil-lens-api sh -c "alembic upgrade head && python -m app.jobs.bootstrap"`. Idempotente; refazer muda o `data_version` e renova os caches.
3. **Render:** `render services create --name brasil-lens-api --type web_service --repo https://github.com/henriquexaud/brasil-lens-backend --branch main --runtime docker --plan free --region virginia --health-check-path /api/v1/health --env-var "DATABASE_URL=$NEON_URL" --env-var "CORS_ORIGINS=https://brasil-lens.vercel.app" --env-var APP_ENV=production --env-var LOG_FORMAT=json --confirm`. A imagem roda as migrations ao subir, porque o plano grátis não tem pre-deploy, e escuta em `$PORT`. `python -m app.server` faz as duas coisas no mesmo processo: a 0,1 de CPU, a subida caiu de 37 s para 20 s.
4. **Vercel**, na pasta do frontend: `vercel link --yes --project brasil-lens`, `printf '%s' "https://brasil-lens-api.onrender.com/api/v1" | vercel env add VITE_API_BASE_URL production --no-sensitive`, `vercel deploy --prod` e `vercel git connect`. O deploy automático exige o app da Vercel no GitHub com acesso ao repositório.
5. **Repasse da Open-Meteo** ([ADR-10](decisions.md)): gere uma chave (`openssl rand -hex 32`), grave-a na Vercel (`vercel env add OPEN_METEO_RELAY_KEY production --sensitive < arquivo`, e o mesmo para `preview`) e no Render (painel → Environment), junto com `OPEN_METEO_URL`. Sem a chave, o repasse responde 401.

### Operar

- Logs: `render logs --resources srv-date5dugekts73affgeg --tail` e `vercel logs <url-do-deploy>`. Saúde: `curl https://brasil-lens-api.onrender.com/api/v1/health/ready`.
- Trocar uma variável do Render: painel → Environment → Save and deploy. Na Vercel: `vercel env update <nome> production` e redeploy.
- Nova migration entra no próximo deploy da API; nova ingestão é o passo 2.

### Limites que moldam o uso

- O Render dorme após 15 min sem tráfego e leva ~1 min para acordar. O agendador de alertas para junto e roda assim que a API acorda; os alertas do CEMADEN expiram sozinhos (invariante 10).
- Não use pinger para manter o Render acordado: o agendador consultaria o banco a cada 10 min e esgotaria as 100 CU-h/mês do Neon, que dorme após 5 min sem consultas. O health check do Render usa `/health`, que não toca o banco.
- A instância tem 512 MB e 0,1 de CPU. Medido com todas as malhas municipais em cache, o processo fica em ~150 MB, porque os caches de GeoJSON guardam JSON pronto ([architecture](architecture.md)).
- O Neon tem 0,5 GB (o banco ocupa ~80 MB) e 5 GB/mês de egress; o cache do `/map` evita reler as malhas.
- A cota grátis da Open-Meteo (10 mil chamadas/dia) é por IP, e o IP de saída do Render é compartilhado por todos os serviços da região: em 2026-09-28 a primeira chamada do serviço já recebeu "limite diário atingido". Por isso a API chama a Open-Meteo pelo repasse da Vercel. Se o clima voltar a dar `provider_rate_limited` em produção, confira a chave nos dois lados e os logs da função (`vercel logs`).

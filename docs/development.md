# Desenvolvimento e testes

Instalação e variáveis de ambiente: [README](../README.md), `.env.example` e `app/core/config.py`.

## Comandos

- `make up-api`: sobe banco e API (as migrations rodam no startup).
- `make dev`: stack com reload.
- `make ingest` / `make ingest-quick`: ingestão IBGE completa / sem malha municipal.
- `make ingest-socioeconomic`: indicadores e séries anuais do IBGE. Equivalente a `python -m app.jobs.import_indicators`, depois de `alembic upgrade head` e da ingestão territorial. O bootstrap territorial permanece independente. Para uma primeira carga, prefira a série completa; `--latest` em banco vazio não permite calcular crescimento quando falta a observação anterior. `python -m app.jobs.seed_indicators` só atualiza nomes/descrições do catálogo.
- `make revision m="..."`: nova migration.
- `make psql`: psql no banco.
- `make smoke`: confere a API e o mapa contra a API rodando.
- Sem container para a API: Python 3.12, `pip install -e '.[dev]'`, `alembic upgrade head` e `uvicorn app.main:app --reload`, com PostGIS acessível.
- Verificação sem Python no host: o comando está no [AGENTS.md](../AGENTS.md). Use a imagem `brasil-lens-api-dev` (a `brasil-lens-api` é de produção, sem pytest). Se ela estiver defasada: `docker build --target development -t brasil-lens-api-dev:latest .`. Sem banco disponível: `pytest -q -m 'not db'`.
- Atenção: os `make` usam o projeto Compose `brasil-lens`, compartilhado com a stack local, e `make reset` apaga o banco.

## Testes

- **Sem rede:** use `respx` ou fixtures reais em `tests/fixtures/` (`load_fixture`).
- **PostGIS:** marque com `pytest.mark.db`; a fixture `session` faz rollback. Teste que lê território ingerido (UFs, municípios) também leva `pytest.mark.ingested`: com o banco vazio ele **pula** com a mensagem "Banco sem dados", em vez de falhar ou, pior, passar vazio. Sem banco nenhum a `session` pula tudo que é `db`. Pulado não é verde: confira o número de `skipped` e rode com `-rs` antes de dar a suíte como validada. Rotas com banco sobrescrevem `get_session` com a sessão do teste; o modelo é `_api()` em `test_map_queries.py`.
- `conftest.py` desliga o Redis e zera os cooldowns em todo teste. Services com estado global expõem `reset_state()`/`clear_cache()`.
- O nome do teste descreve o comportamento (`test_outage_serves_last_reading_as_previous_data_and_pauses_the_source`). Regressão corrigida ganha teste.
- Mudou um provider? Cubra o parser com fixture real, campo faltante (→ `null`) e formato inesperado (→ `ProviderError`). Mudou cache ou fallback? Cubra frescor, marcação `stale`/`partial` e o caminho sem Redis.
- mypy estrito; `DeprecationWarning` de `app.*` quebra a suíte.
- `test_socioeconomic.py` cobre ano único, cobertura PNAD, cache próprio, reutilização nacional entre recortes/anos equivalentes, renovação de resumos por ingestão/geografia/ano, ETag, município sem geometria, denominadores sem uso de ano futuro e rollback de lotes. Classificação, agregados e fórmulas têm fixtures sem rede. A revisão histórica de remoção é testada dentro de uma transação que retira temporariamente os valores novos e os restaura no rollback.

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

Configuração: no Render, `DATABASE_URL`, `CORS_ORIGINS=https://brasil-lens.vercel.app`, `APP_ENV=production`, `LOG_FORMAT=json`, `OPEN_METEO_URL=https://brasil-lens.vercel.app/api/open-meteo` e `OPEN_METEO_RELAY_KEY`, sem `REDIS_URL`; na Vercel, `VITE_API_BASE_URL=/api/v1`, fixada no build (se mudar, faça redeploy), e a mesma `OPEN_METEO_RELAY_KEY` (production e preview), que o repasse exige. O rewrite de `frontend/vercel.json` repassa `/api/v1/*` para o Render; o cookie HttpOnly fica no domínio do frontend e não depende de cookies de terceiros. Atualize a variável antiga com URL absoluta antes do deploy da autenticação. O domínio da Vercel e o `CORS_ORIGINS` andam juntos; previews precisam de sua origem explícita para escritas.

### Montar do zero (CLIs `neon`, `render` e `vercel`)

1. **Neon:** `neon projects create --name brasil-lens --region-id aws-us-east-1 --pg-version 17 --database brasil_lens --set-context` e `export NEON_URL="$(neon connection-string --database-name brasil_lens)"`. O 17 traz PostGIS 3.5 (o 16, a 3.3). A connection string é a direta, sem `-pooler`: com uma instância e até 10 conexões, o pooler não é necessário. A URL entra como o Neon a entrega; `core/config.py` troca o driver para asyncpg e `sslmode` por `ssl`.
2. **Ingestão**, da sua máquina (o Render tem 0,1 de CPU): `docker run --rm -e DATABASE_URL="$NEON_URL" brasil-lens-api sh -c "alembic upgrade head && python -m app.jobs.bootstrap"`. Idempotente; refazer muda o `data_version` e renova os caches.
3. **Render:** `render services create --name brasil-lens-api --type web_service --repo https://github.com/henriquexaud/brasil-lens-backend --branch main --runtime docker --plan free --region virginia --health-check-path /api/v1/health --env-var "DATABASE_URL=$NEON_URL" --env-var "CORS_ORIGINS=https://brasil-lens.vercel.app" --env-var APP_ENV=production --env-var LOG_FORMAT=json --confirm`. A imagem roda as migrations ao subir, porque o plano grátis não tem pre-deploy, e escuta em `$PORT`. `python -m app.server` faz as duas coisas no mesmo processo: a 0,1 de CPU, a subida caiu de 37 s para 20 s.
4. **Vercel**, na pasta do frontend: `vercel link --yes --project brasil-lens`, `printf '%s' "/api/v1" | vercel env add VITE_API_BASE_URL production --no-sensitive`, `vercel deploy --prod` e `vercel git connect`. O deploy automático exige o app da Vercel no GitHub com acesso ao repositório.
5. **Repasse da Open-Meteo** ([ADR-10](decisions.md)): gere uma chave (`openssl rand -hex 32`), grave-a na Vercel (`vercel env add OPEN_METEO_RELAY_KEY production --sensitive < arquivo`, e o mesmo para `preview`) e no Render (painel → Environment), junto com `OPEN_METEO_URL`. Sem a chave, o repasse responde 401.

### Notificações do PWA

Web Push usa os serviços dos navegadores, sem domínio de e-mail, conta de remetente ou serviço pago. O cron usa os runners padrão gratuitos do repositório público no GitHub. No iOS/iPadOS, o usuário precisa adicionar o PWA à Tela de Início (16.4+); a permissão só aparece por um clique explícito, nunca no cadastro.

1. Gere um par VAPID uma vez: `python -m app.jobs.generate_vapid_keys --output /private/tmp/brasil-lens-vapid.env`, com as dependências do projeto instaladas. O arquivo tem permissão 0600 e o comando não imprime a chave privada nem sobrescreve arquivo existente. Copie as chaves aos painéis de configuração; não inclua o arquivo no git. Preserve esse par: trocá-lo exige novas inscrições nos dispositivos.
2. No Render, configure `VAPID_PUBLIC_KEY`. Essa chave pública é devolvida pela API ao app. O padrão `PUSH_NOTIFICATIONS_ENABLED=false` mantém o envio somente no cron; para enviar também com a API acordada, configure `VAPID_PRIVATE_KEY` e habilite essa flag. A chave privada nunca vai para a Vercel/bundle. `VAPID_SUBJECT` já usa a URL pública do Brasil Lens.
3. Publique a migration `0011_notifications` pela API antes do frontend e do cron. No GitHub do backend, em Settings → Secrets and variables → Actions, adicione os Repository Secrets `DATABASE_URL` (mesmo Neon), `VAPID_PUBLIC_KEY` e `VAPID_PRIVATE_KEY`. O workflow “Notificações do PWA” roda aos minutos 17 e 47 e pode ser iniciado em Actions → Run workflow. Sem os três Secrets ele apenas informa o que falta. Um erro numa fonte não impede consultar a outra ou enviar avisos ainda vigentes. Não consulta Open-Meteo nem roda migrations.
4. Publique o frontend. O usuário segue municípios, escolhe os sinos e autoriza o dispositivo. Permissão negada não ativa o sino. Os sinos antigos ficam desligados na migration e precisam ser reativados; os municípios continuam seguidos. “Desativar neste dispositivo” e Sair removem sua inscrição, preservando outros dispositivos da conta.

O GitHub pode atrasar o cron e desativá-lo em repositórios públicos sem atividade por 60 dias; reative em Actions. Se o repositório ficar privado, confira a cota de runners antes de manter o cron. A rotina desperta o Neon a cada 30 min, sem manter o Render ou o banco permanentemente ativos; monitore a cota de compute já descrita abaixo. A periodicidade não garante aviso instantâneo, especialmente se um aviso durar menos que o intervalo entre consultas. `python -m app.jobs.dispatch_notifications` permite execução manual com as mesmas variáveis.

Testes não enviam mensagens a dispositivos reais. Para conferir o envio real, use apenas um dispositivo/conta de teste do dono e um aviso real, após configurar as chaves. Regras de deduplicação, TTL e futuras integrações: [alerts](alerts.md#notificacoes-do-pwa).

### Operar

- Logs: `render logs --resources srv-date5dugekts73affgeg --tail` e `vercel logs <url-do-deploy>`. Saúde: `curl https://brasil-lens-api.onrender.com/api/v1/health/ready`.
- Trocar uma variável do Render: painel → Environment → Save and deploy. Na Vercel: `vercel env update <nome> production` e redeploy.
- Nova migration entra no próximo deploy da API; nova ingestão é o passo 2.
- Para o contexto Socioeconômico: publicar a API com `0012_socioeconomic`, executar `python -m app.jobs.import_indicators` contra o banco de destino e então publicar o frontend. A migration cria o catálogo; o mapa precisa da ingestão para ter valores. Atualização dos indicadores é um job separado, sem mudar o agendador de clima/notificações.

### Limites que moldam o uso

- O Render dorme após 15 min sem tráfego e leva ~1 min para acordar. O agendador para junto e roda quando a API acorda; o cron de notificações ingere e envia mesmo com ela dormindo. Os alertas do CEMADEN expiram sozinhos (invariante 10).
- Não use pinger para manter o Render acordado: o agendador consultaria o banco a cada 10 min e esgotaria as 100 CU-h/mês do Neon, que dorme após 5 min sem consultas. O health check do Render usa `/health`, que não toca o banco.
- A instância tem 512 MB e 0,1 de CPU. Medido com todas as malhas municipais em cache, o processo fica em ~150 MB, porque os caches de GeoJSON guardam JSON pronto ([architecture](architecture.md)).
- O Neon tem 0,5 GB e 5 GB/mês de egress; o banco base ocupa ~80 MB. Com a série socioeconômica completa, foram medidos ~230 MB e 1,09 milhão de valores na cópia local em 2026-10-07. A consulta separada de valores e o cache do `/map` evitam retransmitir a malha a cada indicador/ano.
- A cota grátis da Open-Meteo (10 mil chamadas/dia) é por IP, e o IP de saída do Render é compartilhado por todos os serviços da região: em 2026-09-28 a primeira chamada do serviço já recebeu "limite diário atingido". Por isso a API chama a Open-Meteo pelo repasse da Vercel. Se o clima voltar a dar `provider_rate_limited` em produção, confira a chave nos dois lados e os logs da função (`vercel logs`).

### Ingerir Política

Depois de `alembic upgrade head` e da geografia, execute uma vez para cada ano, com o banco de destino configurado:

```sh
python -m app.jobs.import_elections --year 2022
python -m app.jobs.import_elections --year 2024
python -m app.jobs.import_elections --year 2026
```

Os arquivos ficam no cache de download `/tmp/brasil-lens-tse` (`--cache-dir` muda o diretório). São arquivos oficiais do [Portal de Dados Abertos do TSE](https://dadosabertos.tse.jus.br/dataset/resultados-2026), nas famílias `votacao_candidato_munzona`, `votacao_partido_munzona`, `detalhe_votacao_munzona` e no de/para `municipio_tse_ibge` da CDN do TSE. CSV em Latin-1, separado por `;`; a ingestão usa os campos modernos de votos válidos e escolhe o arquivo BRASIL quando existe, evitando somá-lo às cópias por UF. Falha de formato ou de correspondência territorial impede a troca da edição.

Para atualizar 2026, execute `python -m app.jobs.import_elections --year 2026 --refresh`. Após a publicação final e o encerramento do pleito, acrescente `--complete` para retirar a marca de andamento. Sem `--refresh`, arquivos locais são reaproveitados; não há atualização automática, consulta ao TSE nas rotas ou ampliação para outros anos. Planeje alguns GB de disco temporário para a consolidação; a aplicação guarda apenas resumos e identidades necessárias. Não inclua os ZIPs ou o SQLite no git.

Na stack compartilhada, use um container temporário com o código montado e a rede existente para migrations/ingestão. Para testar a interface, API e Vite devem usar portas separadas (por exemplo 8001/5174), `REDIS_CACHE_PREFIX` próprio, `WEATHER_REFRESH_ENABLED=false` e `HYDROGRAPHY_WARMUP_ENABLED=false`; não recrie os serviços compartilhados. Testes de banco usam PostGIS temporário e cópia apenas dos dados públicos. As regressões de política estão em `tests/test_political.py` e `frontend/tests/political.test.mjs`.

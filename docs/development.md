# Desenvolvimento e testes

Instalação e variáveis de ambiente: [README](../README.md), `.env.example` e `app/core/config.py`.

## Comandos

- `make up-api`: sobe banco e API (as migrations rodam no startup).
- `make dev`: stack com reload.
- `make ingest` / `make ingest-quick`: ingestão IBGE completa / sem malha municipal.
- `make ingest-socioeconomic`: indicadores e séries anuais do IBGE. Equivalente a `python -m app.jobs.import_indicators`, depois de `alembic upgrade head` e da ingestão territorial. O bootstrap territorial permanece independente. Para uma primeira carga, prefira a série completa; `--latest` em banco vazio não permite calcular crescimento quando falta a observação anterior. `python -m app.jobs.seed_indicators` só atualiza nomes/descrições do catálogo.
- `python -m app.jobs.import_environment`: desmatamento (PRODES/INPE) e florestas (MapBiomas) do contexto Meio ambiente, depois de `alembic upgrade head` (`0014`). Baixa 12 JSON do TerraBrasilis e o zip de ~78 MB do MapBiomas uma vez; `--only prodes|mapbiomas` limita a fonte e `--mapbiomas-file caminho.zip` usa um arquivo baixado à mão. Leva ~15 s na cópia local.
- `python -m app.jobs.import_transport`: malha do contexto Transporte, depois de `alembic upgrade head` (`0015`) e da ingestão territorial (a extensão por município usa a malha `detail`). Lê o SNV mais recente do DNIT (WFS, ~1 min por página) e as camadas do BC250 2025 por HTTP Range; `--bc250-file caminho.zip` usa o zip baixado à mão. Leva ~1,5 min e substitui a malha inteira numa transação.
- `python -m app.jobs.import_education`: Censo da Educação Superior (INEP) do contexto Educação, depois de `alembic upgrade head` (`0017`) e da ingestão territorial. Baixa um zip de microdados por ano (~16–37 MB de 2019 a 2023; o de 2024, sem compressão, tem 457 MB) para `--cache-dir` (padrão `/tmp/brasil-lens-inep`; zip já baixado é reaproveitado) e grava os anos de `--years` (padrão `2019-2024`; aceita `2024` ou `2022,2024`) numa transação. Na cópia local, os seis anos levaram ~1 min com o download de 2019–2023; só 2024, já em cache, 4 s. O servidor do INEP não envia o certificado intermediário da RNP; ele vai em `app/providers/certs/` e vale até 2030-11-19.
- `scripts/transport_tiles/build.sh <dir>` e `python -m app.jobs.import_transport_tiles --mbtiles <dir>/transport.mbtiles`: tiles OSM do contexto Transporte, depois de `alembic upgrade head` (`0016`). O script precisa de Docker e de ~6 GB livres no diretório; o download de 2,1 GB da Geofabrik é o passo mais lento.
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
- `test_transport.py` cobre o leitor de shapefile/DBF (partes degeneradas, forma nula, nulos numéricos, registro apagado), os filtros do BC250 (federal, particular, não construído; estadual × local), o zip local, o SNV (superfície, planejadas e travessias fora, paginação, versão ausente) e, com banco, a multilinha recortada à área, os portos e o `identify`.
- `test_education.py` cobre o leitor dos microdados (só redes públicas, graduação presencial por município, EAD só no total, sequencial/privada/especial fora, os dois nomes do arquivo de IES, coluna faltante, zip inválido, ano sem instituição pública), a soma por rede, tipo e território sem inventar zero e, com banco, a lista de instituições por recorte, rede e tipo, o detalhe com os municípios, o ano sem Censo e o ETag.
- `test_environment.py` cobre o PRODES (só incrementos anuais, zero medido, ano parcial descartado, formato inesperado), o xlsx do MapBiomas (soma de biomas, recorte de anos, aba ausente), agregados ponderados por área e o catálogo separado por contexto.
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
| Frontend | https://www.brasillens.com.br (projeto Vercel `brasil-lens`; também em https://brasil-lens.vercel.app) | push na `main` do frontend |
| API | https://brasil-lens-api.onrender.com (Swagger em `/docs`; serviço Render `srv-date5dugekts73affgeg`) | push na `main` deste repositório |
| Banco | Neon, projeto `brasil-lens` (Postgres 17, AWS `us-east-1`) | migrations na subida da API |

Configuração: no Render, `DATABASE_URL`, `CORS_ORIGINS=https://www.brasillens.com.br,https://brasillens.com.br,https://brasil-lens.vercel.app`, `APP_ENV=production`, `LOG_FORMAT=json`, `OPEN_METEO_URL=https://brasil-lens.vercel.app/api/open-meteo` e `OPEN_METEO_RELAY_KEY`, sem `REDIS_URL`; na Vercel, `VITE_API_BASE_URL=/api/v1`, fixada no build (se mudar, faça redeploy), e a mesma `OPEN_METEO_RELAY_KEY` (production e preview), que o repasse exige. O rewrite de `frontend/vercel.json` repassa `/api/v1/*` para o Render; o cookie HttpOnly fica no domínio do frontend e não depende de cookies de terceiros. Atualize a variável antiga com URL absoluta antes do deploy da autenticação. Os domínios do frontend e o `CORS_ORIGINS` andam juntos; previews precisam de sua origem explícita para escritas.

Domínio: `brasillens.com.br`, registrado no Registro.br, com a zona no DNS do próprio Registro.br em modo avançado: `A` no domínio raiz para `216.198.79.1` e `CNAME` em `www` para o valor que a Vercel mostra em Settings → Domains. Na Vercel, o `www` é o principal, e o domínio raiz redireciona para ele (308); o certificado é emitido pela Vercel. O `brasil-lens.vercel.app` continua no ar, porque sessões, PWAs instalados e inscrições de push são presos à origem em que foram criados.

### Montar do zero (CLIs `neon`, `render` e `vercel`)

1. **Neon:** `neon projects create --name brasil-lens --region-id aws-us-east-1 --pg-version 17 --database brasil_lens --set-context` e `export NEON_URL="$(neon connection-string --database-name brasil_lens)"`. O 17 traz PostGIS 3.5 (o 16, a 3.3). A connection string é a direta, sem `-pooler`: com uma instância e até 10 conexões, o pooler não é necessário. A URL entra como o Neon a entrega; `core/config.py` troca o driver para asyncpg e `sslmode` por `ssl`.
2. **Ingestão**, da sua máquina (o Render tem 0,1 de CPU): `docker run --rm -e DATABASE_URL="$NEON_URL" brasil-lens-api sh -c "alembic upgrade head && python -m app.jobs.bootstrap"`. Idempotente; refazer muda o `data_version` e renova os caches.
3. **Render:** `render services create --name brasil-lens-api --type web_service --repo https://github.com/henriquexaud/brasil-lens-backend --branch main --runtime docker --plan free --region virginia --health-check-path /api/v1/health --env-var "DATABASE_URL=$NEON_URL" --env-var "CORS_ORIGINS=https://www.brasillens.com.br,https://brasillens.com.br,https://brasil-lens.vercel.app" --env-var APP_ENV=production --env-var LOG_FORMAT=json --confirm`. A imagem roda as migrations ao subir, porque o plano grátis não tem pre-deploy, e escuta em `$PORT`. `python -m app.server` faz as duas coisas no mesmo processo: a 0,1 de CPU, a subida caiu de 37 s para 20 s.
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
- Para o contexto Educação: publicar a API com `0017_education`, executar `python -m app.jobs.import_education` contra o banco de destino e então publicar o frontend. Sem a ingestão, o seletor mostra Educação com "Sem dados". Um ano novo do Censo (o INEP publica no fim do ano seguinte) é `--years 2025` mais o ajuste de `DEFAULT_YEARS`.
- Para o contexto Transporte: publicar a API com `0015_transport` e `0016_transport_tiles`, executar `python -m app.jobs.import_transport` e `python -m app.jobs.import_transport_tiles --mbtiles …` contra o banco de destino e então publicar o frontend. Sem os tiles, o mapa do Transporte fica só com hidrovias e portos; sem a malha oficial, a extensão mostra "Sem dados".
- Para o contexto Meio ambiente: publicar a API com `0014_environment_indicators`, executar `python -m app.jobs.import_environment` contra o banco de destino e então publicar o frontend (que lê a sessão v6 e o novo seletor). Sem a ingestão, Desmatamento e Florestas mostram "Sem dados"; Fogo e rios funcionam sem ela.
- Para o contexto Socioeconômico: publicar a API com `0012_socioeconomic`, executar `python -m app.jobs.import_indicators` contra o banco de destino e então publicar o frontend. A migration cria o catálogo; o mapa precisa da ingestão para ter valores. Atualização dos indicadores é um job separado, sem mudar o agendador de clima/notificações.

### Limites que moldam o uso

- O Render dorme após 15 min sem tráfego e leva ~1 min para acordar. O agendador para junto e roda quando a API acorda; o cron de notificações ingere e envia mesmo com ela dormindo. Os alertas do CEMADEN expiram sozinhos (invariante 10).
- Não use pinger para manter o Render acordado: o agendador consultaria o banco a cada 10 min e esgotaria as 100 CU-h/mês do Neon, que dorme após 5 min sem consultas. O health check do Render usa `/health`, que não toca o banco.
- A instância tem 512 MB e 0,1 de CPU. Medido com todas as malhas municipais em cache, o processo fica em ~150 MB, porque os caches de GeoJSON guardam JSON pronto ([architecture](architecture.md)).
- O Neon tem 0,5 GB e 5 GB/mês de egress; o banco base ocupa ~80 MB. Com a série socioeconômica completa, foram medidos ~230 MB e 1,09 milhão de valores na cópia local em 2026-10-07. O Meio ambiente acrescentou 128,9 mil valores e ~14 MB (banco local de 322 para 336 MB em 2026-10-26). O Transporte acrescentou ~51 MB (114,5 mil trechos e 130 portos), medido numa cópia isolada em 2026-10-26. A consulta separada de valores e o cache do `/map` evitam retransmitir a malha a cada indicador/ano.
- A Educação ocupa menos de 1 MB: 1,7 mil instituições e 8 mil ofertas por município de 2019 a 2024 (~360 KB com índices) e 15,8 mil valores no motor de indicadores (12 indicadores, rede × tipo), medidos numa cópia isolada em 2026-10-28.
- Os tiles OSM do Transporte ocupam ~300 MB no banco (306,7 mil tiles, do zoom 4 ao 13), medidos numa cópia isolada em 2026-10-26; o maior tem ~145 KB comprimido.
- A cota grátis da Open-Meteo (10 mil chamadas/dia) é por IP, e o IP de saída do Render é compartilhado por todos os serviços da região: em 2026-09-28 a primeira chamada do serviço já recebeu "limite diário atingido". Por isso a API chama a Open-Meteo pelo repasse da Vercel. Se o clima voltar a dar `provider_rate_limited` em produção, confira a chave nos dois lados e os logs da função (`vercel logs`).

### Ingerir Política

Depois de `alembic upgrade head` e da geografia, execute uma vez para cada ano, com o banco de destino configurado:

```sh
python -m app.jobs.import_elections --year 2022
python -m app.jobs.import_elections --year 2024
python -m app.jobs.import_elections --year 2026
```

Os arquivos ficam no cache de download `/tmp/brasil-lens-tse` (`--cache-dir` muda o diretório). São arquivos oficiais do [Portal de Dados Abertos do TSE](https://dadosabertos.tse.jus.br/dataset/resultados-2026), nas famílias `votacao_candidato_munzona`, `votacao_partido_munzona`, `detalhe_votacao_munzona` e no de/para `municipio_tse_ibge` da CDN do TSE. CSV em Latin-1, separado por `;`; a ingestão usa os campos modernos de votos válidos e escolhe o arquivo BRASIL quando existe, evitando somá-lo às cópias por UF. Falha de formato ou de correspondência territorial impede a troca da edição.

Para atualizar 2026, execute `python -m app.jobs.import_elections --year 2026 --refresh`. Após a publicação final e o encerramento do pleito, acrescente `--complete` para retirar a marca de andamento. Sem `--refresh`, arquivos locais são reaproveitados; não há atualização automática, consulta ao TSE nas rotas ou ampliação para outros anos. Os arquivos abertos costumam chegar dias depois do resultado. Até lá, `--live-round 2` complementa a edição com o turno já totalizado no [site de resultados do TSE](https://resultados.tse.jus.br): o job lê `oficial/comum/config/ele-c.json`, escolhe as eleições ordinárias do ano e turno e baixa um JSON por município e cargo (cerca de 5.800 arquivos pequenos no 2º turno de 2026, com 8 pedidos simultâneos e cache em `divulgacao_<ano>_<turno>/`). Cada JSON vira as mesmas colunas dos CSVs, e o restante da consolidação não muda. Totalização incompleta (`tf` diferente de `s`) impede a troca da edição. Um cargo/turno que já tenha votos nos arquivos abertos prevalece e ignora a divulgação; por isso a mesma linha de comando continua correta depois que o TSE publicar os ZIPs. Exemplo: `python -m app.jobs.import_elections --year 2026 --live-round 2`. Planeje alguns GB de disco temporário para a consolidação; a aplicação guarda apenas resumos e identidades necessárias. Não inclua os ZIPs ou o SQLite no git.

Na stack compartilhada, use um container temporário com o código montado e a rede existente para migrations/ingestão. Para testar a interface, API e Vite devem usar portas separadas (por exemplo 8001/5174), `REDIS_CACHE_PREFIX` próprio, `WEATHER_REFRESH_ENABLED=false` e `HYDROGRAPHY_WARMUP_ENABLED=false`; não recrie os serviços compartilhados. Testes de banco usam PostGIS temporário e cópia apenas dos dados públicos. As regressões de política estão em `tests/test_political.py` e `frontend/tests/political.test.mjs`.

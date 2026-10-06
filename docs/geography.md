# Geografia (IBGE, território e hidrografia ANA)

## Território

- `territories`: níveis `country`, `region`, `state` e `municipality`, ligados por `parent_id`, com `ibge_code` único, `abbreviation`, `capital_territory_id`, `bbox_*` e busca sem acento.
- `territory_geometries` (PK `(territory_id, lod)`): `canonical` é a malha do IBGE; `overview` (0,005°) e `detail` (0,001°) são derivadas na ingestão com `ST_SimplifyPreserveTopology`.
- `/map` nunca serve a `canonical` (o padrão é `overview` para país/região e `detail` para UF/município, e municípios exigem `parent`). A `canonical` é usada em `POST /territories/locate` (`ST_Covers`, coordenadas não cacheadas), nas áreas geodésicas e em `/weather/municipal-boundaries` (paginada, até 40 por página).
- Ponto de clima de um município: `ST_PointOnSurface` do `overview`, em ordem de farthest-point sampling a partir da capital.
- Cache do `/map`: o JSON pronto fica em memória por `(nível, pai, LOD, data_version)`, até 64 entradas por 5 min (`READ_CACHE_*`), e no Redis por 24 h. Todas as malhas municipais somam ~17 MB (medido). A resposta leva ETag e `max-age=3600`.

Código: `api/v1/map.py`, `territories.py` · `services/map.py`, `territories.py` · `repositories/territories.py`, `map_projection.py`, `boundaries.py`, `viewport.py`.

## Contas e acompanhamento

A migration `0010_user_accounts` cria `users` (nome, e-mail único normalizado, hash de senha e tema) e `user_sessions` (hash do token, FK da conta e validade). Acrescenta FK de `followed_municipalities.user_id` para `users.id`, mantendo a unicidade por conta/código IBGE. Os usuários legados, inclusive `local`, são preservados sem e-mail/senha e não podem entrar; seus municípios não são atribuídos automaticamente a novos cadastros. A malha e as migrations anteriores ficam inalteradas. Contrato e fluxo de acesso em [architecture](architecture.md).

`0011_notifications` acrescenta `notifications_opt_in_at` aos acompanhamentos e começa com os sinos desligados, incluindo os antigos, sem remover nenhum município seguido. `notification_events` guarda um aviso por conta/aviso/município/versão; `push_subscriptions`, os dispositivos por conta; `push_deliveries`, cada evento/inscrição. FKs removem vínculos ao apagar conta, aviso ou inscrição. A abrangência usa códigos explícitos da fonte ou a malha municipal canônica. Fluxo em [alerts](alerts.md#notificacoes-do-pwa).

## Ingestão IBGE

`python -m app.jobs.bootstrap [--skip-municipal-geometries] [--states 35,31]` roda `import_territories` (Localidades v1) e depois `import_geometries` (Malhas v3; a municipal tem ~60 MB, timeout de 120 s e concorrência 4). É idempotente e grava `ingestion_runs`/`datasets`, que definem o `data_version`. As tolerâncias só mudam na próxima ingestão. Nenhuma requisição de usuário chama o IBGE.

## Hidrografia (ANA/SNIRH)

`GET /hydrography?zoom=&bbox=` (`services/hydrography.py`). Só o zoom e a área definem a resposta; a rota não usa o banco.
- **Zoom < 6:** rios do snapshot `services/data/major_rivers.json`, filtrados por área de drenagem, e lagos grandes da ANA no recorte do Brasil. O bbox é ignorado: uma só entrada de cache para qualquer enquadramento, aquecida no `lifespan`.
- **Zoom ≥ 6:** ArcGIS REST do SNIRH (rios e massas d'água) na área pedida, com o detalhe definido por `hydro_detail(zoom)`. Rios e lagos saem em paralelo, assim como os lotes de 250 feições, com no máximo 4 conexões com a ANA.
- **Peso:** os trechos de cada rio são emendados (`_merge_lines`) e depois simplificados e recortados. Anéis de lago, ilha ou buraco com área menor que `(4 × tolerância)²`, poucos pixels no detalhe, são descartados (`_visible_rings`). Nas escalas nacional e regional, isso reduz a resposta a menos da metade.
- **Área pedida:** o frontend encaixa o bbox numa grade (1° no zoom 6–8, 0,5° no 8–10, 0,25° acima), reaproveita qualquer área já carregada que cubra a vista e não manda bbox abaixo do zoom 6.
- **Cache:** chave `(detalhe, área)`, 24 h em memória (como JSON pronto) e no Redis, com HTTP `max-age` de 1 h. Se a ANA falha, entra o cooldown de 60 s, os rios caem para o snapshot, os lagos saem e a resposta vira `partial`, com cache de só 60 s (sem Redis, e o mesmo `max-age`).
- Testes: `test_hydrography.py`.

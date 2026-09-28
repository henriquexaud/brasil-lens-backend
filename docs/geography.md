# Geografia (IBGE, território e hidrografia ANA)

## Território

- `territories`: níveis `country`, `region`, `state` e `municipality`, ligados por `parent_id`, com `ibge_code` único, `abbreviation`, `capital_territory_id`, `bbox_*` e busca sem acento.
- `territory_geometries` (PK `(territory_id, lod)`): `canonical` é a malha do IBGE; `overview` (0,005°) e `detail` (0,001°) são derivadas na ingestão com `ST_SimplifyPreserveTopology`.
- `/map` nunca serve a `canonical` (o padrão é `overview` para país/região e `detail` para UF/município, e municípios exigem `parent`). A `canonical` é usada em `POST /territories/locate` (`ST_Covers`, coordenadas não cacheadas), nas áreas geodésicas e em `/weather/municipal-boundaries` (paginada, até 40 por página).
- Ponto de clima de um município: `ST_PointOnSurface` do `overview`, em ordem de farthest-point sampling a partir da capital.

Código: `api/v1/map.py`, `territories.py` · `services/map.py`, `territories.py` · `repositories/territories.py`, `map_projection.py`, `boundaries.py`, `viewport.py`.

## Ingestão IBGE

`python -m app.jobs.bootstrap [--skip-municipal-geometries] [--states 35,31]` roda `import_territories` (Localidades v1) e depois `import_geometries` (Malhas v3; a municipal tem ~60 MB, timeout de 120 s e concorrência 4). É idempotente e grava `ingestion_runs`/`datasets`, que definem o `data_version`. As tolerâncias só mudam na próxima ingestão. Nenhuma requisição de usuário chama o IBGE.

## Hidrografia (ANA/SNIRH)

`GET /hydrography` (`services/hydrography.py`):
- **Zoom < 6:** só o snapshot `services/data/major_rivers.json`, filtrado por área de drenagem, sem rede e com uma única entrada de cache para qualquer enquadramento.
- **Zoom ≥ 6:** ArcGIS REST do SNIRH (rios e massas d'água) na caixa do recorte, com o detalhe definido por `hydro_detail(zoom)`; os rios são consolidados, recortados e simplificados.
- **Área pedida:** o frontend encaixa o bbox numa grade (1° no zoom 6–8, 0,5° no 8–10, 0,25° acima) e não manda bbox abaixo do zoom 6, para que pans pequenos reaproveitem a mesma chave de cache e a mesma chamada à ANA.
- **Cache:** 24 h em memória e no Redis. Se a ANA falha, entra o cooldown de 60 s, os rios caem para o snapshot, os lagos saem, a resposta vira `partial` e o cache dura só 60 s (sem Redis).
- Testes: `test_hydrography.py`.

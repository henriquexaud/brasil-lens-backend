# Focos de calor (INPE BDQueimadas)

Consulta sob demanda, sem persistência, com janela padrão de 48 h. O recorte é `country`, `state` (código IBGE de 2 dígitos) ou `municipality` (7 dígitos). Código: `providers/inpe.py`, `services/fire_hotspots.py`, `fire_summary.py` e `fire_scope.py`. Testes: `test_fire_hotspots.py` e `test_fire_summary.py`.

## Três rotas, uma janela

- `GET /fire-hotspots` faz um WFS com `count=1` e devolve a metadata: total, detecção mais recente, janela, `wmsUrl`, `wmsLayer` e o `cqlFilter`. O navegador desenha os pontos pelo WMS do INPE com **esse** filtro.
- `GET /fire-hotspots/summary?at=` pagina o WFS em CSV (10 mil por página, 4 em paralelo) e agrega por município e UF: densidade = focos × 1000 / km² (área geodésica da malha canônica), `count48h`, top 5 e `unassignedCount`. O total é conferido antes e depois; se mudou no meio, a resposta é erro, não um total incoerente.
- `GET /fire-hotspots/identify` faz um WFS com `BBOX`, `count=20`. A fonte devolve os N **mais recentes** da caixa, não os mais próximos, por isso a API reordena por distância.

Todos os filtros saem de `fire_scope.py` (`id_0=33`, `id_1` ou `id_2`, e a faixa de `data_hora_gmt`). O `at` precisa estar entre 1 min no futuro e 2 h atrás.

## Cache e falhas

- Metadata: 600 s em memória e no Redis, com fallback de 1 h servido como `stale`.
- Resumo: 2 h, com chave que inclui `at` e `data_version`, e sem fallback.
- Lock por chave, falha memorizada por 60 s e `inpe_cooldown` de 60 s para a fonte toda.
- Normalização: números inválidos ou negativos viram `null`; horário sem fuso é tratado como UTC.

## Limites da fonte

- O GeoServer responde 414 para URLs acima de ~8 KB, então o `SLD_BODY` precisa ficar abaixo de ~5 KB. O SLD aceita `env('wms_scale_denominator')`, `Recode` e `ElseFilter`.
- ~78 % das detecções são VIIRS 375 m e ~16 % são GOES-19, que repete o mesmo pixel a cada ~10 min: contagem de focos não é o número de incêndios.

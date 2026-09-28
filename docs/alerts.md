# Alertas (INMET e CEMADEN)

Alertas oficiais em vigor, gravados na tabela `weather_alerts`. Um agendador os ingere a cada 10 min (`jobs/weather_scheduler.py`, desligável com `WEATHER_REFRESH_ENABLED=false`) e `GET /weather/alerts` os lê do banco, com 90 s de cache do JSON pronto, ETag e `Cache-Control: no-cache`: o polling do frontend recebe `304` vazio enquanto nada muda. Os polígonos saem com 5 casas decimais (~1 m); as fontes mandam 8, o que dobrava a resposta. Um ciclo que falha é registrado em `ingestion_runs` e não apaga nada; `/weather/sources` mostra se cada job está `stale` (passou de 3 intervalos) ou `unavailable`.

Código: `providers/inmet/alerts.py`, `providers/cemaden/alerts.py`, `jobs/_weather_alerts.py` (upsert em lote), `services/weather.py` (normalização).

## Modelo unificado

- `category` vem do provider: o CEMADEN é `geo_hydrological`, o resto `meteorological`.
- `severityLevel` (`moderate`, `high`, `very_high`, `extreme`) é derivado do texto original, que continua em `severity`:
  - INMET: "grande perigo"/"extremo" → `extreme`; "muito alto" → `very_high`; "potencial"/"moderado" → `moderate`; "perigo"/"alto" → `high`.
  - CEMADEN: "muito alto" → `very_high`; "alto" → `high`; o resto → `moderate`.
- O enum ainda declara `potential` e `danger`, que nunca são emitidos: é legado.

## INMET

`GET {INMET_ALERTS_BASE_URL}/avisos/ativos`, lista `hoje`. Avisos `encerrado`, sem id, sem datas ou sem polígono são descartados. O polígono pode vir como string e é corrigido com `ST_MakeValid`. A validade é o próprio `data_fim`, então, se a fonte cai, os avisos gravados continuam valendo até lá. O parser não tem teste dedicado (lacuna).

Estações (`providers/inmet/stations.py`, job manual, `/weather/stations`): o agendador não roda esse job e o frontend não consome a rota. Integre ou remova antes de investir nelas.

## CEMADEN

WFS `GetFeature` em `cemaden_dev:alertas_vigentes_siaden` com `status=1`. Feições com outro status são descartadas mesmo que o filtro falhe. O evento perde o sufixo " - {nivel}"; `codibge` vira `affected_ibge_codes`; o PDF vira uma instrução. Como a fonte não informa validade, cada ciclo grava `expires = agora + 30 min` (`CEMADEN_ALERT_VALIDITY_BUFFER_SECONDS`) e o alerta some sozinho quando sai da lista ou a ingestão para. Não troque isso por "nunca expira". Testes: `test_providers_cemaden.py`.

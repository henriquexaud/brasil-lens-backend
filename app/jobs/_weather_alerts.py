"""Upsert de `weather_alerts`, compartilhado entre INMET e CEMADEN.

Extraído de `import_weather_inmet_alerts.py` ao adicionar o CEMADEN: os dois
jobs gravam exatamente as mesmas colunas, a partir do mesmo
`WeatherAlertRecord` (`app/providers/records.py`) — só o `provider` muda.
Manter duas cópias quase idênticas do mesmo UPSERT de ~15 colunas seria o
tipo de redundância que uma segunda fonte deixa óbvia; a primeira (só INMET)
não justificava a extração sozinha.
"""

from __future__ import annotations

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.results import affected_rows
from app.providers.records import WeatherAlertRecord

_UPSERT_ALERTS_SQL = text(
    """
    INSERT INTO weather_alerts AS target (
        provider, external_id, event, severity, color, description, onset, expires, polygon,
        affected_ibge_codes, risks, instructions, dataset_id, ingestion_run_id,
        created_at, updated_at
    )
    SELECT :provider, source.external_id, source.event, source.severity, source.color,
           source.description, source.onset, source.expires,
           ST_Multi(ST_CollectionExtract(
               ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(source.polygon_geojson), 4326)), 3
           )),
           source.affected_ibge_codes::jsonb, source.risks::jsonb, source.instructions::jsonb,
           :dataset_id, :ingestion_run_id, now(), now()
      FROM (
          SELECT UNNEST(CAST(:external_ids AS text[]))  AS external_id,
                 UNNEST(CAST(:events AS text[]))         AS event,
                 UNNEST(CAST(:severities AS text[]))     AS severity,
                 UNNEST(CAST(:colors AS text[]))         AS color,
                 UNNEST(CAST(:descriptions AS text[]))   AS description,
                 UNNEST(CAST(:onsets AS timestamptz[]))  AS onset,
                 UNNEST(CAST(:expirations AS timestamptz[])) AS expires,
                 UNNEST(CAST(:polygons AS text[]))       AS polygon_geojson,
                 UNNEST(CAST(:affected AS text[]))       AS affected_ibge_codes,
                 UNNEST(CAST(:risks AS text[]))          AS risks,
                 UNNEST(CAST(:instructions AS text[]))   AS instructions
      ) AS source
    ON CONFLICT (provider, external_id) DO UPDATE
       SET event                = EXCLUDED.event,
           severity             = EXCLUDED.severity,
           color                = EXCLUDED.color,
           description          = EXCLUDED.description,
           onset                = EXCLUDED.onset,
           expires              = EXCLUDED.expires,
           polygon              = EXCLUDED.polygon,
           affected_ibge_codes  = EXCLUDED.affected_ibge_codes,
           risks                = EXCLUDED.risks,
           instructions         = EXCLUDED.instructions,
           dataset_id           = EXCLUDED.dataset_id,
           ingestion_run_id     = EXCLUDED.ingestion_run_id,
           updated_at           = now()
     WHERE target.expires             IS DISTINCT FROM EXCLUDED.expires
        OR target.severity            IS DISTINCT FROM EXCLUDED.severity
        OR target.description         IS DISTINCT FROM EXCLUDED.description
        OR target.event               IS DISTINCT FROM EXCLUDED.event
        OR target.color               IS DISTINCT FROM EXCLUDED.color
        OR target.onset               IS DISTINCT FROM EXCLUDED.onset
        OR target.affected_ibge_codes IS DISTINCT FROM EXCLUDED.affected_ibge_codes
        OR target.risks               IS DISTINCT FROM EXCLUDED.risks
        OR target.instructions        IS DISTINCT FROM EXCLUDED.instructions
        OR NOT ST_Equals(target.polygon, EXCLUDED.polygon)
    """
)

# Alertas que já saíram da fonte (revogados) não desaparecem sozinhos daqui —
# `GET /weather/alerts` já filtra por `expires > now()`, então uma linha
# vencida simplesmente para de aparecer sem precisar de DELETE. Vale para as
# duas fontes: o CEMADEN só nunca reconfirma `vigencia` (ver
# app/providers/cemaden/alerts.py) em vez de mandar uma data de fim explícita.


async def upsert_alerts(
    session: AsyncSession,
    records: list[WeatherAlertRecord],
    *,
    provider: str,
    dataset_id: int,
    ingestion_run_id: int,
) -> int:
    if not records:
        return 0
    result = await session.execute(
        _UPSERT_ALERTS_SQL,
        {
            "provider": provider,
            "external_ids": [r.external_id for r in records],
            "events": [r.event for r in records],
            "severities": [r.severity for r in records],
            "colors": [r.color for r in records],
            "descriptions": [r.description for r in records],
            "onsets": [r.onset for r in records],
            "expirations": [r.expires for r in records],
            "polygons": [orjson.dumps(r.polygon_geojson).decode() for r in records],
            "affected": [orjson.dumps(list(r.affected_ibge_codes)).decode() for r in records],
            "risks": [orjson.dumps(list(r.risks)).decode() for r in records],
            "instructions": [orjson.dumps(list(r.instructions)).decode() for r in records],
            "dataset_id": dataset_id,
            "ingestion_run_id": ingestion_run_id,
        },
    )
    await session.commit()
    return affected_rows(result)

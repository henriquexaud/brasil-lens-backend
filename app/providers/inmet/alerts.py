"""Avisos meteorológicos oficiais do INMET.

Contrato confirmado por chamada HTTP real a `GET /avisos/ativos` durante o
desenho deste provider — exemplo real recebido (`icone` truncado aqui, o
provider ignora esse campo):

```json
{
  "id_aviso": 28277, "id_sequencia": 2,
  "codigo": "urn:oid:2.49.0.0.76.0.2026.28277.2",
  "referencia": "info.aviso@inmet.gov.br,urn:oid:...28277.1,2026-09-16T00:00:00-03:00",
  "data_inicio": "2026-09-19T00:00:00.000Z", "data_fim": "2026-09-20T00:00:00.000Z",
  "estados": "Paraná,Santa Catarina,...", "descricao": "Tempestade",
  "aviso_cor": "#FFFE00", "severidade": "Perigo Potencial",
  "alterado": false, "encerrado": false,
  "riscos": ["Chuva entre 20 e 30 mm/h..."],
  "instrucoes": ["Em caso de rajadas de vento...", "..."]
}
```

Duas decisões que só ficaram claras com o exemplo real:

- **A chave estável é `id_aviso`, não `codigo`.** `codigo` muda a cada
  revisão do mesmo aviso (`...28277.1` → `...28277.2`, ligadas por
  `referencia`); `id_aviso` é o identificador que se mantém. Usar `codigo`
  como `external_id` criaria uma linha nova a cada revisão em vez de
  atualizar a existente — o oposto do upsert idempotente que o resto do
  projeto usa.
- **`municipios`/`estados`/`geocodes` são opcionais e mutuamente
  independentes.** Um aviso pode cobrir estados inteiros sem listar
  municípios (o exemplo acima não tem `municipios` nem `geocodes`). Só
  `geocodes` é usado aqui — é a única variante documentada como puramente
  numérica (código IBGE); `municipios` mistura nome e código em texto livre e
  não vale a pena parsear quando o polígono já é a representação espacial
  primária.
- **`poligono` vem duplamente serializado**: uma *string* contendo JSON, não
  um objeto GeoJSON aninhado — só ficou evidente rodando o job contra a fonte
  de verdade (`python -m app.jobs.import_weather_inmet_alerts`): a primeira
  execução real reportou 3 avisos ativos e 0 gravados, porque o código
  original checava `isinstance(poligono, dict)` e descartava os três em
  silêncio. Ver `_parse_polygon`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import orjson

from app.core.errors import ProviderError
from app.providers.base import get_json
from app.providers.records import WeatherAlertRecord

SOURCE = "inmet_avisos"
PROVIDER_KEY = "inmet"


async def fetch_active_alerts(client: httpx.AsyncClient) -> list[WeatherAlertRecord]:
    raw = await get_json(client, "/avisos/ativos", source=SOURCE)
    if not isinstance(raw, dict) or not isinstance(raw.get("hoje"), list):
        raise ProviderError(f"{SOURCE} devolveu formato inesperado para /avisos/ativos.")

    records: list[WeatherAlertRecord] = []
    for entry in raw["hoje"]:
        if not isinstance(entry, dict) or entry.get("encerrado"):
            continue
        record = _to_alert(entry)
        if record is not None:
            records.append(record)
    return records


def _to_alert(entry: dict[str, Any]) -> WeatherAlertRecord | None:
    external_id = entry.get("id_aviso")
    onset = _parse_iso(entry.get("data_inicio"))
    expires = _parse_iso(entry.get("data_fim"))
    polygon = _parse_polygon(entry.get("poligono"))
    if external_id is None or onset is None or expires is None or polygon is None:
        return None

    return WeatherAlertRecord(
        provider=PROVIDER_KEY,
        external_id=str(external_id),
        event=entry.get("descricao") or "Aviso meteorológico",
        severity=entry.get("severidade") or "Desconhecida",
        onset=onset,
        expires=expires,
        polygon_geojson=polygon,
        color=entry.get("aviso_cor"),
        affected_ibge_codes=_split_codes(entry.get("geocodes")),
        risks=tuple(entry.get("riscos") or ()),
        instructions=tuple(entry.get("instrucoes") or ()),
    )


def _parse_polygon(value: Any) -> dict[str, Any] | None:
    """`poligono` chega **duplamente serializado**: uma string JSON, não um
    objeto aninhado — descoberto rodando o job contra a fonte real (a
    verificação inicial desta sessão, feita por uma ferramenta de leitura de
    página, já havia mostrado o GeoJSON por dentro da string sem sinalizar
    que era string). Sem este parse extra, todo aviso é descartado em
    silêncio — foi exatamente o que aconteceu na primeira execução real deste
    job (3 avisos ativos, 0 gravados).
    """
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = orjson.loads(value)
    except orjson.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _split_codes(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, str) or not raw.strip():
        return ()
    return tuple(code.strip() for code in raw.split(",") if code.strip().isdigit())

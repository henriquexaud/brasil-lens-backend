import asyncio
import math
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from app.core.config import settings
from app.core.cooldown import SourceCooldown
from app.core.errors import ProviderError
from app.core.logging import get_logger

logger = get_logger(__name__)
deter_cooldown = SourceCooldown("deter", 60)

# O painel do INPE soma estas três classes como "desmatamento"; cicatriz de queimada, degradação
# e corte seletivo ficam de fora.
CLASSES = ("DESMATAMENTO_CR", "DESMATAMENTO_VEG", "MINERACAO")
# (bioma, workspace, camada). O DETER só cobre Amazônia e Cerrado.
BIOMES = (
    ("Amazônia", "deter-amz", "deter_amz"),
    ("Cerrado", "deter-cerrado-nb", "deter_cerrado"),
)
MAX_FEATURES = 20000
# O WFS do TerraBrasilis devolve 502 de forma intermitente (o do Cerrado, em cerca de metade das
# chamadas em 30/10/2026), então uma falha 5xx ou de rede é repetida antes de desistir do bioma.
ATTEMPTS = 3
RETRY_DELAY = 0.5


@dataclass(frozen=True, slots=True)
class Alert:
    biome: str
    view_date: date
    area_km2: float
    municipality: str
    state: str
    # Só o DETER Amazônia traz o código IBGE; o do Cerrado vem apenas com nome e UF.
    ibge_code: str | None
    longitude: float
    latitude: float


def normalize(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(plain.casefold().split())


def _positions(node: Any) -> list[tuple[float, float]]:
    if isinstance(node, list | tuple) and len(node) >= 2 and isinstance(node[0], int | float):
        return [(float(node[0]), float(node[1]))]
    return [point for child in node for point in _positions(child)]


def parse_feature(biome: str, raw: dict[str, Any]) -> Alert:
    props = raw["properties"]
    points = _positions(raw["geometry"]["coordinates"])
    longitudes = [lon for lon, _ in points]
    latitudes = [lat for _, lat in points]
    area = props.get("areamunkm")
    area = float(area) if area is not None and math.isfinite(float(area)) else 0.0
    code = props.get("mun_geocod")
    return Alert(
        biome=biome,
        view_date=date.fromisoformat(props["view_date"][:10]),
        area_km2=max(area, 0.0),
        municipality=str(props.get("municipality") or ""),
        state=str(props.get("uf") or ""),
        ibge_code=str(code) if code and len(str(code)) == 7 else None,
        longitude=round((min(longitudes) + max(longitudes)) / 2, 4),
        latitude=round((min(latitudes) + max(latitudes)) / 2, 4),
    )


async def _fetch_biome(
    client: httpx.AsyncClient, biome: str, workspace: str, layer: str, since: date
) -> list[Alert]:
    classes = ",".join(f"'{name}'" for name in CLASSES)
    response = await client.get(
        f"{settings.deter_wfs_url}/{workspace}/ows",
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": f"{workspace}:{layer}",
            "outputFormat": "application/json",
            "cql_filter": f"view_date >= '{since.isoformat()}' AND classname IN ({classes})",
            "count": MAX_FEATURES,
        },
    )
    response.raise_for_status()
    data = response.json()
    if data["type"] != "FeatureCollection" or not isinstance(data["features"], list):
        raise ValueError("Resposta GeoJSON inválida")
    if len(data["features"]) >= MAX_FEATURES:
        raise ValueError("Mais alertas do que o limite de leitura")
    return [parse_feature(biome, feature) for feature in data["features"]]


async def _fetch_biome_retrying(
    client: httpx.AsyncClient, biome: str, workspace: str, layer: str, since: date
) -> list[Alert]:
    for attempt in range(ATTEMPTS):
        try:
            return await _fetch_biome(client, biome, workspace, layer, since)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            retryable = (
                not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code >= 500
            )
            if not retryable or attempt == ATTEMPTS - 1:
                raise
            await asyncio.sleep(RETRY_DELAY)
    raise AssertionError("inalcançável")


async def fetch_alerts(since: date) -> tuple[list[Alert], list[str]]:
    """Os alertas dos biomas que responderam e a lista deles; só falha se nenhum responder."""
    if deter_cooldown.active:
        raise ProviderError(
            "O INPE não está respondendo. Nova tentativa automática em "
            f"{deter_cooldown.remaining_seconds()} s."
        )
    async with httpx.AsyncClient(timeout=settings.deter_http_timeout) as client:
        results = await asyncio.gather(
            *(_fetch_biome_retrying(client, *biome, since) for biome in BIOMES),
            return_exceptions=True,
        )
    alerts: list[Alert] = []
    answered: list[str] = []
    for (biome, _, _), result in zip(BIOMES, results, strict=True):
        if isinstance(result, list):
            alerts.extend(result)
            answered.append(biome)
        elif isinstance(
            result, httpx.HTTPError | ValueError | TypeError | KeyError | AttributeError
        ):
            logger.warning("deter.biome_failed", extra={"biome": biome, "error": repr(result)})
        else:
            raise result
    if not answered:
        deter_cooldown.trip()
        raise ProviderError("Não foi possível consultar os alertas do DETER no INPE.")
    return alerts, answered

"""Malha de transporte para o mapa (ADR-17).

As linhas de uma camada chegam fundidas numa só multilinha por área: o mapa
desenha um path por camada e o detalhe de um trecho vem do clique
(`identify`). O traçado nacional é pré-calculado na ingestão.
"""

from __future__ import annotations

from dataclasses import dataclass

import orjson
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTLCache
from app.core.errors import InvalidParameterError, TerritoryNotFoundError
from app.repositories import transport as repo
from app.schemas.common import to_json
from app.schemas.transport import (
    StreetsLayer,
    TransportCatalog,
    TransportDetail,
    TransportFeature,
    TransportFeatureProperties,
    TransportIdentify,
    TransportLayer,
    TransportLayerInfo,
    TransportNetwork,
    TransportNetworkMetadata,
    TransportSource,
    TransportSummary,
    TransportTotal,
)

LINE_LAYERS: tuple[TransportLayer, ...] = (
    "road_federal",
    "road_state",
    "road_local",
    "rail",
    "waterway",
)
LAYERS: tuple[TransportLayerInfo, ...] = (
    TransportLayerInfo(key="road_federal", label="Rodovias federais", source="dnit", min_zoom=3),
    # Estaduais (~34 mil trechos) e locais (~74 mil) não têm traçado nacional: as
    # estaduais de uma UF do Sudeste já somam ~125 KB comprimidos. O frontend
    # limita a área à UF aberta.
    TransportLayerInfo(key="road_state", label="Rodovias estaduais", source="ibge", min_zoom=6),
    TransportLayerInfo(key="road_local", label="Rodovias locais", source="ibge", min_zoom=9),
    TransportLayerInfo(key="rail", label="Ferrovias", source="ibge", min_zoom=3),
    TransportLayerInfo(key="waterway", label="Hidrovias", source="ibge", min_zoom=3),
    TransportLayerInfo(key="port", label="Portos", source="ibge", min_zoom=3),
)
_MIN_ZOOM = {layer.key: layer.min_zoom for layer in LAYERS}

# Ruas: WMS oficial do IBGE (faces de logradouro do Censo 2022), desenhado pelo
# navegador como o WMS do INPE (ADR-01); o estilo (SLD) fica no frontend.
STREETS = StreetsLayer(
    url="https://geoservicos.ibge.gov.br/geoserver/wms",
    layer="CGMAT:qg_2022_800_facelogradouro__v00prelim",
    min_zoom=11,
    description="IBGE — Faces de logradouro do Censo Demográfico 2022 (versão preliminar)",
)


@dataclass(frozen=True, slots=True)
class Detail:
    name: TransportDetail
    min_zoom: float
    tolerance: float
    digits: int
    max_span: float


# Do nacional ao de rua. A tolerância fica abaixo de um pixel em cada faixa de zoom.
DETAILS = (
    Detail("overview", 0, 0, 2, 360),
    Detail("regional", 6, 0.005, 3, 36),
    Detail("local", 8, 0.001, 4, 12),
    Detail("street", 10, 0.0002, 5, 4),
)

NETWORK_CACHE_SECONDS = 24 * 60 * 60
_cache: TTLCache[bytes] = TTLCache(ttl_seconds=NETWORK_CACHE_SECONDS, max_entries=256)
_version_cache: TTLCache[int] = TTLCache(ttl_seconds=60, max_entries=1)


def clear_cache() -> None:
    _cache.clear()
    _version_cache.clear()


def detail_for(zoom: float) -> Detail:
    return next(detail for detail in reversed(DETAILS) if zoom >= detail.min_zoom)


async def _version(session: AsyncSession) -> int:
    cached = _version_cache.get("version")
    if cached is None:
        cached = await repo.fetch_version(session)
        _version_cache.set("version", cached)
    return cached


async def get_network(
    session: AsyncSession,
    *,
    layer: TransportLayer,
    zoom: float,
    bbox: repo.BBox | None,
) -> bytes:
    if zoom < _MIN_ZOOM[layer]:
        raise InvalidParameterError("Aproxime o mapa para ver esta camada.", "zoom")
    detail = detail_for(zoom)
    if layer in LINE_LAYERS and detail.name != "overview":
        if bbox is None:
            raise InvalidParameterError("Informe a área a partir do zoom 6.", "bbox")
        if bbox[2] - bbox[0] > detail.max_span or bbox[3] - bbox[1] > detail.max_span:
            raise InvalidParameterError("Área grande demais para esta escala.", "bbox")
    else:
        bbox = None  # traçado nacional e portos não dependem da área
    version = await _version(session)
    key = (layer, detail.name if layer in LINE_LAYERS else "overview", bbox, version)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    features: list[TransportFeature] = []
    if layer not in LINE_LAYERS:
        features = [
            TransportFeature(
                id=str(row.id),
                properties=TransportFeatureProperties(
                    layer=layer, name=row.name, attributes=row.attributes or None
                ),
                geometry=orjson.loads(row.geometry),
            )
            for row in await repo.fetch_points(session, layer)
        ]
    else:
        geometry = (
            await repo.fetch_lines(session, layer, bbox, detail.tolerance, detail.digits)
            if bbox is not None
            else await repo.fetch_overview(session, layer, detail.digits)
        )
        if geometry is not None:
            features = [
                TransportFeature(
                    id=layer,
                    properties=TransportFeatureProperties(layer=layer),
                    geometry=orjson.loads(geometry),
                )
            ]
    body = to_json(
        TransportNetwork(
            metadata=TransportNetworkMetadata(
                layer=layer,
                detail=detail.name if layer in LINE_LAYERS else "overview",
                version=version,
            ),
            features=features,
        )
    )
    _cache.set(key, body)
    return body


async def identify(
    session: AsyncSession,
    *,
    layer: TransportLayer,
    longitude: float,
    latitude: float,
    tolerance: float,
) -> TransportIdentify:
    row = await repo.identify(session, layer, longitude, latitude, min(max(tolerance, 1e-4), 0.05))
    if row is None:
        return TransportIdentify(feature=None)
    return TransportIdentify(
        feature=TransportFeatureProperties(
            layer=row.layer, name=row.name, attributes=row.attributes or None
        )
    )


async def get_catalog(session: AsyncSession) -> TransportCatalog:
    version = await _version(session)
    rows = await repo.fetch_sources(session)
    details = await repo.fetch_run_details(session, version) if version else {}
    editions = {
        "dnit": f"SNV {details['snv_version']}" if details.get("snv_version") else None,
        "ibge": f"BC250 {details['bc250']}" if details.get("bc250") else None,
    }
    labels = {"dnit": "DNIT", "ibge": "IBGE"}
    return TransportCatalog(
        version=version,
        layers=list(LAYERS),
        sources=[
            TransportSource(
                key=key,
                label=labels[key],
                edition=editions[key],
                description=row.name,
                url=row.url,
            )
            for key, row in sorted(rows.items())
            if key in labels
        ],
        streets=STREETS,
    )


async def get_summary(session: AsyncSession, code: str) -> TransportSummary:
    territory = await repo.fetch_territory(session, code)
    if territory is None:
        raise TerritoryNotFoundError(code)
    imported = await _version(session) > 0
    totals = await repo.fetch_totals(session, territory.id)
    # A malha cobre o país inteiro: sem trecho no território é 0 medido, não ausência.
    return TransportSummary(
        code=territory.ibge_code,
        name=territory.name,
        totals=[
            TransportTotal(layer=layer.key, value=totals.get(layer.key, 0) if imported else None)
            for layer in LAYERS
        ],
    )

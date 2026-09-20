"""Rota de hidrografia: rios e massas d'água sob demanda com carregamento progressivo."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.config import settings
from app.schemas.hydrography import HydroFeatureCollection
from app.services import hydrography as hydrography_service

router = APIRouter(prefix="/hydrography", tags=["hydrography"])


@router.get(
    "",
    response_model=HydroFeatureCollection,
    summary="GeoJSON com cursos d'água e massas d'água oficiais da ANA/SNIRH",
    response_model_exclude_none=False,
)
async def get_hydrography(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    level: Annotated[
        str,
        Query(
            description=(
                "Nível territorial a desenhar: 'country' (grandes rios nacionais), "
                "'state' (rios estaduais/regionais da UF) ou 'municipality' (local)."
            )
        ),
    ] = "country",
    parent: Annotated[
        str | None,
        Query(description="Código IBGE do território pai (UF para estado, município para local)."),
    ] = None,
    include_water_bodies: Annotated[
        bool,
        Query(description="Incluir lagos, represas e corpos d'água poligonais."),
    ] = True,
) -> HydroFeatureCollection:
    collection = await hydrography_service.get_hydrography(
        session,
        level=level,
        parent_code=parent,
        include_water_bodies=include_water_bodies,
    )
    # Cache longo no cliente para evitar chamadas de rede repetidas durante navegação
    response.headers["Cache-Control"] = f"public, max-age={settings.http_cache_max_age}"
    return collection


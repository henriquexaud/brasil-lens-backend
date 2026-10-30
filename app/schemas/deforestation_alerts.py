from datetime import date, datetime

from app.schemas.common import CamelModel


class AlertMunicipality(CamelModel):
    ibge_code: str
    name: str
    state: str
    area_km2: float | None
    count: int
    alert_km2: float
    density: float | None
    latest_view_date: date | None


class AlertPoint(CamelModel):
    longitude: float
    latitude: float
    area_km2: float
    view_date: date
    municipality: str
    state: str
    biome: str


class DeforestationAlerts(CamelModel):
    window_start: date | None
    window_end: date | None
    days: int
    fetched_at: datetime
    total: int
    total_km2: float
    unassigned_count: int = 0
    country_density: float | None = None
    biomes: list[str]
    municipalities: list[AlertMunicipality]
    states: list[AlertMunicipality]
    ranked_municipalities: list[AlertMunicipality]
    points: list[AlertPoint]
    source: str = "INPE · DETER (TerraBrasilis)"
    source_url: str = "https://terrabrasilis.dpi.inpe.br/"
    area_source: str = "Área geodésica calculada sobre a malha territorial original do IBGE"
    notice: str = (
        "Alertas de fiscalização do DETER, só Amazônia e Cerrado; não são a taxa oficial "
        "de desmatamento (PRODES). O INPE publica com cerca de duas semanas de atraso."
    )

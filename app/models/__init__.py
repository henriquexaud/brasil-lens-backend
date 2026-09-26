"""Modelos SQLAlchemy. Importados em conjunto para que o metadata fique completo."""

from app.models.followed_municipality import FollowedMunicipality
from app.models.ingestion import Dataset, IngestionRun, IngestionStatus
from app.models.territory import (
    EXPECTED_PARENT_LEVEL,
    REQUIRES_PARENT,
    GeometryLOD,
    Territory,
    TerritoryGeometry,
    TerritoryLevel,
)
from app.models.weather import (
    WeatherAlert,
    WeatherObservation,
    WeatherProvider,
    WeatherStation,
    WeatherStationType,
)

__all__ = [
    "EXPECTED_PARENT_LEVEL",
    "REQUIRES_PARENT",
    "Dataset",
    "FollowedMunicipality",
    "GeometryLOD",
    "IngestionRun",
    "IngestionStatus",
    "Territory",
    "TerritoryGeometry",
    "TerritoryLevel",
    "WeatherAlert",
    "WeatherObservation",
    "WeatherProvider",
    "WeatherStation",
    "WeatherStationType",
]

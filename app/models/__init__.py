"""Modelos SQLAlchemy. Importados em conjunto para que o metadata fique completo."""

from app.models.context import DataContext
from app.models.followed_municipality import FollowedMunicipality
from app.models.indicator import Dataset, Indicator, IndicatorOrigin, IndicatorValue
from app.models.ingestion import IngestionRun, IngestionStatus
from app.models.saved_view import SavedView
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
    "DataContext",
    "Dataset",
    "FollowedMunicipality",
    "GeometryLOD",
    "Indicator",
    "IndicatorOrigin",
    "IndicatorValue",
    "IngestionRun",
    "IngestionStatus",
    "SavedView",
    "Territory",
    "TerritoryGeometry",
    "TerritoryLevel",
    "WeatherAlert",
    "WeatherObservation",
    "WeatherProvider",
    "WeatherStation",
    "WeatherStationType",
]

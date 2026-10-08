from __future__ import annotations

from typing import Any, Literal

from app.schemas.common import CamelModel

Office = Literal[
    "president", "governor", "senator", "federal_deputy", "state_deputy", "mayor", "councillor"
]
Metric = Literal[
    "leading_candidate",
    "leading_party",
    "leader_share",
    "margin",
    "turnout",
    "abstention",
    "blank_votes",
    "null_votes",
    "invalid_votes",
    "representation",
]


class PoliticalCatalog(CamelModel):
    releases: list[dict[str, Any]]


class PoliticalValue(CamelModel):
    ibge_code: str
    value: float | None
    label: str | None
    party: str | None
    tie: bool = False


class PoliticalValues(CamelModel):
    year: int
    office: Office
    round: int
    metric: Metric
    status: str
    updated_at: str | None
    note: str
    values: list[PoliticalValue]


class PoliticalDetail(CamelModel):
    ibge_code: str
    name: str
    level: str
    year: int
    office: Office
    round: int
    status: str
    updated_at: str | None
    summary: dict[str, Any] | None
    leaders: list[dict[str, Any]]
    representatives: list[dict[str, Any]]
    representative_total: int
    offset: int
    limit: int
    note: str

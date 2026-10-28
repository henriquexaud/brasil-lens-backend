from typing import Literal

from app.schemas.common import CamelModel

EducationNetwork = Literal["federal", "state", "municipal"]
# Filtro do mapa: universidades, institutos federais e CEFETs, faculdades e centros.
EducationKind = Literal["all", "university", "institute", "college"]
EducationCategory = Literal["federal", "state", "municipal"]
EducationOrganization = Literal[
    "university", "university_center", "college", "federal_institute", "cefet"
]


class EducationPlace(CamelModel):
    """Município, com a sigla da UF."""

    code: str
    name: str
    state: str | None


class EducationInstitutionSummary(CamelModel):
    """Instituição com oferta presencial no território, somada dentro dele."""

    code: int
    name: str
    acronym: str | None
    category: EducationCategory
    organization: EducationOrganization
    seat: EducationPlace | None
    enrollment: int
    courses: int
    municipalities: int


class EducationTerritory(CamelModel):
    code: str
    name: str
    year: int | None
    available_years: list[int]
    network: EducationNetwork
    kind: EducationKind
    enrollment: int | None
    courses: int | None
    institutions: list[EducationInstitutionSummary]


class EducationCampus(EducationPlace):
    enrollment: int
    courses: int
    entrants: int
    graduates: int


class EducationInstitutionDetail(CamelModel):
    code: int
    year: int
    name: str
    acronym: str | None
    category: EducationCategory
    organization: EducationOrganization
    seat: EducationPlace | None
    faculty: int | None
    staff: int | None
    enrollment: int
    distance_enrollment: int
    courses: int
    campuses: list[EducationCampus]

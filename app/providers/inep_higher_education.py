"""Censo da Educação Superior do INEP: instituições públicas e onde oferecem cursos.

Cada ano é um zip de microdados (~40 MB comprimido; o de 2024, gravado sem
compressão, tem ~460 MB) com dois CSV em Latin-1 separados por `;`: o cadastro
das instituições (IES) e o dos cursos. Os cursos presenciais trazem o código
IBGE do município onde são oferecidos, que é o que o mapa usa. Cursos a
distância contam só no total da instituição: o INEP não os atribui a um local
de oferta físico.
"""

from __future__ import annotations

import csv
import io
import ssl
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import certifi
import httpx

from app.core.errors import ProviderError

SOURCE = "INEP"
DATASET_URL = (
    "https://www.gov.br/inep/pt-br/acesso-a-informacao/dados-abertos/microdados/"
    "censo-da-educacao-superior"
)
DOWNLOAD_URL = (
    "https://download.inep.gov.br/microdados/microdados_censo_da_educacao_superior_{year}.zip"
)

# TP_CATEGORIA_ADMINISTRATIVA: 1, 2 e 3 são as redes públicas. A 7 ("Especial")
# reúne instituições criadas por lei estadual ou municipal que cobram mensalidade;
# privadas (4 e 5) e especiais ficam de fora.
CATEGORIES = {"1": "federal", "2": "state", "3": "municipal"}
ORGANIZATIONS = {
    "1": "university",
    "2": "university_center",
    "3": "college",
    "4": "federal_institute",
    "5": "cefet",
}
# O servidor do INEP não envia o certificado intermediário da RNP (o navegador o
# busca pelo AIA; o Python, não). A cadeia continua validada até a raiz
# GlobalSign R46 do certifi; o intermediário vale até 2030-11-19.
_INTERMEDIATE = Path(__file__).parent / "certs" / "rnp-icpedu-gr46-ov-tls-ca-2025.pem"
_PRESENTIAL = "1"
_UNDERGRADUATE = "1"

_INSTITUTION_COLUMNS = (
    "CO_IES",
    "NO_IES",
    "SG_IES",
    "TP_CATEGORIA_ADMINISTRATIVA",
    "TP_ORGANIZACAO_ACADEMICA",
    "CO_MUNICIPIO_IES",
    "QT_DOC_EXE",
    "QT_TEC_TOTAL",
)
_COURSE_COLUMNS = (
    "CO_IES",
    "CO_MUNICIPIO",
    "TP_CATEGORIA_ADMINISTRATIVA",
    "TP_MODALIDADE_ENSINO",
    "TP_NIVEL_ACADEMICO",
    "QT_CURSO",
    "QT_MAT",
    "QT_ING",
    "QT_CONC",
)


@dataclass(frozen=True, slots=True)
class Institution:
    code: int
    name: str
    acronym: str | None
    category: str
    organization: str
    seat_code: str | None
    faculty: int | None
    staff: int | None


@dataclass(slots=True)
class Offer:
    """Cursos presenciais de graduação de uma instituição num município."""

    courses: int = 0
    enrollment: int = 0
    entrants: int = 0
    graduates: int = 0


@dataclass(slots=True)
class Census:
    year: int
    institutions: dict[int, Institution] = field(default_factory=dict)
    offers: dict[tuple[int, str], Offer] = field(default_factory=dict)
    distance_enrollment: dict[int, int] = field(default_factory=dict)


def _int(value: str) -> int:
    return int(value) if value.strip() else 0


def _optional_int(value: str) -> int | None:
    return int(value) if value.strip() else None


def _rows(archive: zipfile.ZipFile, suffix: str, columns: tuple[str, ...]) -> Iterator[list[str]]:
    members = [name for name in archive.namelist() if name.upper().endswith(suffix)]
    if len(members) != 1:
        raise ProviderError(f"O zip do {SOURCE} deveria ter um único arquivo *{suffix}.")
    with archive.open(members[0]) as raw:
        reader = csv.reader(io.TextIOWrapper(raw, encoding="latin-1", newline=""), delimiter=";")
        header = next(reader, [])
        missing = [column for column in columns if column not in header]
        if missing:
            raise ProviderError(
                f"Formato inesperado em {suffix}: faltam as colunas {', '.join(missing)}."
            )
        indexes = [header.index(column) for column in columns]
        for row in reader:
            yield [row[index] for index in indexes]


def read_census(path: Path, year: int) -> Census:
    census = Census(year=year)
    try:
        with zipfile.ZipFile(path) as archive:
            for code, name, acronym, category, organization, seat, faculty, staff in _rows(
                archive, f"_IES_{year}.CSV", _INSTITUTION_COLUMNS
            ):
                if category not in CATEGORIES:
                    continue
                if organization not in ORGANIZATIONS:
                    raise ProviderError(f"Organização acadêmica desconhecida: {organization!r}.")
                census.institutions[int(code)] = Institution(
                    code=int(code),
                    name=name.strip(),
                    acronym=acronym.strip() or None,
                    category=CATEGORIES[category],
                    organization=ORGANIZATIONS[organization],
                    seat_code=seat.strip() or None,
                    faculty=_optional_int(faculty),
                    staff=_optional_int(staff),
                )
            for (
                code,
                municipality,
                category,
                modality,
                level,
                courses,
                enrollment,
                entrants,
                graduates,
            ) in _rows(archive, f"_CURSOS_{year}.CSV", _COURSE_COLUMNS):
                institution = int(code)
                if category not in CATEGORIES or institution not in census.institutions:
                    continue
                if modality != _PRESENTIAL:
                    # O curso a distância aparece uma vez por polo; a soma é o total da IES.
                    census.distance_enrollment[institution] = census.distance_enrollment.get(
                        institution, 0
                    ) + _int(enrollment)
                    continue
                if level != _UNDERGRADUATE or not municipality.strip():
                    continue
                offer = census.offers.setdefault((institution, municipality.strip()), Offer())
                offer.courses += _int(courses)
                offer.enrollment += _int(enrollment)
                offer.entrants += _int(entrants)
                offer.graduates += _int(graduates)
    except zipfile.BadZipFile as exc:
        raise ProviderError(f"O arquivo do {SOURCE} de {year} não é um zip válido.") from exc
    except (ValueError, IndexError) as exc:
        raise ProviderError(f"Linha inesperada nos microdados do {SOURCE} de {year}.") from exc
    if not census.institutions or not census.offers:
        raise ProviderError(
            f"Os microdados do {SOURCE} de {year} vieram sem instituições públicas."
        )
    return census


def client(timeout: float = 900) -> httpx.AsyncClient:
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cafile=_INTERMEDIATE)
    return httpx.AsyncClient(
        verify=context,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "brasil-lens/0.1 (ingestion)"},
    )


async def download(client: httpx.AsyncClient, year: int, directory: Path) -> Path:
    """Baixa o zip do ano em disco (streaming), reaproveitando um download anterior."""
    target = directory / f"microdados_censo_da_educacao_superior_{year}.zip"
    if target.is_file():
        return target
    partial = target.with_suffix(".part")
    url = DOWNLOAD_URL.format(year=year)
    try:
        async with client.stream("GET", url) as response:
            if response.status_code != httpx.codes.OK:
                raise ProviderError(
                    f"{SOURCE} respondeu HTTP {response.status_code} para {year}.",
                    status=response.status_code,
                )
            with partial.open("wb") as output:
                async for chunk in response.aiter_bytes(1 << 20):
                    output.write(chunk)
    except httpx.HTTPError as exc:
        raise ProviderError(f"Falha de rede ao baixar o {SOURCE} de {year}: {exc}") from exc
    partial.rename(target)
    return target

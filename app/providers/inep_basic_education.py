"""Censo Escolar do INEP: escolas públicas por município e etapa (ADR-23).

Cada ano é um zip de microdados com uma linha por escola (Latin-1, `;`). Até
2024 tudo vem num CSV só (`microdados_ed_basica_{ano}.csv`, ~420 colunas); a
partir de 2025 o INEP separou o cadastro (`Tabela_Escola`) das matrículas
(`Tabela_Matricula`), e só a segunda interessa aqui, porque já traz município,
rede e matrículas por etapa de cada escola.

Escola conta quando é pública (federal, estadual ou municipal) e tem matrícula
na educação básica no ano; numa etapa, quando tem matrícula nela. Isso dispensa
a situação de funcionamento (que a tabela de 2025 não traz): em 2019–2024
nenhuma escola paralisada ou extinta tinha matrícula. Privadas, inclusive as
conveniadas com o poder público, ficam de fora.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.core.errors import ProviderError
from app.providers.inep_higher_education import client, download_file

__all__ = ["STAGES", "Census", "Counts", "client", "download", "read_census"]

SOURCE = "INEP"
DATASET_URL = (
    "https://www.gov.br/inep/pt-br/acesso-a-informacao/dados-abertos/microdados/censo-escolar"
)
DOWNLOAD_URL = "https://download.inep.gov.br/dados_abertos/microdados_censo_escolar_{name}.zip"
# O INEP publicou o de 2025 com um sublinhado a mais no nome do arquivo.
_FILE_NAMES = {2025: "2025_"}

PUBLIC_NETWORKS = frozenset({"1", "2", "3"})
# Etapa → coluna de matrículas da escola. "all" é a educação básica inteira (com
# EJA e educação profissional), que decide se a escola conta.
STAGES = {
    "all": "QT_MAT_BAS",
    # Creche e pré-escola juntas: QT_MAT_INF é a soma das duas em 2019–2025.
    "early_childhood": "QT_MAT_INF",
    "elementary": "QT_MAT_FUND",
    "high_school": "QT_MAT_MED",
}
_COLUMNS = ("CO_MUNICIPIO", "TP_DEPENDENCIA", *STAGES.values())
_MEMBERS = ("MICRODADOS_ED_BASICA_", "TABELA_MATRICULA_")


def _zeros() -> dict[str, int]:
    return dict.fromkeys(STAGES, 0)


@dataclass(slots=True)
class Counts:
    """Escolas públicas de um município por etapa, com as matrículas delas."""

    schools: dict[str, int] = field(default_factory=_zeros)
    enrollment: dict[str, int] = field(default_factory=_zeros)


@dataclass(slots=True)
class Census:
    year: int
    municipalities: dict[str, Counts] = field(default_factory=dict)


def _int(value: str) -> int:
    return int(value) if value.strip() else 0


def _member(archive: zipfile.ZipFile) -> str:
    members = [
        name
        for name in archive.namelist()
        if name.upper().endswith(".CSV") and name.rsplit("/", 1)[-1].upper().startswith(_MEMBERS)
    ]
    if len(members) != 1:
        raise ProviderError(
            f"O zip do Censo Escolar deveria ter um único arquivo de escolas com matrículas; "
            f"tem {len(members)}."
        )
    return members[0]


def _rows(archive: zipfile.ZipFile) -> Iterator[list[str]]:
    with archive.open(_member(archive)) as raw:
        reader = csv.reader(io.TextIOWrapper(raw, encoding="latin-1", newline=""), delimiter=";")
        header = next(reader, [])
        missing = [column for column in _COLUMNS if column not in header]
        if missing:
            raise ProviderError(
                f"Formato inesperado no Censo Escolar: faltam as colunas {', '.join(missing)}."
            )
        indexes = [header.index(column) for column in _COLUMNS]
        for row in reader:
            yield [row[index] for index in indexes]


def read_census(path: Path, year: int) -> Census:
    census = Census(year=year)
    try:
        with zipfile.ZipFile(path) as archive:
            for municipality, network, *enrollment in _rows(archive):
                values = dict(zip(STAGES, map(_int, enrollment), strict=True))
                if network not in PUBLIC_NETWORKS or values["all"] <= 0:
                    continue
                counts = census.municipalities.setdefault(municipality.strip(), Counts())
                for stage, value in values.items():
                    if value > 0:
                        counts.schools[stage] += 1
                        counts.enrollment[stage] += value
    except zipfile.BadZipFile as exc:
        raise ProviderError(f"O arquivo do Censo Escolar de {year} não é um zip válido.") from exc
    except (ValueError, IndexError) as exc:
        raise ProviderError(f"Linha inesperada no Censo Escolar de {year}.") from exc
    if not census.municipalities:
        raise ProviderError(f"O Censo Escolar de {year} veio sem escolas públicas.")
    return census


async def download(client: httpx.AsyncClient, year: int, directory: Path) -> Path:
    url = DOWNLOAD_URL.format(name=_FILE_NAMES.get(year, str(year)))
    target = directory / f"microdados_censo_escolar_{year}.zip"
    return await download_file(client, url, target, year=year)

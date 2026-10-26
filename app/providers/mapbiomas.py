"""Cobertura florestal por município, da Coleção 11 do MapBiomas.

A tabela oficial "Biomas, Estados e Municípios — Cobertura 30m" vem num zip do
Google Drive com um xlsx de ~78 MB. O xlsx é lido em streaming com a
biblioteca padrão (zip + XML), sem carregar a planilha na memória: a aba
`COVERAGE_11` tem uma linha por município × bioma × classe e colunas `y1985`…
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import IO
from xml.etree.ElementTree import iterparse

import httpx

from app.core.errors import ProviderError

SOURCE = "MapBiomas"
DATASET_URL = "https://brasil.mapbiomas.org/estatisticas/"
# Link de download publicado na página de estatísticas da Coleção 11.
DOWNLOAD_URL = "https://drive.usercontent.google.com/download"
DRIVE_FILE_ID = "1otOqymHuixvkRGVl65zTTNyfaHo46Gqk"
COVERAGE_SHEET = "COVERAGE_11"
# Nível 1 "Floresta" do MapBiomas: formação florestal, savânica, mangue,
# floresta alagável e restinga arbórea.
FOREST_LEVEL_1 = "1. Forest"
# Série recortada para caber no Neon: a cada cinco anos até 2020 e anual depois.
FIRST_ANNUAL_YEAR = 2021

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


@dataclass(frozen=True, slots=True)
class ForestArea:
    ibge_code: str
    reference_year: int
    forest_ha: Decimal
    total_ha: Decimal


def selected_year(year: int) -> bool:
    return year >= FIRST_ANNUAL_YEAR or year % 5 == 0


def _column(reference: str) -> int:
    index = 0
    for char in reference:
        if not char.isalpha():
            break
        index = index * 26 + ord(char) - 64
    return index - 1


def _shared_strings(book: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in book.namelist():
        return []
    strings: list[str] = []
    with book.open("xl/sharedStrings.xml") as handle:
        for _, element in iterparse(handle):
            if element.tag == f"{_NS}si":
                strings.append("".join(text.text or "" for text in element.iter(f"{_NS}t")))
                element.clear()
    return strings


def _sheet_path(book: zipfile.ZipFile, name: str) -> str:
    workbook = book.read("xl/workbook.xml").decode("utf-8")
    relations = book.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    sheet = re.search(rf'<sheet [^>]*name="{re.escape(name)}"[^>]*r:id="([^"]+)"', workbook)
    if sheet is None:
        raise ProviderError(f"Aba {name} ausente na planilha do MapBiomas.")
    target = re.search(rf'Id="{re.escape(sheet.group(1))}"[^>]*Target="([^"]+)"', relations)
    if target is None:
        target = re.search(rf'Target="([^"]+)"[^>]*Id="{re.escape(sheet.group(1))}"', relations)
    if target is None:
        raise ProviderError(f"Aba {name} sem arquivo na planilha do MapBiomas.")
    path = target.group(1).lstrip("/")
    return path if path.startswith("xl/") else f"xl/{path}"


def _rows(handle: IO[bytes], strings: list[str]) -> Iterator[dict[int, str]]:
    for _, element in iterparse(handle):
        if element.tag != f"{_NS}row":
            continue
        row: dict[int, str] = {}
        for cell in element.iter(f"{_NS}c"):
            value = cell.find(f"{_NS}v")
            if value is None or value.text is None:
                inline = cell.find(f"{_NS}is")
                if inline is not None:
                    row[_column(cell.get("r", "A"))] = "".join(
                        text.text or "" for text in inline.iter(f"{_NS}t")
                    )
                continue
            row[_column(cell.get("r", "A"))] = (
                strings[int(value.text)] if cell.get("t") == "s" else value.text
            )
        element.clear()
        yield row


def read_forest_areas(xlsx: Path | IO[bytes]) -> list[ForestArea]:
    """Floresta e área total por município e ano (ha), somando os biomas do município."""
    forest: dict[tuple[str, int], Decimal] = {}
    total: dict[tuple[str, int], Decimal] = {}
    try:
        with zipfile.ZipFile(xlsx) as book:
            strings = _shared_strings(book)
            with book.open(_sheet_path(book, COVERAGE_SHEET)) as handle:
                rows = _rows(handle, strings)
                header = next(rows, None)
                if header is None:
                    raise ProviderError("Aba de cobertura do MapBiomas vazia.")
                columns = {name: index for index, name in header.items()}
                geocode = columns["geocode"]
                level_1 = columns["class_level_1"]
                years = {
                    index: int(name[1:])
                    for name, index in columns.items()
                    if re.fullmatch(r"y\d{4}", name) and selected_year(int(name[1:]))
                }
                for row in rows:
                    code = row.get(geocode, "").strip()
                    # O fim da aba traz linhas vazias e as lagoas sem município.
                    if len(code) != 7 or not code.isdigit():
                        continue
                    is_forest = row.get(level_1, "").strip() == FOREST_LEVEL_1
                    for index, year in years.items():
                        raw = row.get(index)
                        if not raw:
                            continue
                        area = Decimal(raw)
                        key = (code, year)
                        total[key] = total.get(key, Decimal(0)) + area
                        if is_forest:
                            forest[key] = forest.get(key, Decimal(0)) + area
    except (KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise ProviderError(f"Planilha do MapBiomas em formato inesperado: {exc}") from exc
    if not total:
        raise ProviderError("Planilha do MapBiomas sem dados municipais.")
    return [
        ForestArea(
            ibge_code=code,
            reference_year=year,
            forest_ha=forest.get((code, year), Decimal(0)),
            total_ha=area,
        )
        for (code, year), area in sorted(total.items())
        if area > 0
    ]


async def download_coverage(client: httpx.AsyncClient, directory: Path) -> Path:
    """Baixa o zip oficial em disco (streaming) e devolve o xlsx extraído.

    O Drive responde com uma página de aviso para arquivos grandes; `confirm=t`
    é o mesmo parâmetro que o botão "Baixar assim mesmo" envia.
    """
    archive = directory / "mapbiomas.zip"
    params = {"id": DRIVE_FILE_ID, "export": "download", "confirm": "t"}
    try:
        async with client.stream("GET", DOWNLOAD_URL, params=params) as response:
            if response.status_code != httpx.codes.OK:
                raise ProviderError(
                    f"{SOURCE} respondeu HTTP {response.status_code}.",
                    status=response.status_code,
                )
            with archive.open("wb") as output:
                async for chunk in response.aiter_bytes(1 << 20):
                    output.write(chunk)
    except httpx.HTTPError as exc:
        raise ProviderError(f"Falha de rede ao baixar {SOURCE}: {exc}") from exc
    return extract_xlsx(archive, directory)


def extract_xlsx(path: Path, directory: Path | None = None) -> Path:
    """Aceita o zip publicado ou o xlsx já extraído (ingestão por arquivo)."""
    if path.suffix.lower() == ".xlsx":
        return path
    try:
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".xlsx")]
            if len(members) != 1:
                raise ProviderError("O zip do MapBiomas deveria conter uma única planilha.")
            target = Path(directory or tempfile.mkdtemp(prefix="mapbiomas-"))
            return Path(archive.extract(members[0], target))
    except zipfile.BadZipFile as exc:
        raise ProviderError(
            "O download do MapBiomas não é um zip: o Drive pode ter devolvido uma página "
            "de aviso. Baixe o arquivo manualmente e use --mapbiomas-file."
        ) from exc

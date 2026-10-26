import io
import zipfile
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.core.errors import ProviderError
from app.jobs.import_environment import deforestation_totals, forest_cover
from app.models import TerritoryLevel
from app.providers import mapbiomas, prodes
from app.repositories import socioeconomic as repository
from app.services import socioeconomic as service


@pytest.fixture(autouse=True)
def isolated_cache():
    service.clear_cache()
    yield
    service.clear_cache()


def period(start, end, features):
    return {
        "startDate": {"year": start, "month": 8, "day": 1},
        "endDate": {"year": end, "month": 7, "day": 31},
        "features": features,
    }


def area(loi, gid, value):
    return {"loi": loi, "loiname": gid, "areas": [{"type": 1, "area": value}]}


NAMES = {
    "lois": [
        {"gid": 1, "name": "uf", "loinames": [{"gid": 10, "loiname": "Pará"}]},
        {
            "gid": 2,
            "name": "mun",
            "loinames": [
                {"gid": 20, "loiname": "A", "codibge": 1500107},
                {"gid": 21, "loiname": "B", "codibge": 1500206},
            ],
        },
    ]
}


def test_prodes_keeps_only_annual_increments_and_ignores_other_scopes():
    data = {
        "periods": [
            period(1500, 2007, [area(2, 20, 900.0)]),
            period(2006, 2008, [area(2, 20, 50.0)]),
            period(2023, 2024, [area(2, 20, 1.25), area(1, 10, 99.0)]),
        ]
    }
    biome = prodes.parse_biome("amazon", data, NAMES)
    assert biome.municipalities == {"1500107", "1500206"}
    assert list(biome.annual) == [2024]
    assert biome.annual[2024] == {"1500107": Decimal("1.25")}


def test_prodes_zero_is_measured_and_partial_years_are_dropped():
    amazon = prodes.BiomeIncrements(
        "amazon",
        frozenset({"1500107", "5100102"}),
        {2024: {"1500107": Decimal(2)}, 2025: {"5100102": Decimal(3)}, 2026: {}},
    )
    cerrado = prodes.BiomeIncrements(
        "cerrado",
        frozenset({"5100102"}),
        {2024: {"5100102": Decimal("0.5")}, 2025: {}},
    )
    records = {(r.ibge_code, r.reference_year): r.value for r in prodes.combine([amazon, cerrado])}
    # 2026 só saiu num bioma: a soma seria parcial, então o ano não entra.
    assert records == {
        ("1500107", 2024): Decimal(2),
        ("1500107", 2025): Decimal(0),
        ("5100102", 2024): Decimal("0.5"),
        ("5100102", 2025): Decimal(3),
    }


def test_prodes_rejects_unexpected_format():
    with pytest.raises(ProviderError):
        prodes.parse_biome("amazon", {"periods": []}, {"lois": []})


def test_aggregates_sum_municipalities_into_state_region_and_country():
    records = [
        prodes.DeforestationRecord("1500107", 2024, Decimal(2)),
        prodes.DeforestationRecord("1500206", 2024, Decimal(3)),
        prodes.DeforestationRecord("5100102", 2024, Decimal(1)),
    ]
    totals = deforestation_totals(records)
    assert totals[("15", 2024)] == Decimal(5)
    assert totals[("1", 2024)] == Decimal(5)
    assert totals[("BR", 2024)] == Decimal(6)


def test_forest_cover_is_weighted_by_area_not_averaged():
    areas = [
        mapbiomas.ForestArea("1500107", 2025, Decimal(90), Decimal(100)),
        mapbiomas.ForestArea("1500206", 2025, Decimal(0), Decimal(900)),
    ]
    shares = forest_cover(areas)
    assert shares[("1500107", 2025)] == Decimal("90.0000")
    assert shares[("1500206", 2025)] == Decimal("0.0000")
    assert shares[("15", 2025)] == Decimal("9.0000")


def xlsx(rows):
    strings: list[str] = []

    def cell(ref, value):
        if isinstance(value, str):
            strings.append(value)
            return f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>'
        return f'<c r="{ref}"><v>{value}</v></c>'

    letters = "ABCDEFGH"
    body = "".join(
        f'<row r="{line}">'
        + "".join(cell(f"{letters[i]}{line}", v) for i, v in enumerate(row) if v is not None)
        + "</row>"
        for line, row in enumerate(rows, start=1)
    )
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as book:
        book.writestr(
            "xl/workbook.xml",
            f'<workbook {ns} {rel}><sheets><sheet name="README" sheetId="1" r:id="rId1"/>'
            f'<sheet name="COVERAGE_11" sheetId="2" r:id="rId2"/></sheets></workbook>',
        )
        book.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>',
        )
        book.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData/></worksheet>")
        book.writestr(
            "xl/worksheets/sheet2.xml", f"<worksheet {ns}><sheetData>{body}</sheetData></worksheet>"
        )
        shared = "".join(f"<si><t>{text}</t></si>" for text in strings)
        book.writestr("xl/sharedStrings.xml", f"<sst {ns}>{shared}</sst>")
    buffer.seek(0)
    return buffer


def test_mapbiomas_sums_biomes_and_keeps_only_selected_years():
    book = xlsx(
        [
            ["geocode", "class_level_1", "y1985", "y1986", "y2025"],
            ["1500107", "1. Forest", 10, 11, 8],
            ["1500107", "3. Farming", 30, 29, 32],
            # Mesmo município em outro bioma: soma, não sobrescreve.
            ["1500107", "1. Forest", 5, 5, 4],
            # Linhas finais sem município são descartadas.
            [None, None, 1, 1, 1],
        ]
    )
    areas = {(a.ibge_code, a.reference_year): a for a in mapbiomas.read_forest_areas(book)}
    assert set(areas) == {("1500107", 1985), ("1500107", 2025)}
    assert areas[("1500107", 2025)].forest_ha == Decimal(12)
    assert areas[("1500107", 2025)].total_ha == Decimal(44)


def test_mapbiomas_without_coverage_sheet_is_a_provider_error():
    book = io.BytesIO()
    with zipfile.ZipFile(book, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook><sheets/></workbook>")
        archive.writestr("xl/_rels/workbook.xml.rels", "<Relationships/>")
    book.seek(0)
    with pytest.raises(ProviderError):
        mapbiomas.read_forest_areas(book)


async def test_catalog_is_split_by_context(monkeypatch):
    monkeypatch.setattr(repository, "version", AsyncMock(return_value="7"))
    rows = [
        {
            "key": "forest_cover",
            "name": "Cobertura florestal",
            "description": None,
            "unit": "%",
            "origin": "sourced",
            "decimal_places": 1,
            "available_years": [2020, 2025],
        }
    ]
    catalog = AsyncMock(return_value=rows)
    monkeypatch.setattr(repository, "catalog", catalog)
    result = await service.list_indicators(AsyncMock(), TerritoryLevel.MUNICIPALITY, "environment")
    catalog.assert_awaited_once()
    assert catalog.await_args.args[2] == "environment"
    assert result.indicators[0].latest_year == 2025
    assert TerritoryLevel.MUNICIPALITY in result.indicators[0].supported_levels
    # A outra chave de cache não reaproveita o catálogo do Meio ambiente.
    catalog.return_value = []
    other = await service.list_indicators(AsyncMock(), TerritoryLevel.MUNICIPALITY)
    assert other.indicators == []
    assert catalog.await_args.args[2] == "socioeconomic"

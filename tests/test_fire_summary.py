"""Densidade territorial, janelas, completude de páginas e ausência de área."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.core.config import settings
from app.core.errors import InvalidParameterError
from app.services import fire_summary as service
from app.services.viewport import parse_bbox

END = datetime(2026, 9, 20, tzinfo=UTC)


def row(identifier, code, age=1):
    return dict(
        id_foco_bdq=str(identifier),
        id_2=code,
        longitude="-50.13",
        latitude="-10.15",
        data_hora_gmt=(END - timedelta(hours=age)).isoformat(),
    )


def test_density_compares_area_not_absolute_counts_and_preserves_zero_and_missing_area():
    rows = [row(i, "1100001") for i in range(100)] + [
        row(100 + i, "1100002", 30) for i in range(20)
    ]
    areas = [
        dict(ibge_code=code, name=code, state="RO", area_km2=area)
        for code, area in [
            ("1100001", 10000),
            ("1100002", 100),
            ("1100003", None),
            ("1100004", 100),
        ]
    ]
    result = service.aggregate(rows, areas, END, 48)
    large, small, unknown, empty = result.municipalities
    assert large.count > small.count
    assert large.density == 10 and small.density == 200
    assert large.count_24h == 100 and small.count_24h == 0
    assert small.latest_detection_at == END - timedelta(hours=30)
    assert unknown.density is None and empty.density == 0
    assert empty.latest_detection_at is None
    assert result.total == 120
    assert result.unassigned_count == 0


def test_unknown_municipality_preserves_total_without_fake_density():
    result = service.aggregate([row(1, "")], [], END, 48)
    assert result.total == result.unassigned_count == 1
    with pytest.raises(ValueError, match="fora do período"):
        service.aggregate([row(2, "", 49)], [], END, 48)


@respx.mock
async def test_all_csv_pages_are_read_and_duplicate_or_incomplete_pages_fail(monkeypatch):
    monkeypatch.setattr(service, "PAGE_SIZE", 2)
    header = "id_foco_bdq,id_2,longitude,latitude,data_hora_gmt\n"
    route = respx.get(settings.inpe_queimadas_wfs_url).mock(
        side_effect=[
            httpx.Response(
                200,
                text=header
                + "1,11,-50,-10,2026-09-20T00:00:00\n2,11,-50,-10,2026-09-20T00:00:00\n",
            ),
            httpx.Response(200, text=header + "3,11,-50,-10,2026-09-20T00:00:00\n"),
        ]
    )
    rows = await service._fetch_rows("id_0=33", 3)
    assert len(rows) == 3
    assert [r.request.url.params["startIndex"] for r in route.calls] == ["0", "2"]
    route.mock(return_value=httpx.Response(200, text=header + "1,11,-50,-10,2026-09-20T00:00:00\n"))
    with pytest.raises(ValueError, match="Contagem"):
        await service._fetch_rows("id_0=33", 3)
    route.mock(return_value=httpx.Response(200, text=header))
    with pytest.raises(ValueError, match="incompleta"):
        await service._fetch_rows("id_0=33", 3)


@pytest.mark.parametrize("value", ["0,0,1", "0,0,NaN,2", "2,1,0,0", "-200,-20,0,20"])
def test_invalid_viewport_rejected(value):
    with pytest.raises(InvalidParameterError):
        parse_bbox(value)


def test_states_use_canonical_state_area_and_same_density_formula_as_municipalities():
    rows = [row(1, "1100001"), row(2, "1100002"), row(3, "1200001")]
    areas = [
        dict(ibge_code=code, name=code, state=uf, area_km2=area)
        for code, uf, area in [("11", "RO", 200), ("12", "AC", 100)]
    ]
    result = service.aggregate(rows, [], END, 48, areas)
    assert [s.count for s in result.states] == [2, 1]
    assert [s.density for s in result.states] == [10, 10]
    assert sum(s.count for s in result.states) == result.total

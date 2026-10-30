from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from app.core.config import settings
from app.core.errors import ProviderError
from app.models import TerritoryLevel
from app.providers import deter
from app.services import deforestation_alerts as service

NOW = datetime(2026, 10, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def reset_cooldown():
    deter.deter_cooldown.reset()
    yield
    deter.deter_cooldown.reset()


def alert(day, area, code="1500107", name="Cumaru do Norte", state="PA", biome="Amazônia"):
    return deter.Alert(
        biome=biome,
        view_date=date(2026, 10, day),
        area_km2=area,
        municipality=name,
        state=state,
        ibge_code=code,
        longitude=-50.1,
        latitude=-8.2,
    )


AREAS = [
    dict(ibge_code="1500107", name="Cumaru do Norte", state="PA", area_km2=10000),
    dict(ibge_code="1700251", name="Abreulândia", state="TO", area_km2=100),
    dict(ibge_code="1100001", name="Sem Alerta", state="RO", area_km2=None),
]
STATES = [
    dict(ibge_code="15", name="Pará", state="PA", area_km2=1_200_000),
    dict(ibge_code="17", name="Tocantins", state="TO", area_km2=277_000),
]


def test_density_favors_small_municipalities_and_window_ends_at_latest_alert():
    alerts = [
        alert(16, 5.0),
        alert(3, 5.0),
        alert(2, 99.0),  # fora dos 14 dias terminados em 16/10
        alert(10, 2.0, code=None, name="ABREULANDIA", state="TO", biome="Cerrado"),
    ]
    result = service.aggregate(alerts, AREAS, STATES, NOW)
    assert (result.window_start, result.window_end) == (date(2026, 10, 3), date(2026, 10, 16))
    assert result.total == 3 and result.total_km2 == 12.0
    large, small = result.municipalities
    assert large.alert_km2 == 10 and large.density == 1
    assert small.ibge_code == "1700251" and small.density == 20
    assert [m.ibge_code for m in result.ranked_municipalities] == ["1700251", "1500107"]
    assert {s.ibge_code for s in result.states} == {"15", "17"}
    assert result.states[0].alert_km2 == 10
    assert len(result.points) == 3


def test_municipality_without_valid_area_has_no_fake_density_and_unknown_name_is_counted():
    alerts = [alert(5, 1.0, code="1100001"), alert(5, 1.0, code=None, name="Desconhecida")]
    result = service.aggregate(alerts, AREAS, STATES, NOW)
    assert result.municipalities[0].density is None
    assert result.unassigned_count == 1


def test_empty_window_is_explicit_not_an_error():
    result = service.aggregate([], AREAS, STATES, NOW)
    assert result.total == 0 and result.window_end is None and result.points == []


def test_parse_feature_takes_bbox_center_and_validates_code():
    raw = {
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [[[[-50.0, -8.0], [-50.2, -8.0], [-50.2, -8.2], [-50.0, -8.2]]]],
        },
        "properties": {
            "view_date": "2026-10-16",
            "areamunkm": 0.08,
            "municipality": "Parintins",
            "uf": "AM",
            "mun_geocod": "1303403",
        },
    }
    parsed = deter.parse_feature("Amazônia", raw)
    assert (parsed.longitude, parsed.latitude) == (-50.1, -8.1)
    assert parsed.ibge_code == "1303403" and parsed.area_km2 == 0.08
    raw["properties"]["mun_geocod"] = "13"
    assert deter.parse_feature("Amazônia", raw).ibge_code is None


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def feature(day):
    return {
        "geometry": {"type": "MultiPolygon", "coordinates": [[[[-50.0, -8.0], [-50.1, -8.1]]]]},
        "properties": {"view_date": f"2026-10-{day}", "areamunkm": 0.1, "uf": "PA"},
    }


@respx.mock
async def test_fetch_alerts_filters_classes_and_queries_both_biomes():
    base = settings.deter_wfs_url
    amazon = respx.get(f"{base}/deter-amz/ows").mock(
        return_value=httpx.Response(200, json=collection(feature("16")))
    )
    respx.get(f"{base}/deter-cerrado-nb/ows").mock(
        return_value=httpx.Response(200, json=collection(feature("15")))
    )
    alerts = await deter.fetch_alerts(date(2026, 10, 1))
    assert sorted(item.biome for item in alerts) == ["Amazônia", "Cerrado"]
    cql = amazon.calls.last.request.url.params["cql_filter"]
    assert "view_date >= '2026-10-01'" in cql and "DESMATAMENTO_CR" in cql
    assert "CICATRIZ" not in cql


@respx.mock
async def test_fetch_alerts_failure_trips_cooldown():
    base = settings.deter_wfs_url
    respx.get(f"{base}/deter-amz/ows").mock(return_value=httpx.Response(500))
    respx.get(f"{base}/deter-cerrado-nb/ows").mock(
        return_value=httpx.Response(200, json=collection())
    )
    with pytest.raises(ProviderError):
        await deter.fetch_alerts(date(2026, 10, 1))
    with pytest.raises(ProviderError, match="Nova tentativa"):
        await deter.fetch_alerts(date(2026, 10, 1))


def test_map_values_classify_density_nationally_and_leave_other_territories_without_value():
    result = service.aggregate([alert(16, 5.0), alert(10, 2.0, code="1700251")], AREAS, STATES, NOW)
    cities = service.map_values(result, TerritoryLevel.MUNICIPALITY, None)
    assert {item.ibge_code: item.value for item in cities.values} == {"1500107": 0.5, "1700251": 20}
    assert cities.indicator.key == "deforestation_alerts" and cities.indicator.year == 2026
    assert cities.classification is not None and cities.statistics is not None
    scoped = service.map_values(result, TerritoryLevel.MUNICIPALITY, "15")
    assert [item.ibge_code for item in scoped.values] == ["1500107"]
    assert scoped.classification == cities.classification
    assert service.map_values(result, TerritoryLevel.REGION, None).values == []


def test_state_values_carry_the_country_density_outside_the_distribution():
    result = service.aggregate([alert(16, 5.0), alert(10, 2.0, code="1700251")], AREAS, STATES, NOW)
    assert result.country_density == round(7.0 * 1000 / 1_477_000, 3)
    states = service.map_values(result, TerritoryLevel.STATE, None)
    assert [item.ibge_code for item in states.values] == ["15", "17", "BR"]
    assert states.statistics is not None and states.statistics.count == 2
    assert service.map_values(result, TerritoryLevel.STATE, "15").values[-1].ibge_code != "BR"

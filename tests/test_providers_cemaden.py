"""Contrato real do WFS do CEMADEN e as decisões de normalização do provider.

Payload de exemplo copiado de uma chamada real a
`GET .../geoserver/cemaden_dev/ows?service=WFS&request=GetFeature&
typeName=cemaden_dev:alertas_vigentes_siaden` — ver docstring de
`app/providers/cemaden/alerts.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.core.config import settings
from app.core.errors import ProviderError
from app.providers.cemaden import alerts

_POLYGON = [[[-49.13, -26.83], [-49.12, -26.83], [-49.12, -26.82], [-49.13, -26.83]]]


_FETCHED_AT = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)


def _feature(**overrides: object) -> dict:
    properties = {
        "id_alerta": 35454,
        "datahoracriacao": "2026-09-20T08:07:29.832Z",
        "cidade": "BLUMENAU",
        "uf": "SC",
        "evento": "Movimentos de Massa - Moderado",
        "nivel": "Moderado",
        "status": 1,
        "path_pdf": "https://siaden.cemaden.gov.br/dados/resources/FilePDF/Alerta_SC_2732.pdf",
        "codibge": 4202404,
        **overrides,
    }
    return {
        "type": "Feature",
        "id": "alertas_vigentes_siaden.fid-1",
        "geometry": {"type": "MultiPolygon", "coordinates": [_POLYGON]},
        "properties": properties,
    }


def test_parses_real_contract() -> None:
    record = alerts._to_alert(_feature(), _FETCHED_AT)
    assert record is not None
    assert record.provider == "cemaden"
    assert record.external_id == "35454"
    # O sufixo " - Moderado" some: nivel já cobre isso em `severity`.
    assert record.event == "Movimentos de Massa"
    assert record.severity == "Moderado"
    assert record.color == "#FFFF00"
    assert record.description == "BLUMENAU/SC"
    assert record.affected_ibge_codes == ("4202404",)
    assert record.onset == datetime(2026, 9, 20, 8, 7, 29, 832000, tzinfo=UTC)
    # Sem data de expiração confiável na fonte (ver docstring do módulo):
    # expira a partir de quando ESTE fetch rodou, mais o buffer configurado.
    assert record.expires == _FETCHED_AT + timedelta(
        seconds=settings.cemaden_alert_validity_buffer_seconds
    )
    assert record.instructions == (
        "Boletim oficial do CEMADEN: "
        "https://siaden.cemaden.gov.br/dados/resources/FilePDF/Alerta_SC_2732.pdf",
    )
    assert record.risks == ()


def test_event_without_matching_level_suffix_is_kept_whole() -> None:
    # "evento" pode não trazer o sufixo esperado — nesse caso não cortamos
    # nada às cegas.
    record = alerts._to_alert(_feature(evento="Risco Hidrológico", nivel="Alto"), _FETCHED_AT)
    assert record is not None
    assert record.event == "Risco Hidrológico"
    assert record.color == "#FFA500"


def test_cessar_alert_is_discarded_even_if_status_field_disagrees() -> None:
    feature = _feature(status=0, nivel="Cessar", evento="Risco Hidrológico - Cessar")
    assert alerts._to_alert(feature, _FETCHED_AT) is None


@pytest.mark.parametrize(
    "override",
    [
        {"id_alerta": None},
        {"datahoracriacao": None},
        {"evento": None},
    ],
)
def test_missing_required_fields_are_discarded(override: dict) -> None:
    assert alerts._to_alert(_feature(**override), _FETCHED_AT) is None


def test_codibge_absent_yields_empty_affected_codes() -> None:
    record = alerts._to_alert(_feature(codibge=None), _FETCHED_AT)
    assert record is not None
    assert record.affected_ibge_codes == ()


def test_description_without_uf_falls_back_to_city_only() -> None:
    record = alerts._to_alert(_feature(uf=None), _FETCHED_AT)
    assert record is not None
    assert record.description == "BLUMENAU"


@respx.mock
async def test_fetch_active_alerts_filters_by_status_and_skips_malformed() -> None:
    route = respx.get(f"{settings.cemaden_alerts_base_url}/ows").mock(
        return_value=httpx.Response(
            200,
            json={"type": "FeatureCollection", "features": [_feature(), "not-a-feature"]},
        )
    )
    async with httpx.AsyncClient(base_url=settings.cemaden_alerts_base_url) as client:
        records = await alerts.fetch_active_alerts(client)

    assert len(records) == 1
    assert records[0].external_id == "35454"
    params = httpx.QueryParams(route.calls.last.request.url.query)
    assert params["cql_filter"] == "status=1"
    assert params["typeName"] == alerts.TYPE_NAME


@respx.mock
async def test_fetch_active_alerts_rejects_unexpected_shape() -> None:
    respx.get(f"{settings.cemaden_alerts_base_url}/ows").mock(
        return_value=httpx.Response(200, json={"type": "FeatureCollection"})
    )
    async with httpx.AsyncClient(base_url=settings.cemaden_alerts_base_url) as client:
        with pytest.raises(ProviderError):
            await alerts.fetch_active_alerts(client)

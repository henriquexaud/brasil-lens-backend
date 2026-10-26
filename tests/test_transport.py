from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import httpx
import orjson
import pytest
import respx
from sqlalchemy import text

from app.core.errors import InvalidParameterError, ProviderError
from app.providers import bc250, dnit
from app.providers.shapefile import read_dbf, read_shapes, wkb_multilinestring
from app.services import transport as service


def _shp(shape_type: int, records: list[bytes]) -> bytes:
    body = b"".join(
        struct.pack(">ii", index + 1, len(content) // 2) + content
        for index, content in enumerate(records)
    )
    header = struct.pack(">i", 9994) + b"\0" * 20 + struct.pack(">i", (100 + len(body)) // 2)
    header += struct.pack("<ii", 1000, shape_type) + b"\0" * 64
    return header + body


def _polyline(*parts: list[tuple[float, float]]) -> bytes:
    points = [point for part in parts for point in part]
    starts, total = [], 0
    for part in parts:
        starts.append(total)
        total += len(part)
    return (
        struct.pack("<i4d", 3, 0, 0, 0, 0)
        + struct.pack("<ii", len(parts), len(points))
        + struct.pack(f"<{len(parts)}i", *starts)
        + b"".join(struct.pack("<dd", *point) for point in points)
    )


def _point(x: float, y: float) -> bytes:
    return struct.pack("<idd", 1, x, y)


def _dbf(fields: list[str], rows: list[list[str]], deleted: frozenset[int] = frozenset()) -> bytes:
    size = 40
    header_length = 32 + 32 * len(fields) + 1
    record_length = 1 + size * len(fields)
    header = struct.pack("<BBBBIHH", 3, 126, 1, 1, len(rows), header_length, record_length)
    header += b"\0" * 20
    for name in fields:
        header += name.encode().ljust(11, b"\0") + b"C" + b"\0" * 4 + bytes([size]) + b"\0" * 15
    header += b"\r"
    body = b"".join(
        (b"*" if index in deleted else b" ")
        + b"".join(value.encode().ljust(size, b" ") for value in row)
        for index, row in enumerate(rows)
    )
    return header + body


def _decode_multiline(wkb: bytes) -> list[list[tuple[float, float]]]:
    _, kind, count = struct.unpack_from("<BII", wkb, 0)
    assert kind == 5
    offset, parts = 9, []
    for _ in range(count):
        _, line_kind, points = struct.unpack_from("<BII", wkb, offset)
        assert line_kind == 2
        offset += 9
        parts.append([struct.unpack_from("<dd", wkb, offset + 16 * i) for i in range(points)])
        offset += 16 * points
    return parts


def test_polyline_records_become_multilinestring_wkb_and_drop_degenerate_parts():
    shp = _shp(3, [_polyline([(-47.0, -23.0), (-46.5, -22.5)], [(-46.0, -22.0)]), b"\0" * 4])

    shapes = list(read_shapes(shp))

    assert _decode_multiline(shapes[0]) == [[(-47.0, -23.0), (-46.5, -22.5)]]
    assert shapes[1] is None  # forma nula continua na sequência, alinhada ao .dbf


def test_point_records_become_point_wkb():
    (shape,) = read_shapes(_shp(1, [_point(-48.5, -1.4)]))
    assert struct.unpack("<BIdd", shape) == (1, 1, -48.5, -1.4)


def test_unsupported_or_broken_shapefile_is_a_provider_error():
    with pytest.raises(ProviderError):
        list(read_shapes(b"not a shapefile" * 10))
    with pytest.raises(ProviderError):
        list(read_shapes(_shp(5, [])))


def test_dbf_rows_keep_alignment_and_blank_numeric_nulls():
    dbf = _dbf(
        ["NOME", "CALADO"], [["Hidrovia do Sul", "****"], ["x", "1"]], deleted=frozenset({1})
    )
    assert list(read_dbf(dbf)) == [{"nome": "Hidrovia do Sul", "calado": ""}, {}]


def _layer(rows: list[list[str]], fields: list[str]) -> tuple[bytes, bytes, str]:
    shapes = [_polyline([(-47.0 + i, -23.0), (-46.0 + i, -22.0)]) for i in range(len(rows))]
    return _shp(3, shapes), _dbf(fields, rows), "utf-8"


def test_bc250_roads_skip_federal_private_and_not_built_and_split_state_from_local():
    fields = ["jurisdicao", "situacaofi", "sigla", "revestimen", "administra"]
    layer = _layer(
        [
            ["Federal", "Construída", "BR-116", "Pavimentado", "Federal"],
            ["Propriedade particular", "Construída", "", "Pavimentado", ""],
            ["Estadual/Distrital", "Planejada", "SP-999", "", ""],
            ["Estadual/Distrital", "Construída", "SP-258", "Pavimentado", "Desconhecida"],
            ["Desconhecida", "Desconhecida", "", "Revestimento primário (solto)", "Municipal"],
        ],
        fields,
    )

    roads = list(bc250.roads(layer))

    assert [(road.layer, road.name) for road in roads] == [
        ("road_state", "SP-258"),
        ("road_local", None),
    ]
    assert roads[0].attributes == {"surface": "Pavimentado"}  # desconhecido não vira atributo
    assert roads[1].attributes == {"surface": "Revestimento primário (solto)"}


def test_bc250_rejects_shp_and_dbf_with_different_record_counts():
    shp, _, encoding = _layer([["Construída"]], ["situacaofi"])
    dbf = _dbf(["situacaofi"], [["Construída"], ["Construída"]])
    with pytest.raises(ProviderError):
        list(bc250.railways((shp, dbf, encoding)))


def test_bc250_reads_only_the_needed_layers_from_a_local_zip(tmp_path: Path):
    archive = tmp_path / "bc250.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for member in bc250.MEMBERS:
            shp, dbf, _ = _layer([["Construída"]], ["situacaofi"])
            handle.writestr(f"bc250/{member}.shp", shp)
            handle.writestr(f"bc250/{member}.dbf", dbf)
            handle.writestr(f"bc250/{member}.cpg", "UTF-8")
        handle.writestr("bc250/veg_vegetacao_a.shp", b"ignored")

    layers = bc250.download_layers(archive)

    assert set(layers) == set(bc250.MEMBERS)
    assert layers[bc250.RAILWAYS][2] == "UTF-8"


def _snv(surface: str, geometry: dict[str, object] | None) -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "Codigo_BR": "116",
            "Codigo_SNV": "116BSP0010",
            "Superficie_Federal": surface,
            "Administracao": "Concessão Federal",
            "Local_Inicio": "DIV RJ/SP",
            "Local_Fim": "ENTR SP-062",
        },
    }


def test_snv_keeps_existing_federal_roads_with_readable_surface():
    line = {"type": "LineString", "coordinates": [[-45.0, -22.5, 0], [-45.1, -22.6, 0]]}
    roads = list(
        dnit.roads(
            [
                _snv("DUP", line),
                _snv("PLA", line),
                _snv("TRV", line),
                _snv("PAV", None),
            ]
        )
    )

    assert len(roads) == 1
    assert roads[0].layer == "road_federal"
    assert roads[0].name == "BR-116"
    assert roads[0].attributes == {
        "snv": "116BSP0010",
        "surface": "Duplicada",
        "administration": "Concessão Federal",
        "stretch": "DIV RJ/SP – ENTR SP-062",
    }
    assert _decode_multiline(roads[0].geometry) == [[(-45.0, -22.5), (-45.1, -22.6)]]


def test_snv_with_unexpected_geometry_is_a_provider_error():
    with pytest.raises(ProviderError):
        list(dnit.roads([_snv("PAV", {"type": "Polygon", "coordinates": []})]))


@respx.mock
async def test_snv_pages_until_a_short_page(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dnit, "PAGE_SIZE", 2)
    route = respx.get(dnit.WFS_URL).mock(
        side_effect=[
            httpx.Response(200, json={"features": [{"id": 1}, {"id": 2}]}),
            httpx.Response(200, json={"features": [{"id": 3}]}),
        ]
    )
    async with httpx.AsyncClient() as client:
        features = await dnit.fetch_roads(client, "202610A")

    assert [feature["id"] for feature in features] == [1, 2, 3]
    assert route.calls[1].request.url.params["startIndex"] == "2"
    assert route.calls[0].request.url.params["cql_filter"] == "Versao_SNV='202610A'"


@respx.mock
async def test_snv_without_version_is_a_provider_error():
    respx.get(dnit.WFS_URL).mock(return_value=httpx.Response(200, json={"features": []}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(ProviderError):
            await dnit.latest_version(client)


def test_detail_follows_zoom():
    assert [service.detail_for(zoom).name for zoom in (4, 6, 8.5, 12)] == [
        "overview",
        "regional",
        "local",
        "street",
    ]


async def test_network_rejects_layers_below_their_zoom_and_oversized_areas():
    with pytest.raises(InvalidParameterError):
        await service.get_network(None, layer="road_local", zoom=8, bbox=(-47, -24, -46, -23))  # type: ignore[arg-type]
    with pytest.raises(InvalidParameterError):
        await service.get_network(None, layer="road_state", zoom=10.5, bbox=(-50, -25, -44, -20))  # type: ignore[arg-type]
    with pytest.raises(InvalidParameterError):
        await service.get_network(None, layer="rail", zoom=7, bbox=None)  # type: ignore[arg-type]


@pytest.fixture
async def network(session):
    service.clear_cache()
    await session.execute(text("DELETE FROM transport_features"))
    await session.execute(text("DELETE FROM transport_overviews"))
    rows = [
        ("rail", "Ferrovia Teste", {"gauge": "Larga"}, [[(-47.0, -23.0), (-46.0, -23.0)]]),
        ("rail", "Ramal", {}, [[(-40.0, -10.0), (-39.0, -10.0)]]),
    ]
    for layer, name, attributes, parts in rows:
        await session.execute(
            text(
                "INSERT INTO transport_features (layer, name, attributes, geom) "
                "VALUES (:layer, :name, CAST(:attributes AS jsonb), ST_GeomFromWKB(:geom, 4326))"
            ),
            {
                "layer": layer,
                "name": name,
                "attributes": orjson.dumps(attributes).decode(),
                "geom": wkb_multilinestring(parts),
            },
        )
    await session.execute(
        text(
            "INSERT INTO transport_features (layer, name, geom) "
            "VALUES ('port', 'Porto de Santos', ST_SetSRID(ST_MakePoint(-46.3, -23.95), 4326))"
        )
    )
    yield session
    service.clear_cache()


@pytest.mark.db
async def test_network_merges_the_area_into_one_clipped_line_feature(network):
    body = orjson.loads(
        await service.get_network(network, layer="rail", zoom=8.5, bbox=(-46.5, -24, -45, -22))
    )

    assert body["metadata"]["detail"] == "local"
    (feature,) = body["features"]
    assert feature["id"] == "rail"
    assert feature["geometry"]["type"] == "MultiLineString"
    # Só o pedaço dentro da área, sem o ramal distante.
    assert feature["geometry"]["coordinates"] == [[[-46.5, -23.0], [-46.0, -23.0]]]


@pytest.mark.db
async def test_network_serves_ports_as_points_with_names(network):
    body = orjson.loads(await service.get_network(network, layer="port", zoom=4, bbox=None))
    (port,) = body["features"]
    assert port["properties"]["name"] == "Porto de Santos"
    assert port["geometry"]["type"] == "Point"


@pytest.mark.db
async def test_identify_returns_the_nearest_segment_within_tolerance(network):
    found = await service.identify(
        network, layer="rail", longitude=-46.5, latitude=-23.004, tolerance=0.01
    )
    missed = await service.identify(
        network, layer="rail", longitude=-46.5, latitude=-23.5, tolerance=0.01
    )

    assert found.feature is not None
    assert found.feature.name == "Ferrovia Teste"
    assert found.feature.attributes == {"gauge": "Larga"}
    assert missed.feature is None

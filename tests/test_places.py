from __future__ import annotations

from types import SimpleNamespace

import httpx
import orjson
import pytest
import respx
from sqlalchemy import text

from app.core.errors import InvalidParameterError, NotFoundError, ProviderError
from app.core.text import title_case
from app.jobs import import_places
from app.providers import gbif, nature_places, ogc, osm_sports, resource_places
from app.services import places as service
from app.services import species

SQUARE = {
    "type": "Polygon",
    "coordinates": [
        [[-43.3, -23.0], [-43.2, -23.0], [-43.2, -22.9], [-43.3, -22.9], [-43.3, -23.0]]
    ],
}


@pytest.fixture(autouse=True)
def clear_caches():
    service.clear_cache()
    species.clear_cache()


def test_title_case_keeps_acronyms_and_lowercases_particles():
    assert title_case("APA DA ORLA MARÍTIMA") == "APA da Orla Marítima"
    assert title_case("ÁREA DE PROTEÇÃO DE GERICINÓ/MENDANHA") == (
        "Área de Proteção de Gericinó/Mendanha"
    )
    assert title_case("VALE S.A.") == "Vale S.A."


def test_conservation_unit_keeps_group_area_and_biomes():
    record = nature_places.conservation_unit(
        {
            "geometry": SQUARE,
            "properties": {
                "nome_uc": "PARQUE NACIONAL DA TIJUCA",
                "cd_cnuc": "0000.00.0195",
                "grupo": "Proteção Integral",
                "categoria": "Parque",
                "esfera": "Federal",
                "cria_ano": "06-07-1961",
                "ha_total": "3958.4",
                "matlantica": "3958.4",
                "cerrado": None,
                "situacao": "Ativo",
            },
        }
    )

    assert record is not None
    assert record.name == "Parque Nacional da Tijuca"
    assert record.category == "full_protection"
    assert record.summary == "Parque · Federal"
    assert ("Criação", "1961") in record.attributes
    assert ("Área", "40 km²") in record.attributes
    assert ("Biomas", "Mata Atlântica") in record.attributes
    assert record.weight == 3958.4


def test_inactive_or_shapeless_conservation_unit_is_left_out():
    base = {"nome_uc": "RESERVA X", "grupo": "Uso Sustentável", "situacao": "Ativo"}
    assert (
        nature_places.conservation_unit(
            {"geometry": SQUARE, "properties": {**base, "situacao": "Extinto"}}
        )
        is None
    )
    assert nature_places.conservation_unit({"geometry": None, "properties": base}) is None
    kept = nature_places.conservation_unit({"geometry": SQUARE, "properties": base})
    assert kept is not None and kept.category == "sustainable_use"


def test_cave_without_coordinates_is_not_placed_anywhere():
    assert nature_places.cave({"properties": {"nome": "Gruta", "latitude": None}}) is None
    record = nature_places.cave(
        {"properties": {"nome": "Gruta Azul", "latitude": "-19.5", "longitude": "-44.0"}}
    )
    assert record is not None
    assert (record.longitude, record.latitude) == (-44.0, -19.5)


def test_biome_outside_the_known_six_is_ignored():
    assert nature_places.biome({"geometry": SQUARE, "properties": {"bioma": "Savana"}}) is None
    record = nature_places.biome(
        {"geometry": SQUARE, "properties": {"bioma": "Mata Atlântica", "area_km": 10.5}}
    )
    assert record is not None and record.category == "atlantic_forest"


@pytest.mark.parametrize(
    ("substance", "group"),
    [
        ("TERRAS RARAS", "rare_earths"),
        ("Cério", "rare_earths"),
        ("MINÉRIO DE LÍTIO", "lithium"),
        ("ÁGUA MARINHA", "gems"),
        ("ÁGUA MINERAL", "water"),
        ("GRANITO P/ BRITA", "construction"),
        ("GRANITO", "stone"),
        ("MINÉRIO DE FERRO", "iron"),
        ("Gás natural", "energy"),
        ("ZINCO", "other"),
        (None, "other"),
    ],
)
def test_substances_fall_into_one_group(substance, group):
    assert resource_places.substance_group(substance) == group


def test_only_mining_phases_with_authorized_extraction_are_kept():
    def feature(phase: str) -> dict:
        return {
            "geometry": SQUARE,
            "properties": {
                "PROCESSO": "1193/1936",
                "FASE": phase,
                "NOME": "VALE S.A.",
                "SUBS": "MINÉRIO DE FERRO",
                "AREA_HA": 158.22,
                "ANO": 1936,
            },
        }

    assert resource_places.mining_title(feature("AUTORIZAÇÃO DE PESQUISA")) is None
    record = resource_places.mining_title(feature("CONCESSÃO DE LAVRA"))
    assert record is not None
    assert record.category == "iron"
    assert record.name == "Minério de ferro"
    assert record.summary == "Concessão de lavra · Vale S.A."
    assert record.weight == 158.22


def test_occurrence_without_known_status_is_left_out_and_mines_come_first():
    def feature(**properties: object) -> dict:
        return {
            "geometry": {"type": "Point", "coordinates": [-40.5, -20.8]},
            "properties": {"SUBSTANCIAS": "Terras raras, Titânio", **properties},
        }

    unknown = feature(STATUS_ECONOMICO="Indeterminado", IMPORTANCIA="Indeterminado")
    assert resource_places.mineral_occurrence(unknown) is None
    mine = resource_places.mineral_occurrence(
        feature(STATUS_ECONOMICO="Mina", SITUACAO_MINA="Inativo(a)", TOPONIMIA="Praia de Maimbá")
    )
    deposit = resource_places.mineral_occurrence(
        feature(STATUS_ECONOMICO="Não explotado", IMPORTANCIA="Depósito")
    )
    assert mine is not None and deposit is not None
    assert mine.category == "rare_earths"
    assert mine.summary == "Mina inativa · Praia de Maimbá"
    assert deposit.summary == "Depósito não explotado"
    assert mine.weight > deposit.weight


def test_oil_field_is_classified_by_its_main_fluid():
    def field(fluid: str | None) -> dict:
        return {
            "geometry": SQUARE,
            "properties": {"NOM_CAMPO": "TUPI", "FLUIDO_PRI": fluid, "NOM_BACIA": "Santos"},
        }

    oil = resource_places.oil_field(field("ÓLEO"))
    gas = resource_places.oil_field(field("GÁS NÃO ASSOCIADO"))
    assert oil is not None and gas is not None
    assert (oil.category, gas.category) == ("oil", "gas")
    assert oil.name == "Tupi"
    assert resource_places.oil_field({"geometry": SQUARE, "properties": {}}) is None


@pytest.mark.parametrize(
    ("tags", "category"),
    [
        ({"leisure": "pitch", "sport": "basketball"}, "basketball"),
        ({"leisure": "pitch", "sport": "soccer;basketball;volleyball"}, "multi"),
        ({"leisure": "pitch", "sport": "futsal"}, "soccer"),
        # Sem o esporte, a etiqueta não diz o que se pratica ali.
        ({"leisure": "pitch"}, None),
        ({"leisure": "pitch", "sport": "basketball", "access": "private"}, None),
        ({"leisure": "skatepark"}, "skate"),
        ({"leisure": "fitness_station"}, "fitness"),
        # Piscina sem acesso declarado é quase sempre particular.
        ({"leisure": "swimming_pool"}, None),
        ({"leisure": "swimming_pool", "access": "yes"}, "swimming"),
        ({"leisure": "sports_centre", "sport": "swimming"}, "swimming"),
        ({"leisure": "sports_centre", "sport": "tennis;swimming"}, "centre"),
        ({"leisure": "stadium"}, "stadium"),
        ({"barrier": "gate"}, None),
    ],
)
def test_osm_tags_decide_the_sport_category(tags, category):
    assert osm_sports.category(tags) == category


def test_multi_sport_court_also_answers_for_each_sport_it_hosts():
    record = osm_sports.place(
        {
            "id": "w1",
            "geometry": SQUARE,
            "properties": {
                "leisure": "pitch",
                "sport": "soccer;basketball",
                "surface": "concrete",
                "name": "Quadra da Praça",
                "lit": "yes",
            },
        }
    )

    assert record is not None
    assert record.category == "multi"
    assert record.extra_categories == ("basketball", "soccer")
    assert record.summary == "Futebol, Basquete · Concreto"
    assert ("Iluminação", "Sim") in record.attributes
    assert (record.longitude, record.latitude) == (-43.25, -22.95)


def test_osm_export_is_read_line_by_line_with_or_without_record_separator():
    court = orjson.dumps(
        {
            "id": "n1",
            "geometry": {"type": "Point", "coordinates": [-46.6, -23.5]},
            "properties": {"leisure": "pitch", "sport": "basketball"},
        }
    )
    gate = orjson.dumps(
        {"id": "n2", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}}
    )

    records = list(osm_sports.read([b"\x1e" + court + b"\n", b"\n", gate + b"\n"]))

    assert [record.external_id for record in records] == ["n1"]
    with pytest.raises(ProviderError):
        list(osm_sports.read([b"{not json"]))


@respx.mock
async def test_wfs_pages_until_a_short_page():
    route = respx.get("https://geo.test/ows").mock(
        side_effect=[
            httpx.Response(200, json={"features": [{"id": 1}, {"id": 2}]}),
            httpx.Response(200, json={"features": [{"id": 3}]}),
        ]
    )
    async with httpx.AsyncClient() as client:
        features = [
            feature
            async for feature in ogc.wfs_features(
                client,
                "https://geo.test/ows",
                "ws:layer",
                source="Teste",
                sort_by="id",
                page_size=2,
            )
        ]

    assert [feature["id"] for feature in features] == [1, 2, 3]
    assert route.calls[1].request.url.params["startIndex"] == "2"


@respx.mock
async def test_arcgis_advances_by_object_id_and_rejects_unexpected_payload():
    route = respx.get("https://arc.test/query").mock(
        side_effect=[
            httpx.Response(200, json={"features": [{"properties": {"OBJECTID": 7}}]}),
            httpx.Response(200, json={"features": []}),
            httpx.Response(200, json={"error": {"code": 400, "message": "Pagination"}}),
        ]
    )
    async with httpx.AsyncClient() as client:
        features = [
            feature
            async for feature in ogc.arcgis_features(
                client, "https://arc.test/query", source="Teste", out_fields="NOME"
            )
        ]
        assert len(features) == 1
        assert "OBJECTID > 7" in route.calls[1].request.url.params["where"]
        with pytest.raises(ProviderError):
            async for _ in ogc.arcgis_features(
                client, "https://arc.test/query", source="Teste", out_fields="NOME"
            ):
                pass


def test_row_carries_extra_categories_and_ordered_attributes():
    source = import_places.SOURCE_BY_LAYER["sport"]
    record = osm_sports.place(
        {
            "id": "w1",
            "geometry": SQUARE,
            "properties": {"leisure": "pitch", "sport": "soccer;basketball", "surface": "sand"},
        }
    )
    assert record is not None

    row = import_places._row(record, source, 5)

    assert row["context"] == "sports"
    assert row["extra_categories"] == ["basketball", "soccer"]
    assert orjson.loads(row["attributes"]) == [
        {"label": "Esportes", "value": "Futebol, Basquete"},
        {"label": "Piso", "value": "Areia"},
    ]
    # Sem contorno guardado para pontos.
    assert row["tolerance"] is None and row["geometry"] is None


def test_every_layer_has_a_source_and_labels():
    assert {layer.key for layer in service.LAYERS} == set(import_places.SOURCE_BY_LAYER)
    for layer in service.LAYERS:
        assert import_places.SOURCE_BY_LAYER[layer.key].context == layer.context
        # Camada de áreas guarda o contorno; a de pontos tem o zoom de entrada.
        has_shape = import_places.SOURCE_BY_LAYER[layer.key].shape_tolerance is not None
        assert has_shape == layer.has_shapes
        assert (layer.point_zoom is None) == layer.has_shapes


def test_detail_follows_the_zoom_bands():
    assert [service.detail_for(zoom).name for zoom in (4, 6, 8.5, 12)] == [
        "overview",
        "regional",
        "local",
        "street",
    ]


async def test_layer_kind_category_and_area_are_validated_before_the_database():
    session = SimpleNamespace()
    with pytest.raises(InvalidParameterError):
        await service.get_values(session, layer="biome", category=None, level="state", parent=None)
    with pytest.raises(InvalidParameterError):
        await service.get_values(
            session, layer="sport", category="chess", level="state", parent=None
        )
    with pytest.raises(InvalidParameterError):
        await service.get_values(
            session, layer="sport", category=None, level="municipality", parent="3550308"
        )
    with pytest.raises(InvalidParameterError):
        await service.get_points(session, layer="biome", category=None, bbox=(-47, -24, -46, -23))
    with pytest.raises(InvalidParameterError):
        await service.get_points(session, layer="sport", category=None, bbox=(-74, -34, -30, 5))
    with pytest.raises(InvalidParameterError):
        await service.get_areas(session, layer="sport", category=None, zoom=4, bbox=None)
    with pytest.raises(InvalidParameterError):
        await service.get_areas(
            session, layer="conservation_unit", category=None, zoom=8, bbox=None
        )


def _gbif(respx_mock, *, units, facets, count=100):
    respx_mock.get("https://api.gbif.org/v1/geocode/reverse").mock(
        return_value=httpx.Response(200, json=units)
    )
    respx_mock.get("https://api.gbif.org/v1/occurrence/search").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json={
                "count": 0 if "iucnRedListCategory" in request.url.params else count,
                "facets": [
                    {
                        "counts": []
                        if "iucnRedListCategory" in request.url.params
                        else [{"name": str(key), "count": total} for key, total in facets]
                    }
                ],
            },
        )
    )
    respx_mock.get(path__regex=r".*/species/\d+$").mock(
        return_value=httpx.Response(
            200, json={"canonicalName": "Callithrix penicillata", "family": "Callitrichidae"}
        )
    )
    respx_mock.get(path__regex=r".*/vernacularNames$").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"vernacularName": "Black-tufted marmoset", "language": "eng"},
                    {"vernacularName": "sagui", "language": "por"},
                    {"vernacularName": "mico-estrela", "language": "por", "country": "BR"},
                ]
            },
        )
    )


def _territory(monkeypatch, name="Belo Horizonte", level="municipality"):
    async def fetch_territory(_session, code):
        return SimpleNamespace(id=1, ibge_code=code, name=name, level=level)

    async def fetch_reference_point(_session, _territory_id):
        return SimpleNamespace(latitude=-19.9, longitude=-43.95)

    monkeypatch.setattr(species.repo, "fetch_territory", fetch_territory)
    monkeypatch.setattr(species.repo, "fetch_reference_point", fetch_reference_point)


BH_UNITS = [
    {"id": "BRA.13_1", "type": "GADM1", "title": "Minas Gerais"},
    {"id": "BRA.13.66_2", "type": "GADM2", "title": "Belo Horizonte"},
    # O geocódigo também devolve os vizinhos do ponto.
    {"id": "BRA.13.659_2", "type": "GADM2", "title": "Sabará"},
]


async def test_species_use_the_unit_with_the_territory_name_not_a_neighbour(
    monkeypatch, respx_mock
):
    _territory(monkeypatch)
    _gbif(respx_mock, units=BH_UNITS, facets=[(1, 253), (2, 80)], count=849)

    summary = await species.get_species(None, "3106200", "mammals")

    search = next(
        call.request for call in respx_mock.calls if call.request.url.path.endswith("/search")
    )
    assert search.url.params["gadmGid"] == "BRA.13.66_2"
    assert search.url.params["taxonKey"] == "359"
    assert (summary.records, summary.species_count) == (849, 2)
    first = summary.species[0]
    assert (first.common_name, first.scientific_name, first.records) == (
        "Mico-estrela",
        "Callithrix penicillata",
        253,
    )
    assert first.threat is None


async def test_species_are_not_shown_when_gbif_has_no_matching_unit(monkeypatch, respx_mock):
    _territory(monkeypatch, name="Mojuí dos Campos")
    _gbif(respx_mock, units=BH_UNITS, facets=[])

    with pytest.raises(NotFoundError):
        await species.get_species(None, "1504752", "birds")
    assert not any(call.request.url.path.endswith("/search") for call in respx_mock.calls)


async def test_species_count_is_unknown_when_the_enumeration_hits_the_limit(
    monkeypatch, respx_mock
):
    _territory(monkeypatch, name="Brasil", level="country")
    _gbif(respx_mock, units=[], facets=[(key, 10) for key in range(1, species.TOP_SPECIES + 1)])

    summary = await species.get_species(None, "BR", "plants")

    assert summary.species_count is None
    assert len(summary.species) == species.TOP_SPECIES


async def test_gbif_failure_pauses_the_source(monkeypatch, respx_mock):
    _territory(monkeypatch)
    respx_mock.get("https://api.gbif.org/v1/geocode/reverse").mock(return_value=httpx.Response(503))

    with pytest.raises(ProviderError):
        await species.get_species(None, "3106200", "birds")
    assert gbif.gbif_cooldown.active
    with pytest.raises(ProviderError) as paused:
        await species.get_species(None, "3106200", "birds")
    assert paused.value.code == "provider_rate_limited"
    assert len(respx_mock.calls) == 1


_PLACE = text(
    """
    INSERT INTO places (context, layer, category, extra_categories, name, summary, attributes,
                        weight, municipality_code, point, shape)
    VALUES (:context, :layer, :category, CAST(:extras AS varchar(32)[]), :name, :summary,
            CAST(:attributes AS jsonb), :weight, :code,
            ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
            CASE WHEN CAST(:shape AS text) IS NULL THEN NULL
                 ELSE ST_SetSRID(ST_GeomFromGeoJSON(CAST(:shape AS text)), 4326) END)
    """
)


@pytest.fixture
async def stocked(session):
    """Lugares de teste no lugar dos ingeridos; o rollback da sessão desfaz tudo."""
    await session.execute(text("DELETE FROM places"))
    rows = [
        ("sports", "sport", "basketball", None, "Quadra A", "3550308", -46.63, -23.55, None, None),
        (
            "sports",
            "sport",
            "multi",
            ["basketball", "soccer"],
            None,
            "3550308",
            -46.62,
            -23.54,
            None,
            None,
        ),
        ("sports", "sport", "soccer", None, "Campo B", "3304557", -43.2, -22.9, None, None),
        (
            "nature",
            "conservation_unit",
            "full_protection",
            None,
            "Parque de Teste",
            None,
            -43.25,
            -22.95,
            orjson.dumps(SQUARE).decode(),
            3958.4,
        ),
    ]
    for context, layer, category, extras, name, code, lon, lat, shape, weight in rows:
        await session.execute(
            _PLACE,
            {
                "context": context,
                "layer": layer,
                "category": category,
                "extras": extras,
                "name": name,
                "summary": None,
                "attributes": '[{"label": "Piso", "value": "Concreto"}]',
                "weight": weight,
                "code": code,
                "lon": lon,
                "lat": lat,
                "shape": shape,
            },
        )
    return session


@pytest.mark.db
async def test_values_count_places_by_state_and_by_municipality(stocked):
    states = orjson.loads(
        await service.get_values(stocked, layer="sport", category=None, level="state", parent=None)
    )
    city = orjson.loads(
        await service.get_values(
            stocked, layer="sport", category="basketball", level="municipality", parent="35"
        )
    )

    assert states["values"] == {"35": 2, "33": 1}
    assert states["total"] == 3
    # A poliesportiva que recebe basquete entra no filtro de basquete.
    assert city["values"] == {"3550308": 2}


@pytest.mark.db
async def test_points_come_from_the_area_and_respect_the_category(stocked):
    area = (-47.0, -24.0, -46.0, -23.0)
    every = orjson.loads(await service.get_points(stocked, layer="sport", category=None, bbox=area))
    soccer = orjson.loads(
        await service.get_points(stocked, layer="sport", category="soccer", bbox=area)
    )

    assert {point["name"] for point in every["points"]} == {"Quadra A", None}
    assert every["truncated"] is False
    assert [point["category"] for point in soccer["points"]] == ["multi"]


@pytest.mark.db
async def test_areas_are_served_whole_from_afar_and_clipped_up_close(stocked):
    national = orjson.loads(
        await service.get_areas(
            stocked,
            layer="conservation_unit",
            category=None,
            zoom=10,
            bbox=(-44, -23.5, -43, -22.5),
        )
    )
    clipped = orjson.loads(
        await service.get_areas(
            stocked,
            layer="conservation_unit",
            category=None,
            zoom=10,
            bbox=(-43.25, -23.5, -43.0, -22.5),
        )
    )
    elsewhere = orjson.loads(
        await service.get_areas(
            stocked, layer="conservation_unit", category=None, zoom=10, bbox=(-50, -10, -49, -9)
        )
    )

    (feature,) = national["features"]
    assert feature["properties"] == {"name": "Parque de Teste", "category": "full_protection"}
    longitudes = [point[0] for point in clipped["features"][0]["geometry"]["coordinates"][0]]
    assert min(longitudes) == -43.25
    assert elsewhere["features"] == []


@pytest.mark.db
@pytest.mark.ingested
async def test_summary_lists_the_places_of_a_territory(stocked):
    city = orjson.loads(
        await service.get_summary(stocked, code="3550308", layer="sport", category="basketball")
    )
    park = orjson.loads(
        await service.get_summary(stocked, code="3304557", layer="conservation_unit", category=None)
    )
    far = orjson.loads(
        await service.get_summary(stocked, code="3550308", layer="conservation_unit", category=None)
    )

    # Sem ingestão registrada da camada na transação de teste, o total depende do
    # banco: o que se confere aqui é a lista e as categorias do recorte.
    assert [item["name"] for item in city["items"]] == ["Quadra A", None]
    assert {item["key"]: item["count"] for item in city["categories"]} == {
        "soccer": 1,
        "basketball": 2,
        "multi": 1,
    }
    # A categoria mais numerosa vem primeiro.
    assert city["categories"][0]["key"] == "basketball"
    assert [item["name"] for item in park["items"]] == ["Parque de Teste"]
    assert far["items"] == []


def test_categories_come_by_count_with_other_last():
    listed = service._categories("mining_title", {"gold": 2, "other": 9, "iron": 5, "tin": 0})

    assert [(item.key, item.count) for item in listed] == [("iron", 5), ("gold", 2), ("other", 9)]


@pytest.mark.db
async def test_place_detail_keeps_the_attribute_order_and_unknown_place_is_404(stocked):
    place_id = (
        await stocked.execute(text("SELECT id FROM places WHERE name = 'Quadra A'"))
    ).scalar_one()

    detail = await service.get_place(stocked, place_id)

    assert detail.category_label == "Basquete"
    assert detail.layer_label == "Locais de esporte"
    assert [(item.label, item.value) for item in detail.attributes] == [("Piso", "Concreto")]
    with pytest.raises(NotFoundError):
        await service.get_place(stocked, -1)

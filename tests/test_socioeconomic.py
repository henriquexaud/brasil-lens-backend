from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.api.deps import get_session
from app.core.errors import InvalidParameterError
from app.jobs._runner import upsert_dataset
from app.jobs.import_indicators import Options, import_spec, persist
from app.main import app
from app.models import Indicator, IndicatorValue, Territory, TerritoryLevel
from app.providers.ibge.datasets import DERIVED_INDICATORS, SOURCED_INDICATORS
from app.providers.ibge.indicator_records import IndicatorObservation
from app.repositories import socioeconomic as repository
from app.schemas.socioeconomic import IndicatorListResponse, IndicatorOut
from app.schemas.territory import TerritoryDetail
from app.services import socioeconomic as service
from app.services.derived import derive


@pytest.fixture(autouse=True)
def isolated_cache():
    service.clear_cache()
    yield
    service.clear_cache()


def catalog(years=(2022, 2024), version="1"):
    return IndicatorListResponse(
        version=version,
        indicators=[
            IndicatorOut(
                key="population",
                name="População",
                description=None,
                unit="people",
                origin="sourced",
                decimal_places=0,
                available_years=list(years),
                latest_year=max(years),
                supported_levels=list(TerritoryLevel),
            )
        ],
    )


@pytest.mark.parametrize("year", ["abc", "1899", "2101", "2022.0", "２０２２"])
def test_invalid_year_is_rejected(year):
    with pytest.raises(InvalidParameterError):
        service.parse_year(year)


def test_coverage_does_not_fabricate_municipal_pnad_values():
    levels = service.supported_levels()
    assert TerritoryLevel.MUNICIPALITY in levels["gdp_per_capita"]
    assert TerritoryLevel.MUNICIPALITY not in levels["household_income_per_capita"]
    assert TerritoryLevel.MUNICIPALITY not in levels["unemployment_rate"]


async def test_latest_resolves_one_year_and_keeps_missing_values(monkeypatch):
    monkeypatch.setattr(service, "list_indicators", AsyncMock(return_value=catalog()))
    monkeypatch.setattr(service, "data_version", AsyncMock(return_value=9))
    monkeypatch.setattr(repository, "indicator_id", AsyncMock(return_value=1))
    values = AsyncMock(
        return_value=[
            ("3500001", "35", Decimal(100)),
            ("3500002", "35", None),
            ("3300001", "33", Decimal(300)),
        ]
    )
    monkeypatch.setattr(repository, "values", values)
    monkeypatch.setattr(
        service.territories_repository, "get_identity_by_code", AsyncMock(return_value=(1, "state"))
    )
    result = await service.get_values(
        AsyncMock(),
        level=TerritoryLevel.MUNICIPALITY,
        parent="35",
        indicator="population",
        year="latest",
    )
    assert values.call_args.kwargs["year"] == 2024
    assert [item.value for item in result.values] == [Decimal(100), None]
    assert result.values[1].class_index is None
    assert result.classification.max == 300
    assert result.statistics.max == 100
    assert result.statistics.missing == 1
    national = await service.get_values(
        AsyncMock(),
        level=TerritoryLevel.MUNICIPALITY,
        parent=None,
        indicator="population",
        year="latest",
    )
    assert national.classification == result.classification
    assert values.await_count == 1
    explicit = await service.get_values(
        AsyncMock(),
        level=TerritoryLevel.MUNICIPALITY,
        parent=None,
        indicator="population",
        year="2024",
    )
    assert explicit.values == national.values
    assert explicit.indicator.requested_year == "2024"
    assert values.await_count == 1


async def test_new_ingestion_changes_cache_but_weather_version_does_not(monkeypatch):
    version = AsyncMock(return_value=catalog())
    monkeypatch.setattr(service, "list_indicators", version)
    geography_version = AsyncMock(return_value=9)
    monkeypatch.setattr(service, "data_version", geography_version)
    monkeypatch.setattr(repository, "indicator_id", AsyncMock(return_value=1))
    values = AsyncMock(return_value=[("35", "3", Decimal(100))])
    monkeypatch.setattr(repository, "values", values)
    session = AsyncMock()
    first = await service.get_values(
        session, level=TerritoryLevel.STATE, parent=None, indicator="population", year="2022"
    )
    values.return_value = [("35", "3", Decimal(200))]
    same = await service.get_values(
        session, level=TerritoryLevel.STATE, parent=None, indicator="population", year="2022"
    )
    assert same == first
    version.return_value = catalog(version="2")
    renewed = await service.get_values(
        session, level=TerritoryLevel.STATE, parent=None, indicator="population", year="2022"
    )
    assert renewed.values[0].value == 200
    assert values.await_count == 2
    geography_version.return_value = 10
    values.return_value = [("35", "3", Decimal(300))]
    geography_renewed = await service.get_values(
        session, level=TerritoryLevel.STATE, parent=None, indicator="population", year="2022"
    )
    assert geography_renewed.values[0].value == 300
    assert values.await_count == 3


async def test_territory_overview_cache_tracks_ingestion_geography_and_year(monkeypatch):
    version = AsyncMock(return_value="1")
    geography_version = AsyncMock(return_value=9)
    detail = AsyncMock(
        return_value=TerritoryDetail(
            ibge_code="35",
            name="São Paulo",
            level=TerritoryLevel.STATE,
            children_count=0,
        )
    )
    values = AsyncMock(return_value={"population": {"value": 100, "year": 2024, "source": "IBGE"}})
    monkeypatch.setattr(repository, "version", version)
    monkeypatch.setattr(service, "data_version", geography_version)
    monkeypatch.setattr(service, "get_detail", detail)
    monkeypatch.setattr(service, "list_indicators", AsyncMock(return_value=catalog()))
    monkeypatch.setattr(repository, "territory_values", values)
    session = AsyncMock()
    first = await service.get_overview(session, "35", "latest")
    assert await service.get_overview(session, "35", "latest") == first
    assert values.await_count == detail.await_count == 1
    await service.get_overview(session, "35", "2022")
    assert values.call_args.args[-1] == 2022
    version.return_value = "2"
    await service.get_overview(session, "35", "latest")
    geography_version.return_value = 10
    await service.get_overview(session, "35", "latest")
    assert values.await_count == detail.await_count == 4


async def test_parent_level_is_validated_before_reading_values(monkeypatch):
    monkeypatch.setattr(
        service.territories_repository,
        "get_identity_by_code",
        AsyncMock(return_value=(1, "region")),
    )
    with pytest.raises(InvalidParameterError):
        await service.get_values(
            AsyncMock(),
            level=TerritoryLevel.MUNICIPALITY,
            parent="3",
            indicator="population",
            year="2022",
        )


async def test_http_supports_conditional_read_without_provider_calls(monkeypatch):
    monkeypatch.setattr(service, "list_indicators", AsyncMock(return_value=catalog()))

    async def session_override():
        yield AsyncMock()

    app.dependency_overrides[get_session] = session_override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/socioeconomic/indicators")
            assert response.status_code == 200
            assert response.json()["indicators"][0]["latestYear"] == 2024
            cached = await client.get(
                "/api/v1/socioeconomic/indicators",
                headers={"If-None-Match": response.headers["etag"]},
            )
            assert cached.status_code == 304
            assert cached.content == b""
    finally:
        app.dependency_overrides.pop(get_session, None)


async def setup_values(session):
    country = Territory(ibge_code="SOCIO-TST", name="País de teste", level=TerritoryLevel.COUNTRY)
    session.add(country)
    await session.flush()
    region = Territory(
        ibge_code="9", name="Região de teste", level=TerritoryLevel.REGION, parent_id=country.id
    )
    session.add(region)
    await session.flush()
    state = Territory(
        ibge_code="98", name="Estado de teste", level=TerritoryLevel.STATE, parent_id=region.id
    )
    session.add(state)
    await session.flush()
    municipalities = [
        Territory(ibge_code=code, name=code, level=TerritoryLevel.MUNICIPALITY, parent_id=state.id)
        for code in ["9800001", "9800002"]
    ]
    session.add_all(municipalities)
    await session.flush()
    ids = {
        key: identifier
        for key, identifier in (await session.execute(select(Indicator.key, Indicator.id))).all()
    }
    dataset = await upsert_dataset(session, source="test", code="socioeconomic", name="Teste")
    return municipalities, ids, dataset


@pytest.mark.db
async def test_values_without_geometry_and_without_current_year_remain_visible(session):
    municipalities, ids, dataset = await setup_values(session)
    session.add_all(
        [
            IndicatorValue(
                territory_id=municipalities[0].id,
                indicator_id=ids["population"],
                reference_year=1901,
                value=100,
                dataset_id=dataset,
            ),
            IndicatorValue(
                territory_id=municipalities[1].id,
                indicator_id=ids["population"],
                reference_year=1900,
                value=200,
                dataset_id=dataset,
            ),
        ]
    )
    await session.flush()
    rows = await repository.values(
        session, level=TerritoryLevel.MUNICIPALITY, indicator_id=ids["population"], year=1901
    )
    mapped = {code: value for code, _, value in rows}
    assert mapped["9800001"] == 100
    assert mapped["9800002"] is None
    result = await service.get_values(
        session, level=TerritoryLevel.MUNICIPALITY, parent="98", indicator="population", year="1901"
    )
    assert result.statistics.count == 1
    assert result.statistics.missing == 1
    await session.rollback()


@pytest.mark.db
async def test_ratio_uses_previous_denominator_and_never_a_future_one(session):
    municipalities, ids, dataset = await setup_values(session)
    for territory, year in [(municipalities[0], 1900), (municipalities[1], 1902)]:
        session.add_all(
            [
                IndicatorValue(
                    territory_id=territory.id,
                    indicator_id=ids["gdp"],
                    reference_year=1901,
                    value=1000,
                    dataset_id=dataset,
                ),
                IndicatorValue(
                    territory_id=territory.id,
                    indicator_id=ids["population"],
                    reference_year=year,
                    value=10,
                    dataset_id=dataset,
                ),
            ]
        )
    await session.flush()
    spec = next(spec for spec in DERIVED_INDICATORS if spec.indicator_key == "gdp_per_capita")
    await derive(session, spec, indicator_ids=ids, dataset_id=dataset)
    rows = await repository.values(
        session, level=TerritoryLevel.MUNICIPALITY, indicator_id=ids["gdp_per_capita"], year=1901
    )
    mapped = {code: value for code, _, value in rows}
    assert mapped["9800001"] == 100
    assert mapped["9800002"] is None
    await session.rollback()


@pytest.mark.db
async def test_failed_import_can_roll_back_every_written_batch(session, monkeypatch):
    municipalities, ids, dataset = await setup_values(session)
    await session.flush()
    savepoint = await session.begin_nested()
    observation = IndicatorObservation(
        ibge_code=municipalities[0].ibge_code, reference_year=1901, value=Decimal(100)
    )
    await persist(
        session, [observation], indicator_id=ids["population"], dataset_id=dataset, run_id=None
    )
    monkeypatch.setattr(
        "app.jobs.import_indicators.agregados.fetch_available_periods",
        AsyncMock(return_value=["1901"]),
    )
    monkeypatch.setattr(
        "app.jobs.import_indicators.agregados.fetch_observations",
        AsyncMock(side_effect=RuntimeError("fonte indisponível")),
    )
    with pytest.raises(RuntimeError, match="fonte indisponível"):
        await import_spec(
            session,
            AsyncMock(),
            SOURCED_INDICATORS[0],
            options=Options(periods="1901"),
            indicator_id=ids["population"],
            run_id=None,
        )
    await savepoint.rollback()
    assert (
        await session.execute(
            text("SELECT count(*) FROM socioeconomic_values WHERE territory_id = :id"),
            {"id": municipalities[0].id},
        )
    ).scalar_one() == 0
    await session.rollback()


async def test_requested_year_includes_all_published_quarters(monkeypatch):
    from unittest.mock import Mock

    spec = next(item for item in SOURCED_INDICATORS if item.table == "6468")
    monkeypatch.setattr(
        "app.jobs.import_indicators.agregados.fetch_available_periods",
        AsyncMock(return_value=["202501", "202502", "202601", "202602"]),
    )
    fetch = AsyncMock(return_value=([], 0))
    monkeypatch.setattr("app.jobs.import_indicators.agregados.fetch_observations", fetch)
    monkeypatch.setattr("app.jobs.import_indicators.upsert_dataset", AsyncMock(return_value=1))
    result = Mock()
    result.scalars.return_value = []
    session = AsyncMock()
    session.execute.return_value = result
    await import_spec(
        session, AsyncMock(), spec, options=Options(periods="2026"), indicator_id=1, run_id=1
    )
    assert fetch.call_args.args[1].periods == "202601|202602"

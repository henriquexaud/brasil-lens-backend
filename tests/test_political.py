import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models import TerritoryLevel
from app.providers.tse import TOTAL_FIELDS, ElectionStage, archive_files, number
from app.repositories import political as repository
from app.services import political as service


@pytest.fixture
def stage(tmp_path: Path):
    value = ElectionStage(tmp_path / "stage.sqlite", 2026, {"BR", "35", "3550308", "3509502"})
    value.crosswalk = {"1": "3550308", "2": "3509502"}
    yield value
    value.db.close()


def row(**patch):
    return {
        "ANO_ELEICAO": "2026",
        "CD_TIPO_ELEICAO": "2",
        "SG_UF": "SP",
        "CD_MUNICIPIO": "1",
        "CD_CARGO": "1",
        "NR_TURNO": "1",
        "DT_GERACAO": "08/10/2026",
        "HH_GERACAO": "10:00:00",
        **patch,
    }


def candidate(candidate_id="100", votes="20", status="2º TURNO", **patch):
    return row(
        SQ_CANDIDATO=candidate_id,
        NM_URNA_CANDIDATO="Candidato " + candidate_id,
        NR_CANDIDATO="13",
        SG_PARTIDO="PT",
        NR_PARTIDO="13",
        DS_SIT_TOT_TURNO=status,
        QT_VOTOS_NOMINAIS_VALIDOS=votes,
        QT_VOTOS_NOMINAIS="900",
        **patch,
    )


def totals(**patch):
    return row(**{field: "100" for field in TOTAL_FIELDS.values()}, **patch)


def test_sentinels_do_not_become_zero():
    assert number("#NULO") is None
    assert number("-3") is None
    assert number("0") == 0


def test_national_archive_is_not_added_to_state_duplicates(tmp_path):
    path = tmp_path / "source.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name in ["source_BRASIL.csv", "source_SP.csv", "source_BR.csv"]:
            archive.writestr(name, "duplicate")
    with zipfile.ZipFile(path) as archive:
        assert archive_files(archive) == ["source_BRASIL.csv"]


def test_crosswalk_is_required_and_supplementary_elections_excluded(stage):
    assert stage.dimensions(row(CD_TIPO_ELEICAO="1")) is None
    assert stage.dimensions(row(SG_UF="ZZ")) is None
    with pytest.raises(ValueError, match="correspondência"):
        stage.dimensions(row(CD_MUNICIPIO="999"))


def test_valid_votes_sum_zones_and_do_not_infer_election(stage):
    stage.load_rows(
        "votacao_candidato_munzona",
        iter([candidate(votes="20"), candidate(votes="30"), candidate("200", "50", "ELEITO")]),
    )
    stage.load_rows("detalhe_votacao_munzona", iter([totals()]))
    stage.aggregate()
    result = next(
        item
        for item in stage.result_rows()
        if item["round"] == 1 and item["territory_code"] == "3550308"
    )
    assert result["data"]["leaders"] == [{"id": "100", "votes": 50}, {"id": "200", "votes": 50}]
    assert result["data"]["candidateTie"] is True
    elected = next(
        item
        for item in stage.result_rows()
        if item["round"] == 0 and item["territory_code"] == "3550308"
    )
    assert elected["data"]["leaders"][0]["id"] == "200"
    candidates = list(stage.candidate_rows())
    assert (
        next(item for item in candidates if item["candidate_id"] == "100")["elected_round"] is None
    )


def test_party_legend_votes_are_added_once(stage):
    stage.load_rows(
        "votacao_partido_munzona",
        iter(
            [
                row(
                    SG_PARTIDO="PT",
                    QT_VOTOS_NOMINAIS_VALIDOS="10",
                    QT_TOTAL_VOTOS_LEG_VALIDOS="7",
                    QT_VOTOS_NOM_CONVR_LEG_VALIDOS="5",
                )
            ]
        ),
    )
    assert stage.db.execute("SELECT votes FROM parties").fetchone()[0] == 17


def test_totals_preserve_null_and_zero_and_unpublished_round_is_hidden(stage):
    first = totals()
    first["QT_VOTOS_BRANCOS"] = "0"
    first["QT_TOTAL_VOTOS_NULOS"] = "-1"
    future = totals(NR_TURNO="2")
    future["QT_VOTOS"] = "0"
    stage.load_rows("detalhe_votacao_munzona", iter([first, future]))
    stage.aggregate()
    assert stage.metadata()["status"] == "partial"
    assert stage.metadata()["contests"][0]["rounds"] == [1]
    result = next(stage.result_rows())["data"]
    assert result["blankVotes"] == 0
    assert result["nullVotes"] is None


def test_percentage_denominators_and_missing_margin():
    data = {
        "eligible": 100,
        "turnout": 80,
        "abstention": 20,
        "totalVotes": 160,
        "validVotes": 120,
        "blankVotes": 16,
        "nullVotes": 24,
        "leaders": [{"id": "a", "votes": 60}, {"id": "b", "votes": 30}],
    }
    assert service.mapped_value("35", data, "turnout", {}).value == 80
    assert service.mapped_value("35", data, "invalid_votes", {}).value == 25
    assert service.mapped_value("35", data, "leader_share", {}).value == 50
    assert service.mapped_value("35", data, "margin", {}).value == 25
    assert service.mapped_value("35", {}, "turnout", {}).value is None
    assert (
        service.mapped_value("35", {"leaders": [{"id": "a", "votes": 1}]}, "margin", {}).value
        is None
    )


async def test_cache_changes_with_release_and_geography(monkeypatch):
    service.clear_cache()
    metadata = {
        "status": "partial",
        "updatedAt": "2026-10-08T10:00:00-03:00",
        "note": "TSE",
        "contests": [{"office": "president", "rounds": [1]}],
    }
    release = SimpleNamespace(year=2026, run_id=10, metadata_json=metadata)
    monkeypatch.setattr(repository, "releases", AsyncMock(return_value=[release]))
    geography = AsyncMock(return_value=1)
    monkeypatch.setattr(service, "data_version", geography)
    results = AsyncMock(return_value=[("35", {"eligible": 10, "turnout": 0})])
    monkeypatch.setattr(repository, "results", results)
    response = await service.values(
        AsyncMock(),
        year=2026,
        office="president",
        election_round=1,
        metric="turnout",
        level=TerritoryLevel.STATE,
        parent=None,
    )
    assert response.values[0].value == 0
    assert response.status == "partial"
    await service.values(
        AsyncMock(),
        year=2026,
        office="president",
        election_round=1,
        metric="turnout",
        level=TerritoryLevel.STATE,
        parent=None,
    )
    assert results.await_count == 1
    release.run_id = 11
    await service.values(
        AsyncMock(),
        year=2026,
        office="president",
        election_round=1,
        metric="turnout",
        level=TerritoryLevel.STATE,
        parent=None,
    )
    geography.return_value = 2
    await service.values(
        AsyncMock(),
        year=2026,
        office="president",
        election_round=1,
        metric="turnout",
        level=TerritoryLevel.STATE,
        parent=None,
    )
    assert results.await_count == 3
    service.clear_cache()


async def test_catalog_conditional_http_and_invalid_round(monkeypatch):
    metadata = {
        "year": 2026,
        "status": "partial",
        "updatedAt": None,
        "note": "TSE",
        "contests": [{"office": "president", "rounds": [1]}],
    }
    monkeypatch.setattr(
        repository,
        "releases",
        AsyncMock(return_value=[SimpleNamespace(year=2026, run_id=1, metadata_json=metadata)]),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/api/v1/political/catalog")
        assert first.status_code == 200
        second = await client.get(
            "/api/v1/political/catalog", headers={"If-None-Match": first.headers["ETag"]}
        )
        assert second.status_code == 304
        missing = await client.get("/api/v1/political/values?year=2026&round=2")
        assert missing.status_code == 400
        assert missing.json()["error"]["code"] == "invalid_parameter"


def test_elected_status_is_preserved_when_round_rows_are_reversed(stage):
    stage.load_rows(
        "votacao_candidato_munzona",
        iter(
            [
                candidate(status="ELEITO", NR_TURNO="2"),
                candidate(status="2º TURNO", NR_TURNO="1"),
            ]
        ),
    )
    stage.load_rows("detalhe_votacao_munzona", iter([totals()]))
    stage.aggregate()
    elected = next(stage.candidate_rows())
    assert elected["elected_round"] == 2
    assert elected["data"]["status"] == "ELEITO"


@pytest.mark.db
@pytest.mark.ingested
async def test_publish_is_idempotent_and_failure_can_rollback(session, stage):
    from sqlalchemy import select, text

    from app.jobs.import_elections import publish
    from app.models import IngestionRun, PoliticalCandidate, PoliticalResult

    stage.load_rows("detalhe_votacao_munzona", iter([totals()]))
    stage.load_rows("votacao_candidato_munzona", iter([candidate(status="ELEITO")]))
    stage.aggregate()
    stage.year = 2080
    run = IngestionRun(job="political_test", source="TSE")
    session.add(run)
    await session.flush()
    try:
        await publish(session, stage, run.id, stage.metadata())
        before = (
            (
                await session.execute(
                    text(
                        "SELECT ctid::text FROM political_results WHERE year=2080 "
                        "ORDER BY office,round,territory_code"
                    )
                )
            )
            .scalars()
            .all()
        )
        session.add(
            PoliticalCandidate(
                year=2080,
                candidate_id="stale",
                office="president",
                scope_code="BR",
                elected_round=1,
                data={"id": "stale"},
            )
        )
        session.add(
            PoliticalResult(
                year=2080,
                office="president",
                round=2,
                territory_code="3550308",
                data={"totalVotes": 1},
            )
        )
        await session.flush()
        await publish(session, stage, run.id, stage.metadata())
        assert (
            await session.execute(
                select(PoliticalCandidate).where(
                    PoliticalCandidate.year == 2080, PoliticalCandidate.candidate_id == "stale"
                )
            )
        ).first() is None
        after = (
            (
                await session.execute(
                    text(
                        "SELECT ctid::text FROM political_results WHERE year=2080 "
                        "ORDER BY office,round,territory_code"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert before and before == after
        await session.rollback()
        assert (
            await session.execute(select(PoliticalResult).where(PoliticalResult.year == 2080))
        ).first() is None
    finally:
        await session.rollback()

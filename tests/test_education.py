import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import orjson
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidParameterError, NotFoundError, ProviderError
from app.jobs.import_education import enrollment_totals
from app.providers import inep_higher_education as inep
from app.services import education as service

IES_HEADER = (
    "NU_ANO_CENSO;CO_MUNICIPIO_IES;TP_ORGANIZACAO_ACADEMICA;TP_CATEGORIA_ADMINISTRATIVA;"
    "CO_IES;NO_IES;SG_IES;QT_TEC_TOTAL;QT_DOC_EXE"
)
COURSE_HEADER = (
    "NU_ANO_CENSO;CO_MUNICIPIO;TP_CATEGORIA_ADMINISTRATIVA;CO_IES;TP_MODALIDADE_ENSINO;"
    "TP_NIVEL_ACADEMICO;QT_CURSO;QT_MAT;QT_ING;QT_CONC"
)


def census_zip(tmp_path: Path, ies: list[str], courses: list[str], *, ies_name: str) -> Path:
    path = tmp_path / "censo.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"dados/{ies_name}", "\n".join([IES_HEADER, *ies]).encode("latin-1"))
        archive.writestr(
            "dados/MICRODADOS_CADASTRO_CURSOS_2024.CSV",
            "\n".join([COURSE_HEADER, *courses]).encode("latin-1"),
        )
    return path


@pytest.mark.parametrize(
    "ies_name", ["MICRODADOS_ED_SUP_IES_2024.CSV", "MICRODADOS_CADASTRO_IES_2024.CSV"]
)
def test_census_keeps_public_presential_undergraduate_offers_by_municipality(tmp_path, ies_name):
    path = census_zip(
        tmp_path,
        [
            "2024;3106200;1;1;10;UNIVERSIDADE FEDERAL DE MINAS GERAIS;UFMG;4000;3000",
            "2024;3550308;1;2;20;UNIVERSIDADE DE SÃO PAULO;USP;;",
            "2024;3550308;3;4;30;FACULDADE PRIVADA;FP;1;1",
            "2024;3548708;3;7;40;FACULDADE ESPECIAL;;1;1",
        ],
        [
            "2024;3106200;1;10;1;1;1;100;30;20",
            "2024;3106200;1;10;1;1;1;50;10;5",
            "2024;3143302;1;10;1;1;1;0;0;0",
            # A distância: só soma no total da instituição, sem município.
            "2024;3106200;1;10;2;1;1;70;0;0",
            "2024;3550308;1;10;2;1;1;30;0;0",
            # Sequencial, privada e especial ficam de fora.
            "2024;3106200;1;10;1;2;1;999;0;0",
            "2024;3550308;4;30;1;1;1;999;0;0",
            "2024;3548708;7;40;1;1;1;999;0;0",
            "2024;3550308;2;20;1;1;2;200;50;40",
        ],
        ies_name=ies_name,
    )
    census = inep.read_census(path, 2024)
    assert set(census.institutions) == {10, 20}
    usp = census.institutions[20]
    assert (usp.category, usp.organization, usp.faculty, usp.staff) == (
        "state",
        "university",
        None,
        None,
    )
    assert census.institutions[10].acronym == "UFMG"
    ufmg = census.offers[(10, "3106200")]
    assert (ufmg.courses, ufmg.enrollment, ufmg.entrants, ufmg.graduates) == (2, 150, 40, 25)
    # Curso novo, sem aluno ainda: a oferta existe com zero matrícula.
    assert census.offers[(10, "3143302")].enrollment == 0
    assert census.distance_enrollment == {10: 100}
    assert census.offers[(20, "3550308")].courses == 2


def test_census_with_missing_column_is_a_provider_error(tmp_path):
    path = tmp_path / "censo.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("MICRODADOS_ED_SUP_IES_2024.CSV", "CO_IES;NO_IES\n1;X")
        archive.writestr("MICRODADOS_CADASTRO_CURSOS_2024.CSV", COURSE_HEADER)
    with pytest.raises(ProviderError, match="faltam as colunas"):
        inep.read_census(path, 2024)


def test_census_that_is_not_a_zip_is_a_provider_error(tmp_path):
    path = tmp_path / "censo.zip"
    path.write_bytes(b"<html>manutencao</html>")
    with pytest.raises(ProviderError):
        inep.read_census(path, 2024)


def test_census_without_public_offers_is_a_provider_error(tmp_path):
    path = census_zip(
        tmp_path,
        ["2024;3550308;3;4;30;FACULDADE PRIVADA;FP;1;1"],
        ["2024;3550308;4;30;1;1;1;10;0;0"],
        ies_name="MICRODADOS_ED_SUP_IES_2024.CSV",
    )
    with pytest.raises(ProviderError, match="sem instituições públicas"):
        inep.read_census(path, 2024)


def test_totals_add_up_by_network_and_skip_territories_without_offer():
    census = inep.Census(year=2024)
    for code, category in ((10, "federal"), (20, "state")):
        census.institutions[code] = inep.Institution(
            code=code,
            name=str(code),
            acronym=None,
            category=category,
            organization="university",
            seat_code=None,
            faculty=None,
            staff=None,
        )
    census.offers[(10, "3106200")] = inep.Offer(enrollment=150)
    census.offers[(10, "3143302")] = inep.Offer(enrollment=0)
    census.offers[(20, "3550308")] = inep.Offer(enrollment=200)
    totals = enrollment_totals(census)
    assert totals["public"] == {
        "3106200": 150,
        "3143302": 0,
        "3550308": 200,
        "31": 150,
        "35": 200,
        "3": 350,
        "BR": 350,
    }
    assert totals["federal"] == {"3106200": 150, "3143302": 0, "31": 150, "3": 150, "BR": 150}
    assert "3106200" not in totals["state"]
    assert totals["municipal"] == {}


# --- Banco -------------------------------------------------------------------

YEAR = 2099


@asynccontextmanager
async def _api(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    from app.api.deps import get_session
    from app.main import app

    async def override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture
async def seeded(session: AsyncSession) -> AsyncSession:
    service.clear_cache()
    dataset = await session.execute(
        text(
            "INSERT INTO datasets (source, code, name) VALUES ('inep', 'teste/educacao', 'Teste') "
            "RETURNING id"
        )
    )
    dataset_id = dataset.scalar_one()
    await session.execute(
        text("""
        INSERT INTO education_institutions
            (year, code, name, acronym, category, organization, seat_territory_id, faculty,
             staff, distance_enrollment, dataset_id)
        SELECT :year, source.code, source.name, source.acronym, source.category, 'university',
               (SELECT id FROM territories WHERE ibge_code = source.seat), 10, 5, 7, :dataset
          FROM (VALUES (9001, 'FEDERAL TESTE', 'FT', 'federal', '3106200'),
                       (9002, 'ESTADUAL TESTE', NULL, 'state', '3550308'))
               AS source(code, name, acronym, category, seat)
    """),
        {"year": YEAR, "dataset": dataset_id},
    )
    await session.execute(
        text("""
        INSERT INTO education_offers
            (year, institution_code, territory_id, courses, enrollment, entrants, graduates)
        SELECT :year, source.code, t.id, source.courses, source.enrollment, 1, 1
          FROM (VALUES (9001, '3106200', 3, 300), (9001, '3143302', 1, 40),
                       (9002, '3550308', 5, 500), (9002, '3106200', 1, 10))
               AS source(code, municipality, courses, enrollment)
          JOIN territories t ON t.ibge_code = source.municipality
    """),
        {"year": YEAR},
    )
    return session


@pytest.mark.db
@pytest.mark.ingested
async def test_territory_lists_institutions_with_offer_inside_it(seeded):
    body = orjson.loads(await service.get_territory(seeded, "31", str(YEAR), "public"))
    assert body["year"] == YEAR
    assert [item["code"] for item in body["institutions"]] == [9001, 9002]
    federal = body["institutions"][0]
    # Só as ofertas dentro de MG: 2 municípios, 340 matrículas.
    assert (federal["enrollment"], federal["municipalities"], federal["courses"]) == (340, 2, 4)
    assert federal["seat"] == {"code": "3106200", "name": "Belo Horizonte", "state": "MG"}
    assert body["enrollment"] == 350

    state = orjson.loads(await service.get_territory(seeded, "31", str(YEAR), "state"))
    assert [item["code"] for item in state["institutions"]] == [9002]

    country = orjson.loads(await service.get_territory(seeded, "BR", str(YEAR), "public"))
    assert country["enrollment"] == 850


@pytest.mark.db
@pytest.mark.ingested
async def test_territory_without_offer_has_no_enrollment_instead_of_zero(seeded):
    body = orjson.loads(await service.get_territory(seeded, "1100015", str(YEAR), "public"))
    assert body["institutions"] == []
    assert body["enrollment"] is None


@pytest.mark.db
@pytest.mark.ingested
async def test_institution_detail_lists_campuses_by_enrollment(seeded):
    body = orjson.loads(await service.get_institution(seeded, 9001, str(YEAR)))
    assert [item["code"] for item in body["campuses"]] == ["3106200", "3143302"]
    assert body["campuses"][1]["state"] == "MG"
    assert (body["enrollment"], body["distanceEnrollment"], body["courses"]) == (340, 7, 4)
    with pytest.raises(NotFoundError):
        await service.get_institution(seeded, 1, str(YEAR))


@pytest.mark.db
@pytest.mark.ingested
async def test_year_without_census_is_rejected(seeded):
    with pytest.raises(InvalidParameterError):
        await service.get_territory(seeded, "BR", "1950", "public")


@pytest.mark.db
@pytest.mark.ingested
async def test_routes_answer_with_etag_and_304(seeded):
    async with _api(seeded) as client:
        first = await client.get(f"/api/v1/education/territories/31?year={YEAR}")
        assert first.status_code == 200
        assert first.headers["cache-control"] == "public, max-age=300"
        again = await client.get(
            f"/api/v1/education/territories/31?year={YEAR}",
            headers={"If-None-Match": first.headers["etag"]},
        )
        assert again.status_code == 304
        assert (
            await client.get("/api/v1/education/territories/31?network=private")
        ).status_code == 422
        detail = await client.get(f"/api/v1/education/institutions/9002?year={YEAR}")
        assert detail.json()["campuses"][0]["code"] == "3550308"


async def test_ingest_downloads_every_year_before_opening_the_transaction(monkeypatch, tmp_path):
    from contextlib import asynccontextmanager

    from app.jobs import import_education as job

    events: list[str] = []

    async def download(client, year, directory):
        events.append(f"download {year}")
        return tmp_path / f"{year}.zip"

    @asynccontextmanager
    async def session(**_):
        events.append("transaction")
        raise RuntimeError("parar aqui")
        yield  # pragma: no cover

    monkeypatch.setattr(job.inep, "download", download)
    monkeypatch.setattr(job.inep, "read_census", lambda path, year: inep.Census(year=year))
    monkeypatch.setattr(job, "job_session", session)
    with pytest.raises(RuntimeError):
        await job.ingest([2024, 2023], tmp_path)
    assert events == ["download 2023", "download 2024", "transaction"]

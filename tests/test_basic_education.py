import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from app.core.errors import ProviderError
from app.jobs import import_basic_education as job
from app.jobs.seed_indicators import BASIC_EDUCATION_CATALOG, BASIC_STAGES, RETIRED_BASIC_KEYS
from app.providers import inep_basic_education as inep

# Ordem real das colunas é outra e há centenas delas; o leitor acha cada uma pelo nome.
HEADER = (
    "NU_ANO_CENSO;CO_MUNICIPIO;CO_ENTIDADE;NO_ENTIDADE;TP_DEPENDENCIA;QT_MAT_INF;QT_MAT_BAS;"
    "QT_MAT_INF_PRE;QT_MAT_FUND;QT_MAT_MED"
)
ROWS = [
    # Municipal com educação infantil (40 na creche e 60 na pré-escola, que o leitor
    # ignora), estadual com fundamental e médio, federal com médio.
    "2024;3106200;1; EMEI SOL ;3;100;300;60;200;",
    "2024;3106200;2;EE LUA;2;0;900;0;500;400",
    "2024;3106200;3;CEFET;1;;20;;;20",
    # Escola sem matrícula na educação básica (só atividade complementar) não conta.
    "2024;3106200;4;EM VAZIA;3;;0;;;",
    # Privada, inclusive conveniada, fica de fora.
    "2024;3106200;5;CRECHE PRIVADA;4;80;80;;;",
    "2024;3143302;6;EM CAMPO;3;;150;;150;",
]


def school_zip(tmp_path: Path, member: str, rows: list[str], header: str = HEADER) -> Path:
    path = tmp_path / "escolar.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"x/dados/{member}", "\n".join([header, *rows]).encode("latin-1"))
        archive.writestr("x/dados/suplemento_cursos_tecnicos_2024.csv", b"NU_ANO_CENSO\n")
        archive.writestr("x/dados/Tabela_Escola_2025_V2.csv", b"NU_ANO_CENSO\n")
    return path


def _counts(schools: dict[str, int], enrollment: dict[str, int]) -> inep.Counts:
    return inep.Counts(
        schools={stage: schools.get(stage, 0) for stage in inep.STAGES},
        enrollment={stage: enrollment.get(stage, 0) for stage in inep.STAGES},
    )


@pytest.mark.parametrize(
    "member", ["microdados_ed_basica_2024.csv", "Tabela_Matricula_2025_V2.csv"]
)
def test_census_counts_public_schools_by_municipality_and_stage(tmp_path, member):
    census = inep.read_census(school_zip(tmp_path, member, ROWS), 2024)
    assert census.municipalities["3106200"] == _counts(
        {"all": 3, "early_childhood": 1, "elementary": 2, "high_school": 2},
        {"all": 1220, "early_childhood": 100, "elementary": 700, "high_school": 420},
    )
    # Sem pedir, a lista de escolas não é montada (só o último ano precisa dela).
    assert census.schools == []
    # Município sem educação infantil pública ainda entra, com zero nela.
    assert census.municipalities["3143302"] == _counts(
        {"all": 1, "elementary": 1}, {"all": 150, "elementary": 150}
    )


def test_census_keeps_each_public_school_when_asked(tmp_path):
    path = school_zip(tmp_path, "Tabela_Matricula_2025_V2.csv", ROWS)
    schools = inep.read_census(path, 2025, schools=True).schools
    assert [(item.code, item.name, item.network) for item in schools] == [
        (1, "EMEI SOL", "municipal"),
        (2, "EE LUA", "state"),
        (3, "CEFET", "federal"),
        (6, "EM CAMPO", "municipal"),
    ]
    assert schools[0].municipality == "3106200"
    assert schools[1].enrollment == {
        "all": 900,
        "early_childhood": 0,
        "elementary": 500,
        "high_school": 400,
    }


def test_census_with_missing_column_is_a_provider_error(tmp_path):
    header = (
        "NU_ANO_CENSO;CO_MUNICIPIO;CO_ENTIDADE;NO_ENTIDADE;TP_DEPENDENCIA;QT_MAT_BAS;"
        "QT_MAT_INF_CRE;QT_MAT_FUND"
    )
    path = school_zip(tmp_path, "microdados_ed_basica_2024.csv", [], header=header)
    with pytest.raises(ProviderError, match="QT_MAT_INF, QT_MAT_MED"):
        inep.read_census(path, 2024)


def test_census_without_the_schools_file_is_a_provider_error(tmp_path):
    path = school_zip(tmp_path, "Tabela_Turma_2025_V2.csv", ROWS)
    with pytest.raises(ProviderError, match="único arquivo"):
        inep.read_census(path, 2025)


def test_census_that_is_not_a_zip_is_a_provider_error(tmp_path):
    path = tmp_path / "escolar.zip"
    path.write_bytes(b"<html>erro</html>")
    with pytest.raises(ProviderError, match="zip válido"):
        inep.read_census(path, 2024)


def test_census_without_public_schools_is_a_provider_error(tmp_path):
    path = school_zip(
        tmp_path, "microdados_ed_basica_2024.csv", ["2024;3106200;5;CRECHE PRIVADA;4;80;80;;;"]
    )
    with pytest.raises(ProviderError, match="sem escolas públicas"):
        inep.read_census(path, 2024)


@respx.mock
async def test_download_uses_the_file_name_inep_published_for_2025(tmp_path):
    route = respx.get(
        "https://download.inep.gov.br/dados_abertos/microdados_censo_escolar_2025_.zip"
    ).mock(return_value=httpx.Response(200, content=b"zip"))
    async with httpx.AsyncClient() as client:
        path = await inep.download(client, 2025, tmp_path)
    assert route.called
    assert path.name == "microdados_censo_escolar_2025.zip"
    assert path.read_bytes() == b"zip"


def test_catalog_has_three_measures_per_stage_and_two_for_the_whole_basic_education():
    keys = [seed.key for seed in BASIC_EDUCATION_CATALOG]
    assert keys[:2] == ["basic_schools", "basic_enrollment"]
    # Duas medidas por etapa e atendimento só na educação infantil e no médio.
    assert len(keys) == len(set(keys)) == 2 + 2 * len(BASIC_STAGES) + 2
    assert set(BASIC_STAGES) == set(inep.STAGES) - {"all"}
    assert set(job.AGE_BANDS) == {s for s, (*_, ages) in BASIC_STAGES.items() if ages}
    assert {"basic_early_childhood_coverage", "basic_high_school_coverage"} <= set(keys)
    assert "basic_elementary_coverage" not in keys
    # A ingestão apaga as chaves antigas antes de semear: nenhuma pode voltar.
    assert not set(RETIRED_BASIC_KEYS) & set(keys)
    coverage = next(seed for seed in BASIC_EDUCATION_CATALOG if seed.key.endswith("_coverage"))
    assert (coverage.unit, coverage.decimal_places) == ("%", 1)


def test_age_bands_ask_ibge_for_the_single_ages_of_each_stage():
    assert job.population_query(job.AGE_BANDS["early_childhood"]).classification == (
        "2[6794]|287[6557,6558,6559,6560,6561,6562]|286[113635]"
    )
    assert (
        "287[6572,6573,6574]"
        in job.population_query(job.AGE_BANDS["high_school"]).params["classificacao"]
    )


def _age_result(age: str, values: dict[str, str]) -> dict[str, object]:
    return {
        "classificacoes": [
            {"id": "2", "categoria": {"6794": "Total"}},
            {"id": "287", "categoria": {age: age}},
        ],
        "series": [
            {
                "localidade": {
                    "id": code,
                    "nome": code,
                    "nivel": {"id": "N6", "nome": "Município"},
                },
                "serie": {"2022": value},
            }
            for code, value in values.items()
        ],
    }


@respx.mock
async def test_population_adds_up_the_single_ages_of_each_stage(monkeypatch):
    monkeypatch.setattr(job, "AGE_BANDS", {"early_childhood": range(0, 4)})
    route = respx.get(
        "https://servicodados.ibge.gov.br/api/v3/agregados/9514/periodos/2022/variaveis/93"
    ).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "93",
                    "variavel": "População residente",
                    "unidade": "Pessoas",
                    "resultados": [
                        _age_result("6557", {"3106200": "10", "3143302": "1"}),
                        _age_result("6558", {"3106200": "20", "3143302": "2"}),
                        _age_result("6559", {"3106200": "30", "3143302": "3"}),
                        # Valor ausente numa idade: o município fica sem população.
                        _age_result("6560", {"3106200": "40", "3143302": "-"}),
                    ],
                }
            ],
        )
    )
    assert await job.fetch_population() == {"early_childhood": {"3106200": 100}}
    params = route.calls.last.request.url.params
    assert params["localidades"] == "N6[all]"
    assert params["classificacao"] == "2[6794]|287[6557,6558,6559,6560]|286[113635]"


def _census(year: int) -> inep.Census:
    census = inep.Census(year=year)
    census.municipalities["3106200"] = _counts(
        {"all": 3, "early_childhood": 2, "high_school": 2},
        {"all": 1220, "early_childhood": 45, "high_school": 700},
    )
    census.municipalities["3143302"] = _counts({"all": 1}, {"all": 150})
    census.municipalities["3550308"] = _counts(
        {"all": 10, "early_childhood": 4}, {"all": 5000, "early_childhood": 300}
    )
    # Município criado depois do Censo 2022: entra nas somas, sem atendimento próprio.
    census.municipalities["5108600"] = _counts(
        {"all": 1, "early_childhood": 1}, {"all": 10, "early_childhood": 5}
    )
    return census


POPULATION = {
    "early_childhood": {"3106200": 100, "3143302": 50, "3550308": 200, "5108700": 20},
    "high_school": {"3106200": 1000, "3143302": 100, "3550308": 0},
}


def test_totals_add_up_by_territory_and_stage_with_measured_zeros():
    totals = job.basic_totals(_census(2024), POPULATION)
    assert totals["basic_schools"] == {
        "3106200": 3,
        "3143302": 1,
        "3550308": 10,
        "5108600": 1,
        "31": 4,
        "35": 10,
        "51": 1,
        "3": 14,
        "5": 1,
        "BR": 15,
    }
    assert totals["basic_early_childhood_schools"]["3143302"] == 0
    assert totals["basic_early_childhood_enrollment"]["BR"] == 350
    assert totals["basic_enrollment"]["31"] == 1370
    assert totals["basic_elementary_schools"]["BR"] == 0


def test_coverage_divides_summed_enrollment_by_summed_population():
    totals = job.basic_totals(_census(2024), POPULATION)
    coverage = totals["basic_early_childhood_coverage"]
    assert coverage["3106200"] == Decimal("45.0")
    assert coverage["3143302"] == Decimal("0.0")
    # Acima de 100% é dado: a escola atende alunos de fora do município.
    assert coverage["3550308"] == Decimal("150.0")
    # UF: (45 + 0) / (100 + 50), não a média dos municípios.
    assert coverage["31"] == Decimal("30.0")
    # Região Sudeste: 345 / 350; Brasil: 350 / 370.
    assert coverage["3"] == Decimal("98.6")
    assert coverage["BR"] == Decimal("94.6")
    assert "5108600" not in coverage
    # Município com população zero na faixa fica sem atendimento, sem dividir por zero.
    assert "3550308" not in totals["basic_high_school_coverage"]
    assert totals["basic_high_school_coverage"]["31"] == Decimal("63.6")
    # Etapa sem faixa etária consultada não ganha atendimento.
    assert "basic_elementary_coverage" not in totals


def test_coverage_starts_in_the_population_census_year():
    assert "basic_early_childhood_coverage" not in job.basic_totals(_census(2021), POPULATION)
    assert "basic_early_childhood_coverage" in job.basic_totals(_census(2022), POPULATION)


async def test_ingest_downloads_every_year_before_opening_the_transaction(monkeypatch, tmp_path):
    events: list[str] = []

    async def download(client, year, directory):
        events.append(f"download {year}")
        return tmp_path / f"{year}.zip"

    async def population():
        events.append("ibge")
        return POPULATION

    @asynccontextmanager
    async def session(**_) -> AsyncIterator[None]:
        events.append("transaction")
        raise RuntimeError("parar aqui")
        yield  # pragma: no cover

    monkeypatch.setattr(job.inep, "download", download)

    def read(path, year, *, schools=False):
        events.append(f"read {year}{' com escolas' if schools else ''}")
        return _census(year)

    monkeypatch.setattr(job.inep, "read_census", read)
    monkeypatch.setattr(job, "fetch_population", population)
    monkeypatch.setattr(job, "job_session", session)
    with pytest.raises(RuntimeError):
        await job.ingest([2025, 2021], tmp_path)
    # Só o último ano monta a lista de escolas.
    assert events == [
        "download 2021",
        "read 2021",
        "download 2025",
        "read 2025 com escolas",
        "ibge",
        "transaction",
    ]


async def test_ingest_before_2022_does_not_ask_ibge_for_population(monkeypatch, tmp_path):
    async def download(client, year, directory):
        return tmp_path / f"{year}.zip"

    async def population():
        raise AssertionError("não deveria consultar o IBGE")

    @asynccontextmanager
    async def session(**_) -> AsyncIterator[None]:
        raise RuntimeError("parar aqui")
        yield  # pragma: no cover

    monkeypatch.setattr(job.inep, "download", download)
    monkeypatch.setattr(job.inep, "read_census", lambda path, year, **_: _census(year))
    monkeypatch.setattr(job, "fetch_population", population)
    monkeypatch.setattr(job, "job_session", session)
    with pytest.raises(RuntimeError, match="parar aqui"):
        await job.ingest([2020, 2021], tmp_path)


@pytest.mark.db
async def test_basic_education_ingestion_renews_the_indicator_cache_version(session):
    from sqlalchemy import text

    from app.repositories import socioeconomic as repository

    run = (
        await session.execute(
            text("""
            INSERT INTO ingestion_runs
                (job, source, status, records_processed, records_written, records_failed)
            VALUES ('import_basic_education', 'inep', 'succeeded', 1, 1, 0) RETURNING id
        """)
        )
    ).scalar_one()
    assert await repository.version(session) == str(run)


@pytest.fixture
async def schools(session):
    """Um município fictício com três escolas; nada é confirmado, a sessão desfaz."""
    from sqlalchemy import text

    from app.services import education as service

    service.clear_cache()
    await session.execute(
        text(
            "INSERT INTO territories (level, ibge_code, name) VALUES ('country', 'BR', 'Brasil') "
            "ON CONFLICT (ibge_code) DO NOTHING"
        )
    )
    parent = "(SELECT id FROM territories WHERE ibge_code = :parent)"
    for level, code, name, parent_code in [
        ("region", "8", "Região Teste", "BR"),
        ("state", "88", "UF Teste", "8"),
        ("municipality", "8800001", "Cidade Teste", "88"),
    ]:
        await session.execute(
            text(
                "INSERT INTO territories (level, ibge_code, name, parent_id) "
                f"VALUES (:level, :code, :name, {parent})"
            ),
            {"level": level, "code": code, "name": name, "parent": parent_code},
        )
    dataset = await session.execute(
        text(
            "INSERT INTO datasets (source, code, name) VALUES ('inep', 'teste/escolas', 'Teste') "
            "RETURNING id"
        )
    )
    census = inep.Census(year=2025)
    stages = ("all", "early_childhood", "elementary", "high_school")
    for code, name, network, enrollment in [
        (1, "EMEI SOL", "municipal", (100, 100, 0, 0)),
        (2, "EE LUA", "state", (900, 0, 500, 400)),
        (3, "EM ESTRELA", "municipal", (300, 50, 250, 0)),
        # Município fora da malha: fica sem lista.
        (4, "EM LONGE", "municipal", (10, 10, 0, 0)),
    ]:
        municipality = "8800009" if code == 4 else "8800001"
        census.schools.append(
            inep.School(
                code, name, municipality, network, dict(zip(stages, enrollment, strict=True))
            )
        )
    assert await job.replace_schools(session, census, dataset.scalar_one()) == 3
    return session


@pytest.mark.db
async def test_schools_of_a_municipality_follow_the_stage_by_enrollment(schools):
    import orjson

    from app.services import education as service

    body = orjson.loads(await service.get_schools(schools, "8800001", "all"))
    assert (body["name"], body["year"], body["stage"]) == ("Cidade Teste", 2025, "all")
    assert [(item["name"], item["enrollment"]) for item in body["schools"]] == [
        ("EE LUA", 900),
        ("EM ESTRELA", 300),
        ("EMEI SOL", 100),
    ]
    assert body["schools"][0] == {
        "code": 2,
        "name": "EE LUA",
        "network": "state",
        "enrollment": 900,
    }
    infant = orjson.loads(await service.get_schools(schools, "8800001", "early_childhood"))
    assert [(item["code"], item["enrollment"]) for item in infant["schools"]] == [(1, 100), (3, 50)]
    high = orjson.loads(await service.get_schools(schools, "8800001", "high_school"))
    assert [item["code"] for item in high["schools"]] == [2]


@pytest.mark.db
async def test_schools_exist_only_for_municipalities(schools):
    from app.core.errors import InvalidParameterError, TerritoryNotFoundError
    from app.services import education as service

    with pytest.raises(InvalidParameterError, match="só por município"):
        await service.get_schools(schools, "88", "all")
    with pytest.raises(TerritoryNotFoundError):
        await service.get_schools(schools, "8800009", "all")


@pytest.mark.db
async def test_schools_route_validates_the_stage(schools):
    from httpx import ASGITransport, AsyncClient

    from app.api.deps import get_session
    from app.main import app

    async def override():
        yield schools

    app.dependency_overrides[get_session] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ok = await client.get("/api/v1/education/schools/8800001?stage=elementary")
            assert ok.status_code == 200
            assert [item["code"] for item in ok.json()["schools"]] == [2, 3]
            assert ok.headers["cache-control"] == "public, max-age=300"
            wrong = await client.get("/api/v1/education/schools/8800001?stage=daycare")
            assert wrong.status_code == 422
    finally:
        app.dependency_overrides.pop(get_session, None)

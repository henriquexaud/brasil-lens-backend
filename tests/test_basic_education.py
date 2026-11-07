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
from app.jobs.seed_indicators import BASIC_EDUCATION_CATALOG, BASIC_STAGES
from app.providers import inep_basic_education as inep

# Ordem real das colunas é outra e há centenas delas; o leitor acha cada uma pelo nome.
HEADER = (
    "NU_ANO_CENSO;CO_MUNICIPIO;CO_ENTIDADE;TP_DEPENDENCIA;QT_MAT_INF_CRE;QT_MAT_BAS;"
    "QT_MAT_INF_PRE;QT_MAT_FUND;QT_MAT_MED"
)
ROWS = [
    # Municipal com creche e pré, estadual com fundamental e médio, federal com médio.
    "2024;3106200;1;3;40;300;60;200;",
    "2024;3106200;2;2;0;900;0;500;400",
    "2024;3106200;3;1;;20;;;20",
    # Escola sem matrícula na educação básica (só atividade complementar) não conta.
    "2024;3106200;4;3;;0;;;",
    # Privada, inclusive conveniada, fica de fora.
    "2024;3106200;5;4;80;80;;;",
    "2024;3143302;6;3;;150;;150;",
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
        {"all": 3, "daycare": 1, "preschool": 1, "elementary": 2, "high_school": 2},
        {"all": 1220, "daycare": 40, "preschool": 60, "elementary": 700, "high_school": 420},
    )
    # Município sem creche pública ainda entra, com zero creche.
    assert census.municipalities["3143302"] == _counts(
        {"all": 1, "elementary": 1}, {"all": 150, "elementary": 150}
    )


def test_census_with_missing_column_is_a_provider_error(tmp_path):
    header = "NU_ANO_CENSO;CO_MUNICIPIO;TP_DEPENDENCIA;QT_MAT_BAS;QT_MAT_INF_CRE;QT_MAT_FUND"
    path = school_zip(tmp_path, "microdados_ed_basica_2024.csv", [], header=header)
    with pytest.raises(ProviderError, match="QT_MAT_INF_PRE, QT_MAT_MED"):
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
    path = school_zip(tmp_path, "microdados_ed_basica_2024.csv", ["2024;3106200;5;4;80;80;;;"])
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
    # Duas medidas por etapa e atendimento só na creche e no médio.
    assert len(keys) == len(set(keys)) == 2 + 2 * len(BASIC_STAGES) + 2
    assert set(BASIC_STAGES) == set(inep.STAGES) - {"all"}
    assert set(job.AGE_BANDS) == {s for s, (*_, ages) in BASIC_STAGES.items() if ages}
    assert {"basic_daycare_coverage", "basic_high_school_coverage"} <= set(keys)
    assert "basic_elementary_coverage" not in keys
    coverage = next(seed for seed in BASIC_EDUCATION_CATALOG if seed.key.endswith("_coverage"))
    assert (coverage.unit, coverage.decimal_places) == ("%", 1)


def test_age_bands_ask_ibge_for_the_single_ages_of_each_stage():
    assert job.population_query(job.AGE_BANDS["daycare"]).classification == (
        "2[6794]|287[6557,6558,6559,6560]|286[113635]"
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
    monkeypatch.setattr(job, "AGE_BANDS", {"daycare": range(0, 4)})
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
    assert await job.fetch_population() == {"daycare": {"3106200": 100}}
    params = route.calls.last.request.url.params
    assert params["localidades"] == "N6[all]"
    assert params["classificacao"] == "2[6794]|287[6557,6558,6559,6560]|286[113635]"


def _census(year: int) -> inep.Census:
    census = inep.Census(year=year)
    census.municipalities["3106200"] = _counts(
        {"all": 3, "daycare": 2, "high_school": 2}, {"all": 1220, "daycare": 45, "high_school": 700}
    )
    census.municipalities["3143302"] = _counts({"all": 1}, {"all": 150})
    census.municipalities["3550308"] = _counts(
        {"all": 10, "daycare": 4}, {"all": 5000, "daycare": 300}
    )
    # Município criado depois do Censo 2022: entra nas somas, sem atendimento próprio.
    census.municipalities["5108600"] = _counts({"all": 1, "daycare": 1}, {"all": 10, "daycare": 5})
    return census


POPULATION = {
    "daycare": {"3106200": 100, "3143302": 50, "3550308": 200, "5108700": 20},
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
    assert totals["basic_daycare_schools"]["3143302"] == 0
    assert totals["basic_daycare_enrollment"]["BR"] == 350
    assert totals["basic_enrollment"]["31"] == 1370
    assert totals["basic_preschool_schools"]["BR"] == 0


def test_coverage_divides_summed_enrollment_by_summed_population():
    totals = job.basic_totals(_census(2024), POPULATION)
    coverage = totals["basic_daycare_coverage"]
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
    assert "basic_preschool_coverage" not in totals


def test_coverage_starts_in_the_population_census_year():
    assert "basic_daycare_coverage" not in job.basic_totals(_census(2021), POPULATION)
    assert "basic_daycare_coverage" in job.basic_totals(_census(2022), POPULATION)


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
    monkeypatch.setattr(job.inep, "read_census", lambda path, year: _census(year))
    monkeypatch.setattr(job, "fetch_population", population)
    monkeypatch.setattr(job, "job_session", session)
    with pytest.raises(RuntimeError):
        await job.ingest([2025, 2021], tmp_path)
    assert events == ["download 2021", "download 2025", "ibge", "transaction"]


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
    monkeypatch.setattr(job.inep, "read_census", lambda path, year: _census(year))
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

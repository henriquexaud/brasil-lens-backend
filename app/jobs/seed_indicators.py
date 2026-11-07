from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.jobs._runner import job_session, run_job
from app.models import Indicator, IndicatorOrigin

logger = get_logger(__name__)

JOB_NAME = "seed_indicators"


@dataclass(frozen=True, slots=True)
class IndicatorSeed:
    key: str
    name: str
    unit: str
    description: str
    origin: IndicatorOrigin
    decimal_places: int
    display_order: int
    context: str = "socioeconomic"


CATALOG: tuple[IndicatorSeed, ...] = (
    IndicatorSeed(
        key="population",
        name="População",
        unit="people",
        description=(
            "População residente. Anos censitários vêm do Censo Demográfico "
            "(tabela 4714); os demais, das estimativas anuais (tabela 6579)."
        ),
        origin=IndicatorOrigin.SOURCED,
        decimal_places=0,
        display_order=10,
    ),
    IndicatorSeed(
        key="population_growth",
        name="Crescimento populacional",
        unit="%/year",
        description=(
            "Variação média anual da população em relação ao ano anterior com "
            "dado publicado. Derivado na ingestão: como a série tem lacunas "
            "(2007, 2010, 2022, 2023), a taxa é anualizada geometricamente pelo "
            "intervalo real. Atenção aos anos censitários: em 2022 a taxa mede "
            "o Censo contra a estimativa de 2021, ou seja, carrega a revisão "
            "que o Censo fez da série — não um movimento demográfico."
        ),
        origin=IndicatorOrigin.DERIVED,
        decimal_places=2,
        display_order=15,
    ),
    IndicatorSeed(
        key="area_km2",
        name="Área territorial",
        unit="km2",
        description=(
            "Área da unidade territorial, em quilômetros quadrados, conforme o "
            "IBGE. Possui ano de referência e é revisada — por isso é modelada "
            "como indicador com histórico, e não como atributo fixo do território."
        ),
        origin=IndicatorOrigin.SOURCED,
        decimal_places=0,
        display_order=20,
    ),
    IndicatorSeed(
        key="population_density",
        name="Densidade demográfica",
        unit="people/km2",
        description=(
            "População residente dividida pela área territorial. Derivado na "
            "ingestão para acompanhar toda a série de população. Quando o ano da "
            "população não tem área publicada, usa-se a área de referência mais "
            "recente até aquele ano."
        ),
        origin=IndicatorOrigin.DERIVED,
        decimal_places=2,
        display_order=30,
    ),
    IndicatorSeed(
        key="gdp",
        name="PIB",
        unit="BRL",
        description=(
            "Produto Interno Bruto a preços correntes, em reais. A fonte publica "
            "em milhares de reais; a ingestão normaliza para a unidade base."
        ),
        origin=IndicatorOrigin.SOURCED,
        decimal_places=0,
        display_order=40,
    ),
    IndicatorSeed(
        key="gdp_per_capita",
        name="PIB per capita",
        unit="BRL",
        description=(
            "PIB dividido pela população. Necessariamente derivado: a tabela "
            "5938 do IBGE não publica esta variável. Quando o ano do PIB não tem "
            "população publicada, usa-se a população de referência mais recente "
            "até aquele ano."
        ),
        origin=IndicatorOrigin.DERIVED,
        decimal_places=2,
        display_order=50,
    ),
    IndicatorSeed(
        key="urban_population",
        name="População urbana",
        unit="people",
        description=(
            "População residente em situação urbana do domicílio, apurada no "
            "Censo Demográfico (tabela 9923 em 2022; tabela 202 em 2010). "
            "Existe apenas em anos censitários."
        ),
        origin=IndicatorOrigin.SOURCED,
        decimal_places=0,
        display_order=32,
    ),
    IndicatorSeed(
        key="urbanization_rate",
        name="Taxa de urbanização",
        unit="%",
        description=(
            "População urbana dividida pela população total. Derivado na "
            "ingestão e conferido contra o percentual que o próprio IBGE "
            "publica (tabela 9923, variável 1000093). Só existe em anos "
            "censitários, porque a população urbana só é apurada no Censo."
        ),
        origin=IndicatorOrigin.DERIVED,
        decimal_places=1,
        display_order=34,
    ),
    IndicatorSeed(
        key="gdp_share_national",
        name="Participação no PIB nacional",
        unit="%",
        description=(
            "PIB do território dividido pelo PIB do Brasil no mesmo ano. "
            "Derivado na ingestão e conferido contra a variável 496 da tabela "
            "5938, que o IBGE publica com esta mesma definição."
        ),
        origin=IndicatorOrigin.DERIVED,
        decimal_places=2,
        display_order=45,
    ),
    IndicatorSeed(
        key="gdp_agriculture",
        name="PIB — Agropecuária",
        unit="BRL",
        description=(
            "Valor adicionado bruto da agropecuária a preços correntes "
            "(tabela 5938, variável 513). A fonte publica em milhares de reais; "
            "a ingestão normaliza para a unidade base."
        ),
        origin=IndicatorOrigin.SOURCED,
        decimal_places=0,
        display_order=55,
    ),
    IndicatorSeed(
        key="gdp_industry",
        name="PIB — Indústria",
        unit="BRL",
        description=(
            "Valor adicionado bruto da indústria a preços correntes "
            "(tabela 5938, variável 517). A fonte publica em milhares de reais; "
            "a ingestão normaliza para a unidade base."
        ),
        origin=IndicatorOrigin.SOURCED,
        decimal_places=0,
        display_order=56,
    ),
    IndicatorSeed(
        key="gdp_services",
        name="PIB — Serviços",
        unit="BRL",
        description=(
            "Valor adicionado bruto dos serviços a preços correntes, inclusive "
            "administração, defesa, educação e saúde públicas. A tabela 5938 "
            "publica as duas parcelas separadas (variáveis 6575 e 525) e a "
            "ingestão as soma, para que os três setores fechem o valor "
            "adicionado bruto total."
        ),
        origin=IndicatorOrigin.SOURCED,
        decimal_places=0,
        display_order=57,
    ),
    IndicatorSeed(
        key="household_income_per_capita",
        name="Renda domiciliar per capita",
        unit="BRL",
        description=(
            "Rendimento médio mensal real domiciliar per capita, da PNAD "
            "Contínua anual (tabela 7395, variável 4196). A pesquisa não "
            "desagrega este indicador por município: há dado para Brasil, "
            "regiões e UFs."
        ),
        origin=IndicatorOrigin.SOURCED,
        decimal_places=0,
        display_order=60,
    ),
    IndicatorSeed(
        key="unemployment_rate",
        name="Taxa de desemprego",
        unit="%",
        description=(
            "Taxa de desocupação das pessoas de 14 anos ou mais, da PNAD "
            "Contínua. Anos fechados usam a média anual oficial (tabela 4562); "
            "o ano em curso usa a média dos trimestres já publicados (tabela "
            "6468). A pesquisa cobre Brasil, regiões e UFs — no nível municipal "
            "ela só apura as capitais."
        ),
        origin=IndicatorOrigin.SOURCED,
        decimal_places=1,
        display_order=70,
    ),
)


# Publicado no contexto Meio ambiente e alimentado por `import_environment` (ADR-15).
ENVIRONMENT_CATALOG: tuple[IndicatorSeed, ...] = (
    IndicatorSeed(
        key="deforestation_prodes",
        name="Desmatamento anual",
        unit="km2",
        description=(
            "Área de vegetação nativa suprimida no ano PRODES (agosto a julho), somando "
            "os seis biomas monitorados pelo INPE. O PRODES mapeia o bioma inteiro: zero "
            "é medição, não ausência. Um ano só entra quando todos os biomas o publicaram."
        ),
        origin=IndicatorOrigin.SOURCED,
        decimal_places=2,
        display_order=110,
        context="environment",
    ),
    IndicatorSeed(
        key="forest_cover",
        name="Cobertura florestal",
        unit="%",
        description=(
            "Parte da área do território coberta por floresta (formação florestal, "
            "savânica, mangue, floresta alagável e restinga arbórea), segundo a Coleção 11 "
            "do MapBiomas. Série a cada cinco anos desde 1985 e anual a partir de 2021."
        ),
        origin=IndicatorOrigin.SOURCED,
        decimal_places=1,
        display_order=120,
        context="environment",
    ),
)


# Educação (ADR-19): matrículas de graduação presencial por município de oferta,
# no Censo da Educação Superior do INEP. Sem oferta, o território fica sem valor.
_EDUCATION_SOURCE = (
    "Graduação presencial, pelo município onde o curso é oferecido (Censo da Educação "
    "Superior, INEP). Cursos a distância não entram: o INEP não os atribui a um local "
    "de oferta. Território sem oferta fica sem valor."
)
_EDUCATION_NETWORKS = (
    ("federal", "federais", 220),
    ("state", "estaduais", 230),
    ("municipal", "municipais", 240),
)
_EDUCATION_KINDS = (
    ("", "instituições", "Todas as instituições da rede."),
    ("university_", "universidades", "Só universidades."),
    ("institute_", "institutos", "Só institutos federais e CEFETs."),
    ("college_", "faculdades", "Faculdades e centros universitários."),
)
# Uma rede (o switch) por tipo de instituição (o filtro): a chave sem tipo é a
# rede inteira. Combinação sem oferta, como instituto estadual, fica sem anos e
# o frontend esconde o filtro.
EDUCATION_CATALOG: tuple[IndicatorSeed, ...] = tuple(
    IndicatorSeed(
        key=f"higher_ed_{network}_{kind}enrollment",
        name=f"Matrículas em {label} {adjective}",
        unit="enrollments",
        description=f"{scope} {_EDUCATION_SOURCE}",
        origin=IndicatorOrigin.SOURCED,
        decimal_places=0,
        display_order=order + index,
        context="education",
    )
    for network, adjective, order in _EDUCATION_NETWORKS
    for index, (kind, label, scope) in enumerate(_EDUCATION_KINDS)
)
# Rede retirada do mapa: a ingestão apaga o indicador e os valores dele.
RETIRED_EDUCATION_KEYS = ("higher_ed_public_enrollment",)


# Educação básica (ADR-23): escolas das redes públicas no Censo Escolar do INEP,
# pelo município da escola, por etapa. O Censo cobre todas as escolas: zero é medição.
_BASIC_SOURCE = (
    "Redes federal, estadual e municipal, pelo município da escola (Censo Escolar, "
    "INEP). Escolas privadas, inclusive as conveniadas com o poder público, não entram."
)
# Etapa → (como a escola a oferece, onde a matrícula está, faixa etária do
# atendimento). Pré-escola e fundamental não têm atendimento: a taxa bruta satura
# (em 2025, mediana municipal de 92% e 95%, um terço dos municípios acima de 100%,
# por alunos fora da idade e de municípios vizinhos) e não diferencia lugares.
BASIC_STAGES: dict[str, tuple[str, str, str | None]] = {
    "daycare": ("com creche", "em creche pública", "0 a 3 anos"),
    "preschool": ("com pré-escola", "na pré-escola pública", None),
    "elementary": ("com ensino fundamental", "no ensino fundamental público", None),
    "high_school": ("com ensino médio", "no ensino médio público", "15 a 17 anos"),
}


def basic_key(stage: str, metric: str) -> str:
    """`basic_schools`, `basic_enrollment` e `basic_<etapa>_<medida>`."""
    return f"basic_{metric}" if stage == "all" else f"basic_{stage}_{metric}"


def _basic(
    key: str,
    name: str,
    unit: str,
    scope: str,
    order: int,
    *,
    origin: IndicatorOrigin = IndicatorOrigin.SOURCED,
    decimal_places: int = 0,
) -> IndicatorSeed:
    return IndicatorSeed(
        key=key,
        name=name,
        unit=unit,
        description=f"{scope} {_BASIC_SOURCE}",
        origin=origin,
        decimal_places=decimal_places,
        display_order=order,
        context="education",
    )


def _stage_seeds(index: int, stage: str) -> tuple[IndicatorSeed, ...]:
    offer, place, ages = BASIC_STAGES[stage]
    order = 310 + 10 * index
    seeds = (
        _basic(
            basic_key(stage, "schools"),
            f"Escolas públicas {offer}",
            "schools",
            f"Escolas públicas {offer} (com matrícula na etapa) no ano.",
            order,
        ),
        _basic(
            basic_key(stage, "enrollment"),
            f"Matrículas {place}",
            "enrollments",
            f"Matrículas {place}.",
            order + 1,
        ),
    )
    if ages is None:
        return seeds
    coverage = _basic(
        basic_key(stage, "coverage"),
        # O nome vai no título da legenda: sem "público", que a descrição já diz.
        f"Atendimento {place.removesuffix(' pública').removesuffix(' público')} ({ages})",
        "%",
        (
            f"Taxa bruta: matrículas {place} divididas pela população de {ages} do Censo "
            "2022 (IBGE, tabela 9514). Só a partir de 2022: antes, a população do Censo não "
            "representa as crianças do ano. A matrícula conta onde fica a escola, não onde o "
            "aluno mora, e inclui alunos fora da idade; por isso um município pode passar "
            "de 100%."
        ),
        order + 2,
        origin=IndicatorOrigin.DERIVED,
        decimal_places=1,
    )
    return (*seeds, coverage)


BASIC_EDUCATION_CATALOG: tuple[IndicatorSeed, ...] = (
    _basic(
        "basic_schools",
        "Escolas públicas",
        "schools",
        "Escolas públicas com matrícula na educação básica no ano.",
        300,
    ),
    _basic(
        "basic_enrollment",
        "Matrículas na educação básica pública",
        "enrollments",
        "Matrículas da creche ao ensino médio, incluindo EJA e educação profissional.",
        301,
    ),
    *(seed for index, stage in enumerate(BASIC_STAGES) for seed in _stage_seeds(index, stage)),
)
# Chaves da primeira versão da educação básica, trocadas pelo padrão por etapa.
RETIRED_BASIC_KEYS = ("basic_daycares",)


async def seed_catalog(session: AsyncSession, catalog: tuple[IndicatorSeed, ...] = CATALOG) -> int:
    written = 0
    for seed in catalog:
        statement = (
            insert(Indicator)
            .values(
                key=seed.key,
                name=seed.name,
                description=seed.description,
                unit=seed.unit,
                origin=seed.origin,
                decimal_places=seed.decimal_places,
                display_order=seed.display_order,
                context=seed.context,
            )
            .on_conflict_do_update(
                index_elements=[Indicator.key],
                set_={
                    "name": seed.name,
                    "description": seed.description,
                    "unit": seed.unit,
                    "origin": seed.origin,
                    "decimal_places": seed.decimal_places,
                    "display_order": seed.display_order,
                    "context": seed.context,
                    "updated_at": datetime.now(UTC),
                },
            )
            .returning(Indicator.id)
        )
        await session.execute(statement)
        written += 1
    await session.commit()
    return written


async def main() -> int:
    async with job_session(job=JOB_NAME, source="brasil-lens") as (session, _, report):
        report.processed = len(CATALOG)
        report.written = await seed_catalog(session)
        report.details = {"indicators": [seed.key for seed in CATALOG]}
    print(f"Catálogo atualizado: {report.written} indicadores.")
    return 0


if __name__ == "__main__":
    run_job(main)

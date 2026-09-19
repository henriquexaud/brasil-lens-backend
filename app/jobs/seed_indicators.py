"""Popula o catálogo de indicadores.

`python -m app.jobs.seed_indicators`

Idempotente por UNIQUE(key): reexecutar atualiza nome/descrição/unidade do
indicador existente, nunca cria duplicata. Os *valores* nunca são tocados aqui.

**Adicionar um indicador novo é adicionar uma entrada nesta lista** (mais a
origem em `providers/ibge/datasets.py`, se ele vier de fonte externa).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.jobs._runner import job_session, run_job
from app.models import DataContext, Indicator, IndicatorOrigin

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
    # Todo indicador de hoje é sociopolítico — daí o default. Um indicador de
    # outro contexto (clima, biodiversidade) declara o seu explicitamente.
    context: DataContext = DataContext.SOCIOPOLITICAL


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
        # A fonte publica 3 casas, mas fração de km² não informa nada na tela.
        # O valor exato continua no banco; isto é só metadado de exibição.
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
    # --- Clima e meio ambiente ----------------------------------------------
    IndicatorSeed(
        key="disaster_affected_people",
        name="Pessoas afetadas por desastres",
        unit="people/100k",
        description=(
            "Mortes, desaparecidos e pessoas diretamente afetadas por desastres "
            "naturais, por 100 mil habitantes — Indicador ODS 11.5.1 (também "
            "vale para 1.5.1 e 13.1.1). Não existe abertura municipal na fonte: "
            "a tabela do IBGE só publica Brasil, região e UF."
        ),
        origin=IndicatorOrigin.SOURCED,
        decimal_places=1,
        display_order=100,
        context=DataContext.CLIMATE_ENVIRONMENTAL,
    ),
)


async def seed_catalog(session: AsyncSession) -> int:
    """Upsert do catálogo. Devolve quantas linhas foram escritas."""
    written = 0
    for seed in CATALOG:
        statement = (
            insert(Indicator)
            .values(
                key=seed.key,
                name=seed.name,
                description=seed.description,
                unit=seed.unit,
                origin=seed.origin,
                context=seed.context,
                decimal_places=seed.decimal_places,
                display_order=seed.display_order,
            )
            .on_conflict_do_update(
                index_elements=[Indicator.key],
                set_={
                    "name": seed.name,
                    "description": seed.description,
                    "unit": seed.unit,
                    "origin": seed.origin,
                    "context": seed.context,
                    "decimal_places": seed.decimal_places,
                    "display_order": seed.display_order,
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

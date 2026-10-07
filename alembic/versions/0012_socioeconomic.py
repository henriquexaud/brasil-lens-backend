from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_socioeconomic"
down_revision: str | None = "0011_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    origin = postgresql.ENUM("sourced", "derived", name="socioeconomic_origin", create_type=False)
    origin.create(op.get_bind())
    op.create_table(
        "socioeconomic_indicators",
        sa.Column("id", sa.SmallInteger(), sa.Identity(), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("unit", sa.String(24), nullable=False),
        sa.Column("origin", origin, nullable=False),
        sa.Column("decimal_places", sa.SmallInteger(), nullable=False),
        sa.Column("display_order", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "socioeconomic_values",
        sa.Column(
            "territory_id",
            sa.Integer(),
            sa.ForeignKey("territories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "indicator_id",
            sa.SmallInteger(),
            sa.ForeignKey("socioeconomic_indicators.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("reference_year", sa.SmallInteger(), primary_key=True),
        sa.Column("value", sa.Numeric(24, 6), nullable=False),
        sa.Column(
            "dataset_id",
            sa.SmallInteger(),
            sa.ForeignKey("datasets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ingestion_run_id",
            sa.BigInteger(),
            sa.ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("reference_year BETWEEN 1900 AND 2100", name="reference_year_range"),
    )
    op.create_index(
        "ix_socioeconomic_values_indicator_year",
        "socioeconomic_values",
        ["indicator_id", "reference_year"],
        postgresql_include=["territory_id", "value"],
    )
    op.create_index("ix_socioeconomic_values_dataset_id", "socioeconomic_values", ["dataset_id"])
    # O catálogo é pequeno e local; a migration não consulta o IBGE.

    table = sa.table(
        "socioeconomic_indicators",
        sa.column("key"),
        sa.column("name"),
        sa.column("description"),
        sa.column("unit"),
        sa.column("origin", origin),
        sa.column("decimal_places"),
        sa.column("display_order"),
    )
    op.bulk_insert(
        table,
        [
            {
                "key": "population",
                "name": "População",
                "unit": "people",
                "description": "População residente. Anos censitários vêm do Censo Demográfico (tabela 4714); os demais, das estimativas anuais (tabela 6579).",
                "origin": "sourced",
                "decimal_places": 0,
                "display_order": 10,
            },
            {
                "key": "population_growth",
                "name": "Crescimento populacional",
                "unit": "%/year",
                "description": "Variação média anual da população em relação ao ano anterior com dado publicado. Derivado na ingestão: como a série tem lacunas (2007, 2010, 2022, 2023), a taxa é anualizada geometricamente pelo intervalo real. Atenção aos anos censitários: em 2022 a taxa mede o Censo contra a estimativa de 2021, ou seja, carrega a revisão que o Censo fez da série — não um movimento demográfico.",
                "origin": "derived",
                "decimal_places": 2,
                "display_order": 15,
            },
            {
                "key": "area_km2",
                "name": "Área territorial",
                "unit": "km2",
                "description": "Área da unidade territorial, em quilômetros quadrados, conforme o IBGE. Possui ano de referência e é revisada — por isso é modelada como indicador com histórico, e não como atributo fixo do território.",
                "origin": "sourced",
                "decimal_places": 0,
                "display_order": 20,
            },
            {
                "key": "population_density",
                "name": "Densidade demográfica",
                "unit": "people/km2",
                "description": "População residente dividida pela área territorial. Derivado na ingestão para acompanhar toda a série de população. Quando o ano da população não tem área publicada, usa-se a área de referência mais recente até aquele ano.",
                "origin": "derived",
                "decimal_places": 2,
                "display_order": 30,
            },
            {
                "key": "gdp",
                "name": "PIB",
                "unit": "BRL",
                "description": "Produto Interno Bruto a preços correntes, em reais. A fonte publica em milhares de reais; a ingestão normaliza para a unidade base.",
                "origin": "sourced",
                "decimal_places": 0,
                "display_order": 40,
            },
            {
                "key": "gdp_per_capita",
                "name": "PIB per capita",
                "unit": "BRL",
                "description": "PIB dividido pela população. Necessariamente derivado: a tabela 5938 do IBGE não publica esta variável. Quando o ano do PIB não tem população publicada, usa-se a população de referência mais recente até aquele ano.",
                "origin": "derived",
                "decimal_places": 2,
                "display_order": 50,
            },
            {
                "key": "urban_population",
                "name": "População urbana",
                "unit": "people",
                "description": "População residente em situação urbana do domicílio, apurada no Censo Demográfico (tabela 9923 em 2022; tabela 202 em 2010). Existe apenas em anos censitários.",
                "origin": "sourced",
                "decimal_places": 0,
                "display_order": 32,
            },
            {
                "key": "urbanization_rate",
                "name": "Taxa de urbanização",
                "unit": "%",
                "description": "População urbana dividida pela população total. Derivado na ingestão e conferido contra o percentual que o próprio IBGE publica (tabela 9923, variável 1000093). Só existe em anos censitários, porque a população urbana só é apurada no Censo.",
                "origin": "derived",
                "decimal_places": 1,
                "display_order": 34,
            },
            {
                "key": "gdp_share_national",
                "name": "Participação no PIB nacional",
                "unit": "%",
                "description": "PIB do território dividido pelo PIB do Brasil no mesmo ano. Derivado na ingestão e conferido contra a variável 496 da tabela 5938, que o IBGE publica com esta mesma definição.",
                "origin": "derived",
                "decimal_places": 2,
                "display_order": 45,
            },
            {
                "key": "gdp_agriculture",
                "name": "PIB — Agropecuária",
                "unit": "BRL",
                "description": "Valor adicionado bruto da agropecuária a preços correntes (tabela 5938, variável 513). A fonte publica em milhares de reais; a ingestão normaliza para a unidade base.",
                "origin": "sourced",
                "decimal_places": 0,
                "display_order": 55,
            },
            {
                "key": "gdp_industry",
                "name": "PIB — Indústria",
                "unit": "BRL",
                "description": "Valor adicionado bruto da indústria a preços correntes (tabela 5938, variável 517). A fonte publica em milhares de reais; a ingestão normaliza para a unidade base.",
                "origin": "sourced",
                "decimal_places": 0,
                "display_order": 56,
            },
            {
                "key": "gdp_services",
                "name": "PIB — Serviços",
                "unit": "BRL",
                "description": "Valor adicionado bruto dos serviços a preços correntes, inclusive administração, defesa, educação e saúde públicas. A tabela 5938 publica as duas parcelas separadas (variáveis 6575 e 525) e a ingestão as soma, para que os três setores fechem o valor adicionado bruto total.",
                "origin": "sourced",
                "decimal_places": 0,
                "display_order": 57,
            },
            {
                "key": "household_income_per_capita",
                "name": "Renda domiciliar per capita",
                "unit": "BRL",
                "description": "Rendimento médio mensal real domiciliar per capita, da PNAD Contínua anual (tabela 7395, variável 4196). A pesquisa não desagrega este indicador por município: há dado para Brasil, regiões e UFs.",
                "origin": "sourced",
                "decimal_places": 0,
                "display_order": 60,
            },
            {
                "key": "unemployment_rate",
                "name": "Taxa de desemprego",
                "unit": "%",
                "description": "Taxa de desocupação das pessoas de 14 anos ou mais, da PNAD Contínua. Anos fechados usam a média anual oficial (tabela 4562); o ano em curso usa a média dos trimestres já publicados (tabela 6468). A pesquisa cobre Brasil, regiões e UFs — no nível municipal ela só apura as capitais.",
                "origin": "sourced",
                "decimal_places": 1,
                "display_order": 70,
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("socioeconomic_values")
    op.drop_table("socioeconomic_indicators")
    op.execute("DROP TYPE socioeconomic_origin")

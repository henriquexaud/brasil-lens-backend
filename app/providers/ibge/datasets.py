"""Registro declarativo: indicador do produto ↔ agregado do IBGE.

**Este arquivo é o ponto de extensão para "adicionar um indicador novo".**
Acrescentar uma entrada aqui (+ uma linha no catálogo em
`app/jobs/seed_indicators.py`) e rodar `python -m app.jobs.import_indicators`
basta — nenhum código de ingestão, modelo ou API muda.

Todos os códigos de tabela/variável abaixo foram verificados contra
`/api/v3/agregados/{tabela}/metadados` na API em produção.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# Níveis do IBGE que o produto consome hoje.
NATIONAL_LEVELS: tuple[str, ...] = ("N1", "N2", "N3")  # Brasil, região, UF
MUNICIPAL_LEVEL = "N6"


@dataclass(frozen=True, slots=True)
class SourcedIndicatorSpec:
    """Como obter um indicador a partir de um agregado do IBGE."""

    indicator_key: str
    table: str
    # Uma variável ("93") ou várias somadas ("6575|525"). Ver o provider.
    variable: str
    dataset_name: str
    # Multiplicador para levar o valor à unidade final do indicador.
    # Ex.: a tabela 5938 publica PIB em "Mil Reais" → 1000 converte para BRL.
    value_multiplier: Decimal = Decimal(1)
    periods: str = "all"
    # Níveis a coletar. Municipal é consultado UF por UF (ver ingestão).
    levels: tuple[str, ...] = (*NATIONAL_LEVELS, MUNICIPAL_LEVEL)
    # Recorte de classificação do IBGE, ex. "1[1]" = situação do domicílio /
    # Urbana. População urbana não é variável própria: é categoria da 93.
    classification: str | None = None
    notes: str = ""

    @property
    def dataset_code(self) -> str:
        """Identidade estável do dataset, usada em `datasets.code`.

        A classificação entra no código porque ela **muda o dado**: a tabela 202
        com a variável 93 devolve a população total ou a urbana conforme o
        recorte, e as duas precisam de proveniências distintas.
        """
        code = f"agregados/{self.table}/v/{self.variable}"
        return f"{code}/c/{self.classification}" if self.classification else code

    @property
    def dataset_url(self) -> str:
        return f"https://servicodados.ibge.gov.br/api/v3/agregados/{self.table}/metadados"

    @property
    def national_levels(self) -> tuple[str, ...]:
        return tuple(level for level in self.levels if level in NATIONAL_LEVELS)

    @property
    def includes_municipalities(self) -> bool:
        return MUNICIPAL_LEVEL in self.levels


# ---------------------------------------------------------------------------
# Indicadores derivados.
#
# São três formas de derivação, e cada uma existe porque um indicador pedido
# não existe na fonte nessa forma. Todas rodam na ingestão, em um único comando
# SQL — a razão está em docs/ARCHITECTURE.md §6: mantém um único caminho de
# leitura, permite índice sobre o resultado e concentra a regra temporal em um
# lugar só.
#
#   razão       A ÷ B     densidade, PIB per capita, taxa de urbanização
#   crescimento A(t)/A(t-1) crescimento populacional
#   participação A ÷ A(Brasil) participação no PIB nacional
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RatioIndicatorSpec:
    """Razão entre dois indicadores no mesmo território (A ÷ B × fator)."""

    indicator_key: str
    numerator_key: str
    denominator_key: str
    dataset_name: str
    factor: Decimal = Decimal(1)
    notes: str = ""

    @property
    def dataset_code(self) -> str:
        return f"derived/{self.indicator_key}"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return (self.numerator_key, self.denominator_key)


@dataclass(frozen=True, slots=True)
class GrowthIndicatorSpec:
    """Variação anual de um indicador em relação ao ano anterior com dado.

    A taxa é **geométrica anualizada**: a cobertura da população é irregular
    (não há 2007, 2010, 2022 nem 2023 nas estimativas), e uma diferença simples
    entre 2021 e 2024 devolveria o crescimento de três anos rotulado como se
    fosse de um.
    """

    indicator_key: str
    base_key: str
    dataset_name: str
    factor: Decimal = Decimal(100)
    notes: str = ""

    @property
    def dataset_code(self) -> str:
        return f"derived/{self.indicator_key}"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return (self.base_key,)


@dataclass(frozen=True, slots=True)
class ShareIndicatorSpec:
    """Participação do território no total nacional do mesmo indicador e ano."""

    indicator_key: str
    base_key: str
    dataset_name: str
    factor: Decimal = Decimal(100)
    notes: str = ""

    @property
    def dataset_code(self) -> str:
        return f"derived/{self.indicator_key}"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return (self.base_key,)


DerivedIndicatorSpec = RatioIndicatorSpec | GrowthIndicatorSpec | ShareIndicatorSpec


# ---------------------------------------------------------------------------
# Indicadores obtidos diretamente do IBGE.
#
# A ordem importa: o upsert é "último escreve", então uma fonte mais autoritativa
# para o mesmo (território, indicador, ano) deve aparecer DEPOIS. Hoje não há
# sobreposição (as estimativas não cobrem anos censitários), mas a ordem
# garante que o Censo prevaleça caso passe a haver.
# ---------------------------------------------------------------------------
# Verificado na fonte: a tabela 5938 publica o PIB total (v. 37) até 2023, mas
# a abertura por atividade econômica só até 2021 — de 2022 em diante os valores
# setoriais vêm como sentinela "...". Os anos sem dado simplesmente não nascem,
# e o catálogo expõe a cobertura real de cada indicador.
_SECTOR_COVERAGE = (
    "Série 2002-2021 nos mesmos níveis do PIB total. A abertura setorial fica "
    "dois anos atrás do PIB total, que vai até 2023."
)

SOURCED_INDICATORS: tuple[SourcedIndicatorSpec, ...] = (
    SourcedIndicatorSpec(
        indicator_key="population",
        table="6579",
        variable="9324",
        dataset_name="IBGE — População residente estimada (tabela 6579)",
        notes=(
            "Cobertura irregular e verificada: 2001-2006, 2008, 2009, 2011-2021, "
            "2024-2026. Anos censitários vêm da tabela 4714."
        ),
    ),
    SourcedIndicatorSpec(
        indicator_key="population",
        table="4714",
        variable="93",
        dataset_name="IBGE — População residente, Censo 2022 (tabela 4714)",
        notes="Fonte censitária: prevalece sobre a estimativa no mesmo ano.",
    ),
    SourcedIndicatorSpec(
        indicator_key="population",
        table="202",
        variable="93",
        periods="2010",
        dataset_name="IBGE — População residente, Censo 2010 (tabela 202)",
        notes=(
            "Fecha a lacuna de 2010: a tabela de estimativas (6579) não publica "
            "anos censitários. A tabela cobre 1970-2010 — trocar `periods` para "
            "'all' traz toda a série censitária."
        ),
    ),
    SourcedIndicatorSpec(
        indicator_key="area_km2",
        table="1301",
        variable="615",
        periods="2010",
        dataset_name="IBGE — Área total das unidades territoriais, Censo 2010 (tabela 1301)",
        notes="Permite que a densidade de 2010 use a área de 2010, não a de 2022.",
    ),
    SourcedIndicatorSpec(
        indicator_key="area_km2",
        table="4714",
        variable="6318",
        dataset_name="IBGE — Área da unidade territorial, Censo 2022 (tabela 4714)",
        notes="Área tem ano de referência e é revisada — por isso é indicador, não coluna.",
    ),
    SourcedIndicatorSpec(
        indicator_key="gdp",
        table="5938",
        variable="37",
        dataset_name="IBGE — Produto Interno Bruto a preços correntes (tabela 5938)",
        # A tabela publica em "Mil Reais"; normalizamos para reais.
        value_multiplier=Decimal(1000),
        notes=(
            "Série 2002-2023. A tabela NÃO possui variável per capita "
            "(46 variáveis verificadas)."
        ),
    ),
    # --- PIB por setor (mesma tabela 5938, valor adicionado bruto) ---------
    SourcedIndicatorSpec(
        indicator_key="gdp_agriculture",
        table="5938",
        variable="513",
        dataset_name="IBGE — Valor adicionado bruto da agropecuária (tabela 5938, v. 513)",
        value_multiplier=Decimal(1000),
        notes=_SECTOR_COVERAGE,
    ),
    SourcedIndicatorSpec(
        indicator_key="gdp_industry",
        table="5938",
        variable="517",
        dataset_name="IBGE — Valor adicionado bruto da indústria (tabela 5938, v. 517)",
        value_multiplier=Decimal(1000),
        notes=_SECTOR_COVERAGE,
    ),
    SourcedIndicatorSpec(
        indicator_key="gdp_services",
        # A 5938 separa serviços privados (6575) de administração, defesa,
        # educação e saúde públicas (525). O setor de serviços do recorte
        # clássico em três setores é a SOMA dos dois — e é por isso que o
        # provider soma as variáveis de uma mesma consulta.
        table="5938",
        variable="6575|525",
        dataset_name=(
            "IBGE — Valor adicionado bruto dos serviços, inclusive administração "
            "pública (tabela 5938, v. 6575 + 525)"
        ),
        value_multiplier=Decimal(1000),
        notes=(
            "A soma é feita na normalização: usar só a 6575 subestimaria os "
            "serviços em ~1/5 do VAB, e os três setores não fechariam o VAB "
            f"total. {_SECTOR_COVERAGE}"
        ),
    ),
    # --- População urbana (base da taxa de urbanização) --------------------
    SourcedIndicatorSpec(
        indicator_key="urban_population",
        table="202",
        variable="93",
        periods="2010",
        classification="1[1]",
        dataset_name=(
            "IBGE — População residente urbana, Censo 2010 (tabela 202, "
            "situação do domicílio: urbana)"
        ),
        notes=(
            "Mesma tabela/variável da população total de 2010: o que muda é o "
            "recorte de classificação. Por isso a classificação entra no "
            "`dataset_code`."
        ),
    ),
    SourcedIndicatorSpec(
        indicator_key="urban_population",
        table="9923",
        variable="93",
        classification="1[1]",
        dataset_name=(
            "IBGE — População residente urbana, Censo 2022 (tabela 9923, "
            "situação do domicílio: urbana)"
        ),
        notes="Dado censitário: existe só em 2022, com cobertura municipal completa.",
    ),
    # --- PNAD Contínua ----------------------------------------------------
    SourcedIndicatorSpec(
        indicator_key="household_income_per_capita",
        table="7395",
        variable="4196",
        levels=NATIONAL_LEVELS,
        dataset_name=(
            "IBGE — Rendimento médio mensal real domiciliar per capita, "
            "PNAD Contínua anual (tabela 7395, v. 4196)"
        ),
        notes=(
            "Série anual 2016-2025, em reais constantes. A PNAD Contínua NÃO "
            "publica este nível de desagregação para municípios: a tabela só "
            "oferece N1/N2/N3 (e recortes metropolitanos, fora do modelo)."
        ),
    ),
    SourcedIndicatorSpec(
        indicator_key="unemployment_rate",
        table="6468",
        variable="4099",
        levels=NATIONAL_LEVELS,
        dataset_name=(
            "IBGE — Taxa de desocupação (14 anos ou mais), PNAD Contínua "
            "trimestral (tabela 6468, v. 4099)"
        ),
        notes=(
            "Fonte TRIMESTRAL (2012T1 em diante). O provider reduz os trimestres "
            "do ano à média dos trimestres publicados, o que dá cobertura ao ano "
            "corrente — para os anos fechados, a média oficial da tabela 4562 "
            "prevalece (entrada seguinte). O nível municipal existe na tabela, "
            "mas só para as capitais (verificado: N6[N3[35]] devolve 1 município "
            "de 645) — pintar um mapa municipal com isso seria enganoso, então o "
            "recorte para em N3."
        ),
    ),
    SourcedIndicatorSpec(
        indicator_key="unemployment_rate",
        table="4562",
        variable="4099",
        levels=NATIONAL_LEVELS,
        dataset_name=(
            "IBGE — Taxa de desocupação (14 anos ou mais), PNAD Contínua anual "
            "(tabela 4562, v. 4099)"
        ),
        notes=(
            "Vem DEPOIS da trimestral de propósito: para um ano fechado, a média "
            "anual oficial do IBGE não é a média das quatro taxas trimestrais "
            "(ela é calculada sobre a amostra anual). Verificado em 2024: "
            "oficial 6,6%, média dos trimestres 6,85%. A trimestral continua "
            "cobrindo o ano em curso, que a tabela anual ainda não publicou."
        ),
    ),
)

# A ordem é a de execução: um derivado pode depender de outro derivado.
DERIVED_INDICATORS: tuple[DerivedIndicatorSpec, ...] = (
    RatioIndicatorSpec(
        indicator_key="population_density",
        numerator_key="population",
        denominator_key="area_km2",
        dataset_name="Brasil Lens — densidade demográfica derivada (população ÷ área)",
        notes=(
            "Derivado em vez de importado da variável 614 (que existe só para 2022) "
            "para que a densidade acompanhe toda a série de população."
        ),
    ),
    RatioIndicatorSpec(
        indicator_key="gdp_per_capita",
        numerator_key="gdp",
        denominator_key="population",
        dataset_name="Brasil Lens — PIB per capita derivado (PIB ÷ população)",
        notes="Obrigatoriamente derivado: a tabela 5938 não publica PIB per capita.",
    ),
    RatioIndicatorSpec(
        indicator_key="urbanization_rate",
        numerator_key="urban_population",
        denominator_key="population",
        factor=Decimal(100),
        dataset_name=("Brasil Lens — taxa de urbanização derivada (população urbana ÷ população)"),
        notes=(
            "O numerador é censitário (2010 e 2022), então o indicador só existe "
            "nesses anos — é o numerador que decide quais linhas nascem. "
            "Conferido contra o percentual publicado pelo IBGE (v. 1000093)."
        ),
    ),
    GrowthIndicatorSpec(
        indicator_key="population_growth",
        base_key="population",
        dataset_name=(
            "Brasil Lens — crescimento populacional derivado (variação anual "
            "geométrica da população)"
        ),
        notes=(
            "Comparado com o ano anterior COM DADO, não com o ano anterior no "
            "calendário: a série de população tem lacunas (2007, 2010, 2022, "
            "2023). A anualização geométrica mantém a unidade '% ao ano' honesta "
            "quando o intervalo é maior que um ano."
        ),
    ),
    ShareIndicatorSpec(
        indicator_key="gdp_share_national",
        base_key="gdp",
        dataset_name="Brasil Lens — participação no PIB nacional derivada (PIB ÷ PIB do Brasil)",
        notes=(
            "Exige o PIB do Brasil no MESMO ano — participação contra outro ano "
            "não significa nada. Conferido contra a variável 496 da tabela 5938, "
            "que o IBGE publica com esta definição."
        ),
    ),
)

# Garante que todo indicador derivado referencie chaves conhecidas.
_SOURCED_KEYS = {spec.indicator_key for spec in SOURCED_INDICATORS}
_DERIVED_KEYS = {spec.indicator_key for spec in DERIVED_INDICATORS}
for _spec in DERIVED_INDICATORS:
    for _dependency in _spec.dependencies:
        if _dependency not in _SOURCED_KEYS | _DERIVED_KEYS:
            raise RuntimeError(
                f"Indicador derivado '{_spec.indicator_key}' depende de "
                f"'{_dependency}', que não está no registro."
            )

__all__ = [
    "DERIVED_INDICATORS",
    "MUNICIPAL_LEVEL",
    "NATIONAL_LEVELS",
    "SOURCED_INDICATORS",
    "DerivedIndicatorSpec",
    "GrowthIndicatorSpec",
    "RatioIndicatorSpec",
    "ShareIndicatorSpec",
    "SourcedIndicatorSpec",
]

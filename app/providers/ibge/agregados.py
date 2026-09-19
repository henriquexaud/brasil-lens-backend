"""Provider da API de Agregados v3 do IBGE (o acervo do SIDRA).

`https://servicodados.ibge.gov.br/api/v3/agregados`

**Por que não `apisidra.ibge.gov.br`:** o endpoint clássico do SIDRA
(`/values/t/{t}/n{n}/...`) responde **HTTP 403** — está atrás de proteção
anti-bot. A API de agregados v3 responde 200, devolve JSON tipado (variável,
unidade, série por localidade) e aceita recorte territorial hierárquico
(`N6[N3[35]]` = municípios de SP), o que é exatamente o que a ingestão precisa.

Armadilhas reais, tratadas explicitamente aqui:

1. **Colisão de ids entre níveis.** No nível N1 o Brasil vem com
   ``localidade.id = "1"`` — o mesmo id da região Norte em N2. Mapear o id sem
   olhar o nível atribuiria a população do Brasil ao Norte. Por isso a tradução
   sempre considera o par ``(nivel, id)``.
2. **Sentinelas de indisponibilidade.** A série traz ``"..."``, ``"-"``, ``".."``
   e ``"X"`` para dado inexistente/sigiloso. Estes são **descartados** — não
   viram zero nem linha com valor nulo.
3. **Períodos sub-anuais.** O destino é ``indicator_values``, cuja chave é
   ``(território, indicador, ano)``. Uma tabela trimestral (6468, taxa de
   desocupação) devolve 4 períodos por ano: entregá-los crus faria o upsert
   tentar afetar a mesma linha duas vezes — erro do PostgreSQL, não valor
   errado. A redução para ano acontece **aqui**, na normalização.
4. **Variáveis complementares.** Uma consulta pode pedir mais de uma variável
   (``.../variaveis/6575|525``); as variáveis da mesma consulta são **somadas**
   por (território, período). É assim que o setor de serviços junta serviços
   privados e administração pública, que a tabela 5938 publica separados.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.models import TerritoryLevel
from app.providers.base import get_json
from app.providers.ibge.localidades import COUNTRY_CODE
from app.providers.records import IndicatorObservation

logger = get_logger(__name__)

SOURCE = "ibge"
BASE_URL = "https://servicodados.ibge.gov.br/api/v3/agregados"

# Níveis territoriais do IBGE ("NIT") relevantes para o produto.
SidraLevel = Literal["N1", "N2", "N3", "N6"]

LEVEL_BY_SIDRA: dict[str, TerritoryLevel] = {
    "N1": TerritoryLevel.COUNTRY,
    "N2": TerritoryLevel.REGION,
    "N3": TerritoryLevel.STATE,
    "N6": TerritoryLevel.MUNICIPALITY,
}
SIDRA_BY_LEVEL: dict[TerritoryLevel, str] = {v: k for k, v in LEVEL_BY_SIDRA.items()}

# Valores que a API usa para "sem dado" / "não se aplica" / "sigiloso".
UNAVAILABLE_TOKENS = frozenset({"...", "..", "-", "X", "", "*"})

# Mesma escala de `indicator_values.value`: a média anual de uma série
# trimestral não pode carregar mais precisão do que a coluna guarda.
_VALUE_SCALE = Decimal("0.000001")


class _Locality(BaseModel):
    id: str
    nome: str
    nivel: _Level


class _Level(BaseModel):
    id: str
    nome: str


class _Series(BaseModel):
    localidade: _Locality
    serie: dict[str, str]


class _Result(BaseModel):
    series: list[_Series]


class _Variable(BaseModel):
    id: str
    variavel: str
    unidade: str
    resultados: list[_Result]


_Locality.model_rebuild()


class AggregateQuery(BaseModel):
    """Parâmetros de uma consulta a um agregado do IBGE."""

    table: str = Field(description="Código do agregado, ex. '4714'")
    variable: str = Field(
        description="Código da variável, ex. '93'. Várias ('6575|525') são somadas."
    )
    periods: str = Field(default="all", description="'all', '2022' ou '2021|2022'")
    # Recorte de classificação, ex. "1[1]" = situação do domicílio / Urbana.
    # Sem isto não há como obter população urbana: ela não é uma variável
    # própria, e sim uma categoria da variável 93.
    classification: str | None = Field(default=None, description="Ex.: '1[1]' (Urbana)")

    @property
    def params(self) -> dict[str, str]:
        params = {}
        if self.classification:
            params["classificacao"] = self.classification
        return params


def territory_code(sidra_level: str, locality_id: str) -> str:
    """Traduz `(nível, id)` do IBGE para o código canônico usado no nosso modelo.

    O nível **precisa** entrar na decisão: N1/"1" é o Brasil, N2/"1" é o Norte.
    """
    if sidra_level == "N1":
        return COUNTRY_CODE
    return locality_id


def parse_value(raw: str, multiplier: Decimal) -> Decimal | None:
    """Converte o valor textual da API, ou devolve `None` se indisponível."""
    token = raw.strip()
    if token in UNAVAILABLE_TOKENS:
        return None
    try:
        parsed = Decimal(token)
    except InvalidOperation:
        return None
    return parsed * multiplier


def reference_year(period: str) -> int | None:
    """Ano de referência de um período do IBGE.

    Períodos anuais são `"2022"`; trimestrais, `"202203"`; mensais, `"202203"`
    também. Em todos os casos o ano são os quatro primeiros dígitos.
    """
    try:
        return int(period[:4])
    except ValueError:
        return None


def _annual_mean(values: list[Decimal]) -> Decimal:
    """Média dos períodos publicados no ano, na escala da coluna de valor.

    Para tabela anual há um único período e a média é o próprio valor. Para a
    trimestral é a **média anual** — a mesma convenção que o IBGE usa ao
    publicar a taxa de desocupação média do ano. Um ano ainda incompleto (2026
    tem apenas 2 trimestres publicados) produz a média do que existe; o dado
    aparece em vez de sumir, e a nota do indicador registra a regra.
    """
    if len(values) == 1:
        return values[0]
    return (sum(values, Decimal(0)) / len(values)).quantize(_VALUE_SCALE)


def parse_observations(
    payload: list[dict[str, object]],
    *,
    multiplier: Decimal = Decimal(1),
    accept_levels: frozenset[str] | None = None,
) -> tuple[list[IndicatorObservation], int]:
    """Normaliza a resposta da API em observações internas.

    Três reduções, nesta ordem:

    1. **soma entre variáveis** da mesma consulta, por (território, período) —
       serviços privados + administração pública = setor de serviços;
    2. **média entre períodos** do mesmo ano — trimestral vira anual, e a chave
       `(território, indicador, ano)` do destino nunca recebe duplicata;
    3. multiplicador da fonte (``Mil Reais`` → BRL), aplicado no valor bruto.

    Devolve `(observações, descartadas)` — a contagem de descartes alimenta
    `records_failed` no registro de ingestão, para que "veio pouco dado" seja
    visível em vez de silencioso.
    """
    if not payload:
        raise ProviderError("IBGE Agregados devolveu lista vazia de variáveis.")

    discarded = 0
    # (código, período) -> parcelas vindas de cada variável da consulta.
    parts: dict[tuple[str, str], list[Decimal]] = {}
    expected_parts = 0

    for raw_variable in payload:
        variable = _Variable.model_validate(raw_variable)
        for result in variable.resultados:
            expected_parts += 1
            for series in result.series:
                level_id = series.localidade.nivel.id
                if accept_levels is not None and level_id not in accept_levels:
                    continue
                code = territory_code(level_id, series.localidade.id)
                for period, raw_value in series.serie.items():
                    value = parse_value(raw_value, multiplier)
                    if value is None or reference_year(period) is None:
                        discarded += 1
                        continue
                    parts.setdefault((code, period), []).append(value)

    # (código, ano) -> um valor por período já somado entre variáveis.
    by_year: dict[tuple[str, int], list[Decimal]] = {}
    for (code, period), values in parts.items():
        if len(values) < expected_parts:
            # Soma parcial é pior que ausência: entregaria "serviços" sem a
            # administração pública com a mesma cara de um valor completo.
            discarded += len(values)
            continue
        year = reference_year(period)
        assert year is not None  # garantido no laço acima
        by_year.setdefault((code, year), []).append(sum(values, Decimal(0)))

    observations = [
        IndicatorObservation(
            ibge_code=code,
            reference_year=year,
            value=_annual_mean(values),
        )
        for (code, year), values in sorted(by_year.items())
    ]
    return observations, discarded


async def fetch_observations(
    client: httpx.AsyncClient,
    query: AggregateQuery,
    *,
    localities: str,
    multiplier: Decimal = Decimal(1),
    accept_levels: frozenset[str] | None = None,
) -> tuple[list[IndicatorObservation], int]:
    """Consulta um agregado e devolve observações já normalizadas.

    `localities` usa a sintaxe do IBGE: `"N3[all]"`, `"N1[all]|N2[all]|N3[all]"`,
    `"N6[N3[35]]"` (municípios de SP).
    """
    path = f"/api/v3/agregados/{query.table}/periodos/{query.periods}/variaveis/{query.variable}"
    payload = await get_json(
        client,
        path,
        params={"localidades": localities, **query.params},
        source="IBGE Agregados",
    )
    if not isinstance(payload, list):
        raise ProviderError("IBGE Agregados devolveu payload inesperado.", path=path)

    observations, discarded = parse_observations(
        payload,
        multiplier=multiplier,
        accept_levels=accept_levels,
    )
    logger.info(
        "agregados.fetched",
        extra={
            "table": query.table,
            "variable": query.variable,
            "localities": localities,
            "observations": len(observations),
            "discarded": discarded,
        },
    )
    return observations, discarded


async def fetch_available_periods(client: httpx.AsyncClient, table: str) -> list[str]:
    """Períodos publicados de um agregado.

    Útil porque a cobertura é irregular: a tabela 6579 publica 2001-2006, 2008,
    2009, 2011-2021 e 2024-2026 — sem 2007, 2010, 2022 e 2023.
    """
    payload = await get_json(
        client,
        f"/api/v3/agregados/{table}/periodos",
        source="IBGE Agregados",
    )
    if not isinstance(payload, list):
        raise ProviderError("IBGE Agregados devolveu períodos inesperados.", table=table)
    return [str(item["id"]) for item in payload if isinstance(item, dict) and "id" in item]

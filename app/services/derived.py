"""Derivação de indicadores durante a ingestão.

Três formas de derivação, cada uma porque a fonte não publica o indicador
naquela forma:

| Forma | Fórmula | Indicadores |
|---|---|---|
| razão | A ÷ B × fator | `population_density`, `gdp_per_capita`, `urbanization_rate` |
| crescimento | (A(t)/A(t−Δ))^(1/Δ) − 1 | `population_growth` |
| participação | A ÷ A(Brasil) × 100 | `gdp_share_national` |

Por que derivar na ingestão e persistir, em vez de calcular por requisição:

* mantém **um único caminho de leitura** — mapa e overview não sabem que o
  indicador é derivado, e não há `if` especial em nenhuma query;
* permite que o resultado participe de índice, ordenação e classificação
  exatamente como os indicadores importados;
* concentra as regras temporais (abaixo) em um lugar só.

**Regra temporal da razão.** Numerador e denominador raramente têm os mesmos
anos: a população tem série anual, a área territorial foi publicada em 2022.
Para o ano *Y*, o denominador usado é o de maior `reference_year` ≤ *Y*; se não
existir nenhum, usa-se o mais próximo acima (que é também o mais antigo
disponível). Sem essa regra, `population_density(2019)` ficaria sem valor só
porque a área tem ano de referência 2022.

**Regra temporal do crescimento.** O ano anterior é o anterior *com dado*, e a
taxa é anualizada geometricamente pelo intervalo real. A série de população tem
buracos (2007, 2010, 2022, 2023); sem anualizar, o salto de 2021 para 2024
apareceria como crescimento de um ano só.

**Regra temporal da participação.** É o mesmo ano, sem tolerância: participação
no PIB nacional de um ano contra o total de outro não significa nada. Se o valor
do Brasil não existe naquele ano, a linha não nasce.

Toda a derivação é **um único comando SQL** por indicador: é conjunto de dados,
não laço de aplicação, e o upsert a torna idempotente.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.results import affected_rows
from app.providers.ibge.datasets import (
    DerivedIndicatorSpec,
    GrowthIndicatorSpec,
    RatioIndicatorSpec,
    ShareIndicatorSpec,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DerivationResult:
    indicator_key: str
    rows_written: int


# Cauda comum aos três comandos. O upsert sobre a PK natural é o que torna a
# derivação idempotente; o WHERE no DO UPDATE evita reescrever linha idêntica,
# mantendo `updated_at` com o significado de "quando o valor mudou".
_UPSERT_TAIL = """
    ON CONFLICT (territory_id, indicator_id, reference_year) DO UPDATE
       SET value            = EXCLUDED.value,
           dataset_id       = EXCLUDED.dataset_id,
           ingestion_run_id = EXCLUDED.ingestion_run_id,
           updated_at       = now()
     WHERE target.value IS DISTINCT FROM EXCLUDED.value
        OR target.dataset_id IS DISTINCT FROM EXCLUDED.dataset_id
"""

_INSERT_HEAD = """
    INSERT INTO indicator_values AS target (
        territory_id, indicator_id, reference_year, value,
        dataset_id, ingestion_run_id, created_at, updated_at
    )
"""

# CROSS JOIN LATERAL (e não subquery correlacionada sobre CTE) para que o
# denominador seja buscado pela PK (territory_id, indicator_id, reference_year):
# cada execução do lateral lê pouquíssimas linhas por índice.
#
# O ORDER BY implementa a regra temporal: primeiro os anos ≤ Y, depois o mais
# próximo — o que resolve "mais recente ≤ Y, senão o mais antigo disponível".
_RATIO_SQL = text(
    _INSERT_HEAD
    + """
    SELECT numerator.territory_id,
           :target_indicator_id,
           numerator.reference_year,
           ROUND(numerator.value / denominator.value * :factor, 6),
           :dataset_id,
           :ingestion_run_id,
           now(),
           now()
      FROM indicator_values numerator
      CROSS JOIN LATERAL (
          SELECT candidate.value
            FROM indicator_values candidate
           WHERE candidate.territory_id = numerator.territory_id
             AND candidate.indicator_id = :denominator_indicator_id
           ORDER BY CASE
                        WHEN candidate.reference_year <= numerator.reference_year THEN 0
                        ELSE 1
                    END,
                    ABS(candidate.reference_year - numerator.reference_year)
           LIMIT 1
      ) AS denominator
     WHERE numerator.indicator_id = :numerator_indicator_id
       AND denominator.value <> 0
    """
    + _UPSERT_TAIL
)

# O lateral busca o ano anterior COM DADO (não o ano anterior do calendário) e
# a fórmula anualiza pelo intervalo real: (Vt/V0)^(1/Δ) − 1. Com Δ = 1 ela é a
# variação simples; com Δ = 3 (2021 → 2024) devolve a taxa média anual, que é o
# que a unidade "% ao ano" promete.
_GROWTH_SQL = text(
    _INSERT_HEAD
    + """
    SELECT observation.territory_id,
           :target_indicator_id,
           observation.reference_year,
           ROUND(
               (POWER(
                    observation.value / previous.value,
                    1.0 / (observation.reference_year - previous.reference_year)
                ) - 1) * :factor,
               6
           ),
           :dataset_id,
           :ingestion_run_id,
           now(),
           now()
      FROM indicator_values observation
      CROSS JOIN LATERAL (
          SELECT candidate.value, candidate.reference_year
            FROM indicator_values candidate
           WHERE candidate.territory_id = observation.territory_id
             AND candidate.indicator_id = :base_indicator_id
             AND candidate.reference_year < observation.reference_year
           ORDER BY candidate.reference_year DESC
           LIMIT 1
      ) AS previous
     WHERE observation.indicator_id = :base_indicator_id
       AND previous.value > 0
    """
    + _UPSERT_TAIL
)

# Participação exige o total nacional do MESMO ano — daí o JOIN por
# reference_year, sem a tolerância temporal da razão. O país é único
# (level='country'), então o JOIN não multiplica linhas.
_SHARE_SQL = text(
    _INSERT_HEAD
    + """
    SELECT part.territory_id,
           :target_indicator_id,
           part.reference_year,
           ROUND(part.value / national.value * :factor, 6),
           :dataset_id,
           :ingestion_run_id,
           now(),
           now()
      FROM indicator_values part
      JOIN territories country
        ON country.level = CAST('country' AS territory_level)
      JOIN indicator_values national
        ON national.territory_id  = country.id
       AND national.indicator_id  = :base_indicator_id
       AND national.reference_year = part.reference_year
     WHERE part.indicator_id = :base_indicator_id
       AND national.value <> 0
    """
    + _UPSERT_TAIL
)


async def derive(
    session: AsyncSession,
    spec: DerivedIndicatorSpec,
    *,
    indicator_ids: dict[str, int],
    dataset_id: int,
    ingestion_run_id: int | None = None,
) -> DerivationResult:
    """Executa a derivação correspondente ao tipo do `spec`.

    `indicator_ids` traz o catálogo já resolvido (chave → id); o job garante
    que todas as dependências estão lá antes de chamar.
    """
    parameters: dict[str, object] = {
        "target_indicator_id": indicator_ids[spec.indicator_key],
        "dataset_id": dataset_id,
        "ingestion_run_id": ingestion_run_id,
        "factor": spec.factor,
    }

    if isinstance(spec, RatioIndicatorSpec):
        statement = _RATIO_SQL
        parameters["numerator_indicator_id"] = indicator_ids[spec.numerator_key]
        parameters["denominator_indicator_id"] = indicator_ids[spec.denominator_key]
    elif isinstance(spec, GrowthIndicatorSpec):
        statement = _GROWTH_SQL
        parameters["base_indicator_id"] = indicator_ids[spec.base_key]
    elif isinstance(spec, ShareIndicatorSpec):
        statement = _SHARE_SQL
        parameters["base_indicator_id"] = indicator_ids[spec.base_key]
    else:  # pragma: no cover - a união de tipos torna isto inalcançável
        raise TypeError(f"derivação não suportada: {type(spec).__name__}")

    result = await session.execute(statement, parameters)
    written = affected_rows(result)
    logger.info(
        "derived.computed",
        extra={
            "indicator": spec.indicator_key,
            "kind": type(spec).__name__,
            "depends_on": list(spec.dependencies),
            "rows_written": written,
        },
    )
    return DerivationResult(indicator_key=spec.indicator_key, rows_written=written)


# ---------------------------------------------------------------------------
# Espelhos das fórmulas em Python.
#
# Existem para que os testes comparem o cálculo com números oficiais do IBGE
# sem precisar de banco. Se uma fórmula mudar no SQL sem mudar aqui, o teste que
# compara com o valor publicado pelo IBGE é quem acusa.
# ---------------------------------------------------------------------------
_SCALE = Decimal("0.000001")


def expected_value(
    numerator: Decimal,
    denominator: Decimal,
    factor: Decimal = Decimal(1),
) -> Decimal:
    """Razão: mesma fórmula de `_RATIO_SQL`."""
    return (numerator / denominator * factor).quantize(_SCALE)


def expected_growth(
    value: Decimal,
    previous_value: Decimal,
    *,
    years: int,
    factor: Decimal = Decimal(100),
) -> Decimal:
    """Crescimento anualizado: mesma fórmula de `_GROWTH_SQL`."""
    if years <= 0:
        raise ValueError("o intervalo entre os anos precisa ser positivo")
    ratio = value / previous_value
    annualized = ratio ** (Decimal(1) / Decimal(years))
    return ((annualized - 1) * factor).quantize(_SCALE)


def expected_share(
    value: Decimal,
    national_value: Decimal,
    factor: Decimal = Decimal(100),
) -> Decimal:
    """Participação no total nacional: mesma fórmula de `_SHARE_SQL`."""
    return (value / national_value * factor).quantize(_SCALE)

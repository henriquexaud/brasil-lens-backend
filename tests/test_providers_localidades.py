"""Normalização do provider de Localidades.

As fixtures são recortes das respostas reais da API (capturadas em produção),
incluindo o caso que quebraria uma implementação ingênua.
"""

import pytest

from app.core.errors import ProviderError
from app.models import TerritoryLevel
from app.providers.ibge.localidades import (
    COUNTRY_CODE,
    _RawMunicipality,
    _RawState,
    country_record,
)
from tests.conftest import load_fixture


def test_estado_normaliza_codigo_sigla_e_regiao_pai() -> None:
    raw = [_RawState.model_validate(item) for item in load_fixture("localidades_estados.json")]
    by_code = {str(state.id): state for state in raw}

    assert by_code["35"].nome == "São Paulo"
    assert by_code["35"].sigla == "SP"
    # O pai de uma UF é a grande região — e vem no mesmo payload.
    assert str(by_code["35"].regiao.id) == "3"
    assert by_code["53"].nome == "Distrito Federal"


def test_municipio_resolve_uf_pela_microrregiao() -> None:
    municipalities = {
        str(item["id"]): _RawMunicipality.model_validate(item)
        for item in load_fixture("localidades_municipios.json")
    }

    assert municipalities["3550308"].state_code() == "35"
    assert municipalities["3100104"].state_code() == "31"
    assert municipalities["5300108"].state_code() == "53"


def test_municipio_novo_sem_microrregiao_usa_regiao_imediata() -> None:
    """Caso real: "Boa Esperança do Norte" (5101837) tem `microrregiao: null`.

    O IBGE expõe a UF por dois caminhos (divisão antiga e nova) e municípios
    recém-criados só trazem o novo. Sem o fallback, a ingestão quebraria em um
    único município de 5.571.
    """
    payload = {str(item["id"]): item for item in load_fixture("localidades_municipios.json")}[
        "5101837"
    ]

    assert payload["microrregiao"] is None
    assert _RawMunicipality.model_validate(payload).state_code() == "51"


def test_municipio_sem_nenhum_caminho_de_uf_falha_alto() -> None:
    """Resposta fora do contrato não pode virar território órfão silencioso."""
    municipality = _RawMunicipality.model_validate(
        {"id": 9999999, "nome": "Inexistente", "microrregiao": None}
    )

    with pytest.raises(ProviderError):
        municipality.state_code()


def test_pais_e_a_raiz_da_hierarquia() -> None:
    record = country_record()

    assert record.ibge_code == COUNTRY_CODE == "BR"
    assert record.level is TerritoryLevel.COUNTRY
    # A raiz não tem pai — é o que o CHECK `country_is_root` garante no banco.
    assert record.parent_ibge_code is None

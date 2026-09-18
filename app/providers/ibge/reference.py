"""Dados de referência estáticos do IBGE.

Aqui só entra informação **administrativa estável que a API do IBGE não expõe**.
Hoje é apenas o mapa UF → município-capital: a API Localidades não marca capitais
em nenhum endpoint, e capitais mudam por decisão constitucional, não por revisão
de dados.

Os códigos abaixo foram extraídos da própria lista oficial de municípios
(`/api/v1/localidades/municipios`), casando nome da capital dentro da UF, e a
ingestão **valida** cada um contra os municípios importados: um código que deixe
de existir derruba o job em vez de gravar FK errada.
"""

from __future__ import annotations

# UF (código IBGE) → município-capital (código IBGE)
STATE_CAPITALS: dict[str, str] = {
    "11": "1100205",  # Rondônia → Porto Velho
    "12": "1200401",  # Acre → Rio Branco
    "13": "1302603",  # Amazonas → Manaus
    "14": "1400100",  # Roraima → Boa Vista
    "15": "1501402",  # Pará → Belém
    "16": "1600303",  # Amapá → Macapá
    "17": "1721000",  # Tocantins → Palmas
    "21": "2111300",  # Maranhão → São Luís
    "22": "2211001",  # Piauí → Teresina
    "23": "2304400",  # Ceará → Fortaleza
    "24": "2408102",  # Rio Grande do Norte → Natal
    "25": "2507507",  # Paraíba → João Pessoa
    "26": "2611606",  # Pernambuco → Recife
    "27": "2704302",  # Alagoas → Maceió
    "28": "2800308",  # Sergipe → Aracaju
    "29": "2927408",  # Bahia → Salvador
    "31": "3106200",  # Minas Gerais → Belo Horizonte
    "32": "3205309",  # Espírito Santo → Vitória
    "33": "3304557",  # Rio de Janeiro → Rio de Janeiro
    "35": "3550308",  # São Paulo → São Paulo
    "41": "4106902",  # Paraná → Curitiba
    "42": "4205407",  # Santa Catarina → Florianópolis
    "43": "4314902",  # Rio Grande do Sul → Porto Alegre
    "50": "5002704",  # Mato Grosso do Sul → Campo Grande
    "51": "5103403",  # Mato Grosso → Cuiabá
    "52": "5208707",  # Goiás → Goiânia
    "53": "5300108",  # Distrito Federal → Brasília
}

# Capital federal, associada ao nível país.
COUNTRY_CAPITAL = "5300108"  # Brasília

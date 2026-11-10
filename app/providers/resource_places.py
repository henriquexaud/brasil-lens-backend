"""Recursos: lavras autorizadas (SIGMINE/ANM), ocorrências minerais (GeoSGB) e campos de
petróleo e gás (ANP), lidos dos serviços geográficos oficiais na ingestão.
"""

from __future__ import annotations

import ssl
from pathlib import Path
from typing import Any

import certifi

from app.core.text import normalize_text, title_case
from app.providers.place_record import PlaceRecord, area_label, clean, number, pairs

ANM_URL = "https://geo.anm.gov.br/arcgis/rest/services/SIGMINE/dados_anm/MapServer/0/query"
ANM_DATASET_URL = (
    "https://geo.anm.gov.br/portal/apps/webappviewer/index.html?id=6a8f5ccc4b6a4c2bba79759aa952d908"
)
ANM_FIELDS = "PROCESSO,FASE,NOME,SUBS,USO,AREA_HA,UF,ANO"
# Só as fases em que a extração está autorizada; requerimentos e pesquisa são intenção.
ANM_PHASES = {
    "CONCESSÃO DE LAVRA": "Concessão de lavra",
    "LAVRA GARIMPEIRA": "Lavra garimpeira",
    "LICENCIAMENTO": "Licenciamento",
    "REGISTRO DE EXTRAÇÃO": "Registro de extração",
}
# O servidor da ANM não envia o certificado intermediário da Sectigo (o navegador o
# busca pelo AIA; o Python, não). A cadeia continua validada até a raiz do certifi;
# o intermediário vale até 2036-03-21.
_ANM_INTERMEDIATE = (
    Path(__file__).parent / "certs" / "sectigo-public-server-authentication-ca-ov-r36.pem"
)


def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cafile=_ANM_INTERMEDIATE)
    return context


ANM_WHERE = "FASE IN ({})".format(", ".join(f"'{phase}'" for phase in ANM_PHASES))

SGB_URL = "https://geoportal.sgb.gov.br/server/rest/services/geologia/ocorrencias/MapServer/0/query"
SGB_DATASET_URL = "https://geosgb.sgb.gov.br"
SGB_FIELDS = (
    "ID_OCORRENCIA,TOPONIMIA,STATUS_ECONOMICO,IMPORTANCIA,SITUACAO_MINA,SITUACAO_GARIMPO,"
    "SUBSTANCIAS,CLASSES_UTILITARIAS,PROVINCIA"
)

ANP_URL = "https://gishub.anp.gov.br/geoserver/ows"
ANP_LAYER = "BD_ANP:CAMPOS_PRODUCAO_SIRGAS"
ANP_DATASET_URL = (
    "https://www.gov.br/anp/pt-br/assuntos/exploracao-e-producao-de-oleo-e-gas/dados-tecnicos"
)

# A primeira regra que casa decide o grupo; a ordem resolve nomes compostos
# ("água marinha" é gema, "granito p/ brita" é agregado).
_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rare_earths", ("terras raras", "cerio", "lantanio", "monazita", "xenotima", "itrio")),
    ("lithium", ("litio", "espodumenio", "petalita", "lepidolita", "ambligonita")),
    ("niobium", ("niobio", "tantalo", "tantalita", "columbita", "pirocloro")),
    (
        "gems",
        (
            "diamant",
            "esmeralda",
            "ametista",
            "agua marinha",
            "topazio",
            "turmalina",
            "opala",
            "alexandrita",
            "gema",
            "pedra corada",
            "agata",
            "citrino",
            "granada",
            "crisoberilo",
            "kunzita",
            "quartzo rosa",
        ),
    ),
    ("water", ("agua mineral", "agua potavel", "aguas termais", "agua termal", "oligominerais")),
    ("gold", ("ouro", "aurifero")),
    ("iron", ("ferro", "hematita", "itabirito", "magnetita", "canga", "limonita")),
    ("copper", ("cobre",)),
    ("aluminum", ("bauxita", "aluminio", "gibbsita", "hidrargilita")),
    ("manganese", ("manganes",)),
    ("nickel", ("niquel",)),
    ("tin", ("estanho", "cassiterita", "estanifero")),
    ("energy", ("carvao", "uranio", "torio", "turfa", "petroleo", "gas natural", "pirobetumin")),
    ("fertilizers", ("fosfato", "fosforo", "fosforita", "apatita", "potassi", "silvinita")),
    ("construction", ("brita",)),
    ("stone", ("granito", "marmore", "quartzito", "ardosia", "ornamental", "revestimento")),
    ("limestone", ("calcario", "dolomito", "calcita", "gipsita", "gipso", "conchas")),
    (
        "construction",
        ("areia", "argila", "cascalho", "saibro", "seixo", "basalto", "gnaisse", "arenito"),
    ),
)


def substance_group(substance: str | None) -> str:
    text = normalize_text(substance or "")
    for group, keywords in _GROUPS:
        if any(keyword in text for keyword in keywords):
            return group
    return "other"


def mining_title(feature: dict[str, Any]) -> PlaceRecord | None:
    properties = feature.get("properties") or {}
    phase = ANM_PHASES.get(clean(properties.get("FASE")) or "")
    substance = clean(properties.get("SUBS"))
    if phase is None or not substance or not feature.get("geometry"):
        return None
    if "CADASTRADO" in substance.upper():
        return None
    holder = clean(properties.get("NOME"))
    hectares = number(properties.get("AREA_HA"))
    year = number(properties.get("ANO"))
    label = substance.capitalize()
    return PlaceRecord(
        layer="mining_title",
        category=substance_group(substance),
        external_id=clean(properties.get("PROCESSO")),
        name=label,
        summary=" · ".join(
            part for part in (phase, title_case(holder) if holder else None) if part
        ),
        attributes=pairs(
            ("Substância", label),
            ("Fase", phase),
            ("Uso", clean(properties.get("USO"))),
            ("Titular", title_case(holder) if holder else None),
            ("Área", area_label(hectares)),
            ("Processo ANM", clean(properties.get("PROCESSO"))),
            ("Ano do processo", str(int(year)) if year else None),
        ),
        weight=hectares,
        geometry=feature["geometry"],
    )


def _occurrence_status(properties: dict[str, Any]) -> str | None:
    status = clean(properties.get("STATUS_ECONOMICO"))
    importance = clean(properties.get("IMPORTANCIA"))
    if status == "Mina":
        situation = clean(properties.get("SITUACAO_MINA"))
        return {"Ativo(a)": "Mina ativa", "Inativo(a)": "Mina inativa"}.get(situation or "", "Mina")
    if status == "Garimpo":
        situation = clean(properties.get("SITUACAO_GARIMPO"))
        return {"Ativo(a)": "Garimpo ativo", "Inativo(a)": "Garimpo inativo"}.get(
            situation or "", "Garimpo"
        )
    if importance and importance != "Indeterminado":
        return f"{importance} não explotado" if status == "Não explotado" else importance
    return None


def mineral_occurrence(feature: dict[str, Any]) -> PlaceRecord | None:
    properties = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates") if geometry.get("type") == "Point" else None
    substances = clean(properties.get("SUBSTANCIAS"))
    status = _occurrence_status(properties)
    # Registro sem situação nem importância conhecidas não diz o que há no ponto.
    if not coordinates or not substances or status is None:
        return None
    first = substances.replace(";", ",").split(",")[0].strip()
    utility = clean(properties.get("CLASSES_UTILITARIAS"))
    return PlaceRecord(
        layer="mineral_occurrence",
        category=substance_group(first),
        external_id=clean(properties.get("ID_OCORRENCIA")),
        name=substances,
        summary=" · ".join(part for part in (status, clean(properties.get("TOPONIMIA"))) if part),
        attributes=pairs(
            ("Substâncias", substances),
            ("Situação", status),
            ("Local", clean(properties.get("TOPONIMIA"))),
            ("Classe", utility if utility != "Não definida" else None),
            ("Província mineral", clean(properties.get("PROVINCIA"))),
        ),
        # Minas primeiro quando a área traz mais pontos do que cabem na tela.
        weight=2.0 if status.startswith("Mina") else 1.0 if status.startswith("Garimpo") else 0.0,
        longitude=number(coordinates[0]),
        latitude=number(coordinates[1]),
    )


def _year(value: Any) -> str | None:
    text = clean(value)
    return text[:4] if text and text[:4].isdigit() else None


def oil_field(feature: dict[str, Any]) -> PlaceRecord | None:
    properties = feature.get("properties") or {}
    name = clean(properties.get("NOM_CAMPO"))
    if not name or not feature.get("geometry"):
        return None
    fluid = (clean(properties.get("FLUIDO_PRI")) or "").upper()
    basin = clean(properties.get("NOM_BACIA"))
    operator = clean(properties.get("OPERADOR_C"))
    environment = {"T": "Terra", "M": "Mar"}.get((clean(properties.get("AMBIENTE")) or "")[:1])
    area = number(properties.get("AREA"))
    return PlaceRecord(
        layer="oil_field",
        category="gas" if "GÁS" in fluid or "GAS" in fluid else "oil",
        external_id=clean(properties.get("COD_CAMPO")),
        name=title_case(name),
        summary=" · ".join(part for part in (f"Bacia {basin}" if basin else None, operator) if part)
        or None,
        attributes=pairs(
            ("Fluido principal", fluid.capitalize() or None),
            ("Bacia", basin),
            ("Ambiente", environment),
            ("Etapa", clean(properties.get("ETAPA"))),
            ("Operador", operator),
            ("Descoberta", _year(properties.get("DAT_DESCOB"))),
            ("Início da produção", _year(properties.get("DAT_INICIO"))),
            ("Área", f"{area:,.0f} km²".replace(",", ".") if area else None),
        ),
        weight=area,
        geometry=feature["geometry"],
    )

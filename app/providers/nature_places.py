"""Natureza: biomas (IBGE, pelo TerraBrasilis/INPE), unidades de conservação (CNUC/MMA)
e cavernas (CANIE/ICMBio), lidos dos GeoServers oficiais na ingestão.
"""

from __future__ import annotations

from typing import Any

from app.core.text import normalize_text, title_case
from app.providers.place_record import PlaceRecord, area_label, clean, number, pairs

BIOMES_URL = "https://terrabrasilis.dpi.inpe.br/geoserver/ows"
BIOMES_LAYER = "prodes-brasil-nb:biomas_brasil"
BIOMES_DATASET_URL = "https://terrabrasilis.dpi.inpe.br"

INDE_URL = "https://geoservicos.inde.gov.br/geoserver/{workspace}/ows"
CNUC_LAYER = "MMA:cnuc_26_07_31"
CNUC_EDITION = "07/2026"
CNUC_DATASET_URL = "https://dados.mma.gov.br/dataset/unidadesdeconservacao"
CAVES_LAYER = "ICMBio:canie_052026_p"
CAVES_EDITION = "05/2026"
CAVES_DATASET_URL = "https://www.gov.br/icmbio/pt-br/assuntos/centros-de-pesquisa/cavernas/canie"

BIOMES = {
    "amazonia": "amazon",
    "caatinga": "caatinga",
    "cerrado": "cerrado",
    "mata atlantica": "atlantic_forest",
    "pampa": "pampa",
    "pantanal": "pantanal",
}
_CNUC_BIOMES = (
    ("amazonia", "Amazônia"),
    ("caatinga", "Caatinga"),
    ("cerrado", "Cerrado"),
    ("matlantica", "Mata Atlântica"),
    ("pampa", "Pampa"),
    ("pantanal", "Pantanal"),
    ("marinho", "Marinho"),
)


def biome(feature: dict[str, Any]) -> PlaceRecord | None:
    properties = feature.get("properties") or {}
    name = clean(properties.get("bioma"))
    key = BIOMES.get(normalize_text(name)) if name else None
    if key is None or not feature.get("geometry"):
        return None
    return PlaceRecord(
        layer="biome",
        category=key,
        external_id=key,
        name=name,
        weight=number(properties.get("area_km")),
        geometry=feature["geometry"],
    )


def conservation_unit(feature: dict[str, Any]) -> PlaceRecord | None:
    properties = feature.get("properties") or {}
    name = clean(properties.get("nome_uc"))
    group = clean(properties.get("grupo")) or ""
    situation = clean(properties.get("situacao"))
    if not name or not feature.get("geometry") or (situation and situation.lower() != "ativo"):
        return None
    category = "full_protection" if "integral" in group.lower() else "sustainable_use"
    kind = clean(properties.get("categoria"))
    sphere = clean(properties.get("esfera"))
    hectares = number(properties.get("ha_total"))
    created = clean(properties.get("cria_ano"))
    biomes = ", ".join(
        label for key, label in _CNUC_BIOMES if (number(properties.get(key)) or 0) > 0
    )
    manager = clean(properties.get("org_gestor"))
    return PlaceRecord(
        layer="conservation_unit",
        category=category,
        external_id=clean(properties.get("cd_cnuc")),
        name=title_case(name),
        summary=" · ".join(part for part in (kind, sphere) if part) or None,
        attributes=pairs(
            ("Categoria", kind),
            ("Grupo", group or None),
            ("Esfera", sphere),
            ("Criação", created[-4:] if created and created[-4:].isdigit() else None),
            ("Área", area_label(hectares)),
            ("Biomas", biomes or None),
            ("Órgão gestor", title_case(manager) if manager else None),
            ("Plano de manejo", clean(properties.get("pl_manejo"))),
        ),
        weight=hectares,
        geometry=feature["geometry"],
    )


def cave(feature: dict[str, Any]) -> PlaceRecord | None:
    properties = feature.get("properties") or {}
    longitude = number(properties.get("longitude"))
    latitude = number(properties.get("latitude"))
    if longitude is None or latitude is None:
        return None
    name = clean(properties.get("nome"))
    return PlaceRecord(
        layer="cave",
        external_id=clean(properties.get("num_canie")),
        name=name,
        attributes=pairs(
            ("Registro no CANIE", clean(properties.get("num_canie"))),
            ("Validação", clean(properties.get("nvl_valida"))),
        ),
        longitude=longitude,
        latitude=latitude,
    )

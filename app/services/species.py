"""Espécies registradas num território, pelo GBIF, sob demanda (ADR-25).

Registro de ocorrência não é censo: mostra o que já foi observado ou coletado ali,
e municípios pouco estudados têm poucos registros. Por isso nada disto pinta o mapa.
"""

from __future__ import annotations

import asyncio
from weakref import WeakValueDictionary

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTLCache
from app.core.config import settings
from app.core.errors import NotFoundError, ProviderRateLimitedError, TerritoryNotFoundError
from app.core.text import normalize_text
from app.providers import gbif
from app.repositories import places as repo
from app.schemas.places import PlaceSource, Species, SpeciesGroup, SpeciesSummary

# Chaves da taxonomia do GBIF. Répteis são três classes na taxonomia atual.
GROUPS: dict[str, tuple[int, ...]] = {
    "birds": (212,),
    "mammals": (359,),
    "reptiles": (11592253, 11418114, 11493978),
    "amphibians": (131,),
    "insects": (216,),
    "plants": (6,),
    "fungi": (5,),
    "threatened": (),
}
TOP_SPECIES = 12
# Até aqui a consulta enumera as espécies; acima disso o número não é informado.
RICHNESS_LIMIT = 3000
THREAT_LIMIT = 300
CACHE_SECONDS = 7 * 24 * 60 * 60

_SOURCE = PlaceSource(
    key="gbif",
    label="GBIF",
    description="GBIF — registros de ocorrência publicados por instituições e pela ciência "
    "cidadã; o SiBBr (MCTI) é o nó brasileiro da rede. Ameaça: Lista Vermelha da IUCN.",
    url=gbif.DATASET_URL,
)
_summaries: TTLCache[SpeciesSummary] = TTLCache(ttl_seconds=CACHE_SECONDS, max_entries=512)
_areas: TTLCache[dict[str, str]] = TTLCache(ttl_seconds=CACHE_SECONDS, max_entries=2048)
_names: TTLCache[gbif.SpeciesName] = TTLCache(ttl_seconds=CACHE_SECONDS, max_entries=4096)
_locks: WeakValueDictionary[tuple[str, str], asyncio.Lock] = WeakValueDictionary()
_slots = asyncio.Semaphore(6)


def clear_cache() -> None:
    _summaries.clear()
    _areas.clear()
    _names.clear()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.gbif_url,
        timeout=settings.gbif_http_timeout,
        headers={"User-Agent": "brasil-lens/0.1"},
    )


async def _area(
    session: AsyncSession, client: httpx.AsyncClient, code: str
) -> tuple[str, dict[str, str]]:
    territory = await repo.fetch_territory(session, code)
    if territory is None or territory.level not in ("country", "state", "municipality"):
        raise TerritoryNotFoundError(code)
    cached = _areas.get(code)
    if cached is not None:
        return territory.name, cached
    if territory.level == "country":
        area = {"country": "BR"}
    else:
        point = await repo.fetch_reference_point(session, territory.id)
        if point is None:
            raise TerritoryNotFoundError(code)
        units = await gbif.administrative_units(client, point.latitude, point.longitude)
        level = "GADM1" if territory.level == "state" else "GADM2"
        wanted = normalize_text(territory.name)
        # A divisão do GBIF é mais antiga que a malha do IBGE, e a resposta traz também
        # os vizinhos do ponto: vale só a unidade do mesmo nível com o mesmo nome. Sem
        # ela, o recorte seria outro território, e é melhor não mostrar nada.
        unit = next(
            (
                gid
                for kind, gid, title in units
                if kind == level and normalize_text(title) == wanted
            ),
            None,
        )
        if unit is None:
            raise NotFoundError("O GBIF não tem um recorte equivalente a este território.")
        area = {"gadmGid": unit}
    _areas.set(code, area)
    return territory.name, area


async def _name(client: httpx.AsyncClient, key: int) -> gbif.SpeciesName:
    cached = _names.get(key)
    if cached is None:
        async with _slots:
            cached = await gbif.species_name(client, key)
        _names.set(key, cached)
    return cached


async def get_species(session: AsyncSession, code: str, group: SpeciesGroup) -> SpeciesSummary:
    key = (code, group)
    cached = _summaries.get(key)
    if cached is not None:
        return cached
    if gbif.gbif_cooldown.active:
        raise ProviderRateLimitedError(
            "O GBIF está temporariamente indisponível.", gbif.gbif_cooldown.remaining_seconds()
        )
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _summaries.get(key)
        if cached is not None:
            return cached
        async with _client() as client:
            name, area = await _area(session, client, code)
            taxa = GROUPS[group]
            by_threat = await asyncio.gather(
                *(
                    gbif.species_counts(
                        client, area, taxon_keys=taxa, threat=category, limit=THREAT_LIMIT
                    )
                    for category in gbif.THREAT_CATEGORIES
                )
            )
            threats = {
                species: category
                for category, (_, counts) in zip(gbif.THREAT_CATEGORIES, by_threat, strict=True)
                for species, _ in counts
            }
            if group == "threatened":
                records = sum(total for total, _ in by_threat)
                merged = sorted(
                    (item for _, counts in by_threat for item in counts),
                    key=lambda item: (-item[1], item[0]),
                )
                enumerated = all(len(counts) < THREAT_LIMIT for _, counts in by_threat)
                species_count = len(merged) if enumerated else None
            else:
                # No país inteiro a enumeração de plantas ou insetos passaria de megabytes.
                limit = TOP_SPECIES if "country" in area else RICHNESS_LIMIT
                records, merged = await gbif.species_counts(
                    client, area, taxon_keys=taxa, limit=limit
                )
                species_count = len(merged) if len(merged) < limit else None
            top = merged[:TOP_SPECIES]
            names = await asyncio.gather(*(_name(client, species) for species, _ in top))
        summary = SpeciesSummary(
            code=code,
            name=name,
            group=group,
            records=records,
            species_count=species_count,
            species=[
                Species(
                    key=species,
                    scientific_name=info.scientific_name,
                    common_name=info.common_name,
                    family=info.family,
                    records=count,
                    threat=threats.get(species),
                )
                for (species, count), info in zip(top, names, strict=True)
            ],
            source=_SOURCE,
        )
        _summaries.set(key, summary)
        return summary

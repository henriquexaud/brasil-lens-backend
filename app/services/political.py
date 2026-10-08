from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis_cache
from app.core.cache import TTLCache
from app.core.errors import InvalidParameterError, TerritoryNotFoundError
from app.models import Territory, TerritoryLevel
from app.repositories import political as repository
from app.repositories.map_projection import data_version
from app.schemas.common import to_json
from app.schemas.political import (
    Metric,
    Office,
    PoliticalCatalog,
    PoliticalDetail,
    PoliticalValue,
    PoliticalValues,
)

_cache: TTLCache[bytes] = TTLCache(ttl_seconds=300, max_entries=24)


def clear_cache() -> None:
    _cache.clear()


def percentage(numerator: int | None, denominator: int | None) -> float | None:
    return (
        round(100 * numerator / denominator, 2) if numerator is not None and denominator else None
    )


def mapped_value(
    code: str, data: dict[str, Any], metric: Metric, candidates: dict[str, dict[str, Any]]
) -> PoliticalValue:
    leaders = data.get("leaders", [])
    first = leaders[0] if leaders else {}
    candidate = candidates.get(first.get("id", ""), {})
    party = data.get("party")
    tie = (
        bool(data.get("candidateTie"))
        if metric == "leading_candidate"
        else bool(data.get("partyTie"))
        if metric in {"leading_party", "representation"}
        else False
    )
    label: str | None = None
    value: float | None = None
    if metric == "leading_candidate":
        label = "Empate" if tie else candidate.get("name")
        party = candidate.get("party")
        value = first.get("votes")
    elif metric == "leading_party":
        label = "Empate" if tie else party
        value = data.get("partyVotes")
    elif metric == "representation":
        label = "Empate" if tie else candidate.get("name") or party
        value = data.get("partySeats")
    elif metric == "leader_share":
        value = percentage(first.get("votes"), data.get("validVotes"))
    elif metric == "margin":
        value = (
            percentage(leaders[0]["votes"] - leaders[1]["votes"], data.get("validVotes"))
            if len(leaders) > 1
            else None
        )
    elif metric in {"turnout", "abstention"}:
        value = percentage(data.get(metric), data.get("eligible"))
    else:
        numerator = (
            data.get("blankVotes")
            if metric == "blank_votes"
            else data.get("nullVotes")
            if metric == "null_votes"
            else (
                data["blankVotes"] + data["nullVotes"]
                if data.get("blankVotes") is not None and data.get("nullVotes") is not None
                else None
            )
        )
        value = percentage(numerator, data.get("totalVotes"))
    return PoliticalValue(ibge_code=code, value=value, label=label, party=party, tie=tie)


async def catalog(session: AsyncSession) -> PoliticalCatalog:
    return PoliticalCatalog(
        releases=[release.metadata_json for release in await repository.releases(session)]
    )


async def release_for(
    session: AsyncSession, year: int, office: Office, election_round: int
) -> tuple[int, dict[str, Any]]:
    releases = await repository.releases(session)
    release = next((release for release in releases if release.year == year), None)
    contest = (
        next((item for item in release.metadata_json["contests"] if item["office"] == office), None)
        if release
        else None
    )
    if (
        release is None
        or contest is None
        or (election_round != 0 and election_round not in contest["rounds"])
    ):
        raise InvalidParameterError("Não há dados publicados para este ano, cargo e turno.", "year")
    return release.run_id, release.metadata_json


async def values(
    session: AsyncSession,
    *,
    year: int,
    office: Office,
    election_round: int,
    metric: Metric,
    level: TerritoryLevel,
    parent: str | None,
) -> PoliticalValues:
    if level not in {TerritoryLevel.STATE, TerritoryLevel.MUNICIPALITY}:
        raise InvalidParameterError("Escolha estados ou municípios para o mapa político.", "level")
    if parent is not None:
        territory = (
            await session.execute(select(Territory).where(Territory.ibge_code == parent))
        ).scalar_one_or_none()
        if (
            not territory
            or territory.level != TerritoryLevel.STATE
            or level != TerritoryLevel.MUNICIPALITY
        ):
            raise InvalidParameterError(
                "O recorte municipal exige um código IBGE de estado.", "parent"
            )
    if (metric == "representation") != (election_round == 0):
        raise InvalidParameterError(
            "Representação usa os eleitos do pleito; resultados usam 1º ou 2º turno.", "round"
        )
    version, metadata = await release_for(session, year, office, election_round)
    key = (
        version,
        await data_version(session),
        year,
        office,
        election_round,
        metric,
        level,
        parent,
    )
    cached = _cache.get(key)
    if cached:
        return PoliticalValues.model_validate_json(cached)
    shared = await redis_cache.read("political-values-v1", repr(key), PoliticalValues)
    if shared:
        _cache.set(key, to_json(shared))
        return shared
    rows = await repository.results(session, year, office, election_round, level, parent)
    ids = (
        {leader["id"] for _, data in rows for leader in data.get("leaders", [])[:1]}
        if metric in {"leading_candidate", "representation"}
        else set()
    )
    candidates = await repository.candidates(session, year, ids)
    response = PoliticalValues(
        year=year,
        office=office,
        round=election_round,
        metric=metric,
        status=metadata["status"],
        updated_at=metadata["updatedAt"],
        note=metadata["note"],
        values=[mapped_value(code, data, metric, candidates) for code, data in rows],
    )
    _cache.set(key, to_json(response))
    await redis_cache.write("political-values-v1", repr(key), response, 300)
    return response


async def detail(
    session: AsyncSession,
    *,
    code: str,
    year: int,
    office: Office,
    election_round: int,
    offset: int,
    limit: int,
) -> PoliticalDetail:
    _, metadata = await release_for(session, year, office, election_round)
    territory = (
        await session.execute(select(Territory).where(Territory.ibge_code == code))
    ).scalar_one_or_none()
    if not territory:
        raise TerritoryNotFoundError(code)
    data = await repository.result(session, year, office, election_round, code)
    leaders = data.get("leaders", []) if data else []
    candidates = await repository.candidates(session, year, {item["id"] for item in leaders})
    total, representatives = (
        await repository.elected(session, year, office, code, offset, limit)
        if election_round == 0
        else (0, [])
    )
    return PoliticalDetail(
        ibge_code=code,
        name=territory.name,
        level=territory.level.value,
        year=year,
        office=office,
        round=election_round,
        status=metadata["status"],
        updated_at=metadata["updatedAt"],
        summary=data,
        leaders=[
            {**candidates[item["id"]], "votes": item["votes"]}
            for item in leaders
            if item["id"] in candidates
        ],
        representatives=representatives,
        representative_total=total,
        offset=offset,
        limit=limit,
        note=metadata["note"],
    )

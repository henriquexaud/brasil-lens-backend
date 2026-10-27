from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.api.http import etag_matches
from app.schemas.education import (
    EducationInstitutionDetail,
    EducationNetwork,
    EducationTerritory,
)
from app.services import education as service

router = APIRouter(prefix="/education", tags=["education"])
Session = Annotated[AsyncSession, Depends(get_session)]


def conditional(request: Request, body: bytes) -> Response:
    etag = f'W/"education:{sha256(body).hexdigest()}"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=300"}
    return (
        Response(status_code=304, headers=headers)
        if etag_matches(request, etag)
        else Response(body, media_type="application/json", headers=headers)
    )


@router.get(
    "/territories/{code}",
    response_model=EducationTerritory,
    summary="Instituições públicas com cursos presenciais no território",
)
async def territory(
    request: Request,
    session: Session,
    code: str,
    year: str = "latest",
    network: EducationNetwork = "public",
) -> Response:
    return conditional(request, await service.get_territory(session, code, year, network))


@router.get(
    "/institutions/{code}",
    response_model=EducationInstitutionDetail,
    summary="Detalhe de uma instituição pública e os municípios onde ela oferece cursos",
)
async def institution(
    request: Request,
    session: Session,
    code: Annotated[int, Path(ge=1)],
    year: str = "latest",
) -> Response:
    return conditional(request, await service.get_institution(session, code, year))

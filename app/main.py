"""Aplicação FastAPI.

Três pontos não-obvios configurados aqui:

* **GZip**: GeoJSON é texto altamente repetitivo e comprime ~5×. É a única
  "otimização de infraestrutura" do MVP, e ela age exatamente no gargalo real
  (bytes na rede), não em CPU.
* **ORJSONResponse como padrão**: a resposta do mapa é o maior payload do
  sistema; serialização importa.
* **Envelope único de erro**: até o 422 de validação do FastAPI é reescrito para
  `{"error": {...}}`, para o frontend ter um só formato a tratar.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import DomainError, error_body
from app.core.logging import configure_logging, get_logger
from app.core.redis_cache import close as close_redis
from app.core.redis_cache import purge_previous_cache_version
from app.db.session import SessionFactory, dispose_engine
from app.jobs import weather_scheduler
from app.repositories.boundaries import municipality_areas, state_areas
from app.services import hydrography

configure_logging()
logger = get_logger(__name__)


async def _warm_up() -> None:
    """Consultas lentas de dado estático, antes do primeiro pedido que as usaria."""
    async with SessionFactory() as session:
        try:
            # Área geodésica da malha canônica (~2 s): densidade de focos e peso
            # de cada ponto na média do clima de cada UF no mapa do Brasil.
            await municipality_areas(session)
            await state_areas(session)
        except Exception:
            logger.warning("api.warm_up_areas_failed", exc_info=True)
        if settings.hydrography_warmup_enabled:
            await hydrography.warm_up(session)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("api.startup", extra={"environment": settings.app_env})
    await purge_previous_cache_version()
    # Atualização periódica das fontes climáticas — ver
    # app/jobs/weather_scheduler.py sobre por que é um laço em processo e não
    # um cron externo.
    weather_scheduler.start()
    warm_up = asyncio.create_task(_warm_up())
    yield
    warm_up.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await warm_up
    await weather_scheduler.stop()
    await dispose_engine()
    await close_redis()
    logger.info("api.shutdown")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Clima e meio ambiente no território brasileiro.\n\n"
        "A API combina dados territoriais do IBGE com clima, alertas, focos de "
        "calor e hidrografia de fontes públicas."
    ),
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    # Os municípios acompanhados têm operações de escrita feitas pelo browser.
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    # Expõe Location para clientes que seguem o recurso criado.
    expose_headers=["Location"],
)

app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.exception_handler(DomainError)
async def handle_domain_error(_: Request, exc: DomainError) -> ORJSONResponse:
    return ORJSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.code, exc.message, exc.details),
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_: Request, exc: RequestValidationError) -> ORJSONResponse:
    return ORJSONResponse(
        status_code=422,
        content=error_body(
            "validation_error",
            "Parâmetros inválidos.",
            {
                "fields": [
                    {"location": list(error["loc"]), "message": error["msg"]}
                    for error in exc.errors()
                ]
            },
        ),
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(_: Request, exc: StarletteHTTPException) -> ORJSONResponse:
    code = "not_found" if exc.status_code == 404 else "http_error"
    return ORJSONResponse(
        status_code=exc.status_code,
        content=error_body(code, str(exc.detail)),
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> ORJSONResponse:
    logger.exception("api.unhandled_error", extra={"path": request.url.path})
    return ORJSONResponse(
        status_code=500,
        content=error_body("internal_error", "Erro interno inesperado."),
    )

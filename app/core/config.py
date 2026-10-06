from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Brasil Lens API"
    app_env: Literal["development", "test", "production"] = "development"

    database_url: str = "postgresql+asyncpg://brasil_lens:brasil_lens@localhost:5432/brasil_lens"
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 5

    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    redis_url: str | None = None
    redis_cache_prefix: str = "brasil-lens:v4"
    read_cache_ttl_seconds: int = 300
    read_cache_max_entries: int = 64
    http_cache_max_age: int = 300
    map_http_cache_max_age: int = 3600
    map_http_stale_while_revalidate: int = 86400

    ibge_base_url: str = "https://servicodados.ibge.gov.br"
    ibge_http_timeout: float = 120.0
    ibge_max_concurrency: int = 4

    inmet_base_url: str = "https://apitempo.inmet.gov.br"
    inmet_alerts_base_url: str = "https://apiprevmet3.inmet.gov.br"
    inmet_http_timeout: float = 30.0
    inmet_max_concurrency: int = 2

    cemaden_alerts_base_url: str = "https://gsc.cemaden.gov.br/geoserver/cemaden_dev"
    cemaden_http_timeout: float = 30.0
    cemaden_alert_validity_buffer_seconds: int = 3 * 600

    # Em produção a Open-Meteo é chamada pelo repasse da Vercel (IP do Render compartilhado);
    # sem chave, a chamada vai direto, como no ambiente local.
    open_meteo_url: str = "https://api.open-meteo.com"
    open_meteo_relay_key: str | None = None

    weather_refresh_enabled: bool = True
    hydrography_warmup_enabled: bool = True
    weather_refresh_interval_seconds: int = 600
    weather_stations_cache_ttl_seconds: int = 90
    weather_alerts_cache_ttl_seconds: int = 90

    push_notifications_enabled: bool = False
    vapid_public_key: str = ""
    vapid_private_key: SecretStr | None = None
    vapid_subject: str = "https://brasil-lens.vercel.app"
    notification_batch_limit: int = Field(default=500, ge=1, le=1000)

    inpe_queimadas_wfs_url: str = "https://data.inpe.br/queimadas/geoserver/wfs"
    inpe_queimadas_wms_url: str = "https://data.inpe.br/queimadas/geoserver/wms"
    inpe_queimadas_http_timeout: float = 30.0
    fire_hotspots_cache_ttl_seconds: int = 600

    geometry_overview_tolerance: float = 0.005
    geometry_detail_tolerance: float = 0.001

    api_v1_prefix: str = Field(default="/api/v1")

    @field_validator("database_url")
    @classmethod
    def _asyncpg_url(cls, value: str) -> str:
        # Aceita a URL como o Neon e o Render a entregam (`postgresql://...?sslmode=require
        # &channel_binding=require`); o asyncpg não conhece `sslmode` nem `channel_binding`.
        url = make_url(value)
        if not url.drivername.startswith(("postgres", "postgresql")):
            return value
        query = dict(url.query)
        sslmode = query.pop("sslmode", None)
        query.pop("channel_binding", None)
        if sslmode and "ssl" not in query:
            query["ssl"] = sslmode
        url = url.set(drivername="postgresql+asyncpg", query=query)
        return url.render_as_string(hide_password=False)

    @field_validator("open_meteo_relay_key")
    @classmethod
    def _strip_relay_key(cls, value: str | None) -> str | None:
        # Colada num painel, a chave pode vir com quebra de linha, que o httpx recusa
        # em header (e o erro expõe o valor no log).
        return (value or "").strip() or None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

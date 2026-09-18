"""Configuração da aplicação, lida exclusivamente de variáveis de ambiente."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

    # NoDecode: sem ele, pydantic-settings tenta interpretar a variável de
    # ambiente como JSON antes de qualquer validator, e "a,b" quebra o boot.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # Cache em processo das projeções de leitura. Não é infraestrutura: é um dict
    # com TTL. Os dados só mudam durante a ingestão, então todas as sessões pedem
    # exatamente a mesma resposta de mapa. 0 desliga.
    read_cache_ttl_seconds: int = 300
    read_cache_max_entries: int = 64
    # Teto de features por entrada cacheada. Uma projeção municipal completa
    # ocupa ~7 MB de memória depois de desserializada (medido: 40 entradas
    # levaram a API de 92 MB para 378 MB de RSS), e o acerto é baixo porque
    # cada usuário abre um estado diferente. As projeções realmente
    # compartilhadas — Brasil, regiões, UFs — cabem folgadamente neste limite.
    read_cache_max_features: int = 200
    # Cache-Control max-age das respostas geográficas.
    http_cache_max_age: int = 300

    ibge_base_url: str = "https://servicodados.ibge.gov.br"
    ibge_http_timeout: float = 120.0
    ibge_max_concurrency: int = 4

    # Tolerâncias de ST_SimplifyPreserveTopology, em graus (SRID 4326).
    # 0.02° ~ 2 km: suficiente para o Brasil inteiro em zoom 4.
    geometry_overview_tolerance: float = 0.02
    # 0.002° ~ 200 m: suficiente para municípios em zoom de estado.
    geometry_detail_tolerance: float = 0.002

    api_v1_prefix: str = Field(default="/api/v1")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def sync_database_url(self) -> str:
        """URL síncrona — usada só por ferramentas que não falam asyncpg."""
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

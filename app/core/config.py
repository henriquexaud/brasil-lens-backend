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
    redis_url: str | None = None
    redis_cache_prefix: str = "brasil-lens:v3"
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
    # /map e /map/values: o ETag carrega a versão da ingestão, então o navegador
    # pode reusar a malha por mais tempo e revalidar em segundo plano.
    map_http_cache_max_age: int = 3600
    map_http_stale_while_revalidate: int = 86400

    ibge_base_url: str = "https://servicodados.ibge.gov.br"
    ibge_http_timeout: float = 120.0
    ibge_max_concurrency: int = 4

    # Clima — ver docs/ARCHITECTURE.md (contexto Clima) e app/providers/inmet/.
    # Timeout menor que o do IBGE de propósito: são chamadas pequenas (uma
    # estação, um payload de alertas), repetidas a cada ciclo do scheduler —
    # não malhas municipais de dezenas de MB.
    inmet_base_url: str = "https://apitempo.inmet.gov.br"
    inmet_alerts_base_url: str = "https://apiprevmet3.inmet.gov.br"
    inmet_http_timeout: float = 30.0
    # Baixa de propósito: a série horária por estação (`/estacao/{...}`) tem
    # *rate limit* agressivo — confirmado rodando o job real, que com
    # concorrência 8 fez as 518 estações caírem em "Você atingiu o limite de
    # requisições." (HTTP 200, texto plano, sem 429). Concorrência baixa +
    # retentativa (ver app/providers/inmet/stations.py) é o que faz o ciclo
    # completar sem depender de adivinhar o teto exato da fonte.
    inmet_max_concurrency: int = 2

    # CEMADEN — riscos geo-hidrológicos (inundação, enxurrada, deslizamento),
    # complementar aos avisos meteorológicos do INMET acima. Sem API pública
    # documentada (ver docs/ARCHITECTURE.md, contexto Clima): a mesma situação
    # de fato do INMET, cuja `inmet_alerts_base_url` também não é documentada
    # — a diferença é que aqui a fonte real é um GeoServer OGC padrão (WFS),
    # não um endpoint JSON ad-hoc, o que reduz a fragilidade (protocolo
    # estável, `DescribeFeatureType` autodescritivo) mesmo sem documentação
    # formal de negócio. Confirmado por chamada real: `GET .../ows?...
    # &typeName=cemaden_dev:alertas_vigentes_siaden` — a mesma camada que
    # https://mapainterativo.cemaden.gov.br usa para o próprio mapa público.
    cemaden_alerts_base_url: str = "https://gsc.cemaden.gov.br/geoserver/cemaden_dev"
    cemaden_http_timeout: float = 30.0
    # O CEMADEN não manda uma data de expiração explícita, e o candidato óbvio
    # (`vigencia`) também não serve: medido contra a fonte real, alertas ainda
    # `status=1` ficaram mais de 8h sem `vigencia` atualizar — não dá para
    # assumir um heartbeat frequente da fonte. `expires` é derivado do nosso
    # próprio ciclo de ingestão, não de `vigencia` (ver
    # app/providers/cemaden/alerts.py): 3 ciclos perdidos do scheduler antes
    # de um alerta ainda ativo sumir do mapa — mesmo multiplicador de
    # `_STALE_MULTIPLIER` em app/services/weather.py, aplicado por alerta em
    # vez de por fonte inteira.
    cemaden_alert_validity_buffer_seconds: int = 3 * 600

    # Liga o laço asyncio de atualização periódica (app/jobs/weather_scheduler.py)
    # no lifespan da API. Desligado em teste/CI por padrão via .env, para não
    # depender de rede externa ao rodar a suíte.
    weather_refresh_enabled: bool = True
    # Aquece a hidrografia nacional (consulta lenta à ANA) em segundo plano no
    # startup da API.
    hydrography_warmup_enabled: bool = True
    weather_refresh_interval_seconds: int = 600
    # TTL de resposta HTTP das rotas /weather/* — curto porque o dado já é
    # barato de ler (vem do Postgres, não da fonte externa); só evita reler a
    # cada poll do frontend durante picos de tráfego.
    weather_stations_cache_ttl_seconds: int = 90
    weather_alerts_cache_ttl_seconds: int = 90

    # Focos INPE — mesmos serviços públicos usados pelo BDQueimadas, sem chave.
    inpe_queimadas_wfs_url: str = "https://data.inpe.br/queimadas/geoserver/wfs"
    inpe_queimadas_wms_url: str = "https://data.inpe.br/queimadas/geoserver/wms"
    inpe_queimadas_http_timeout: float = 30.0
    fire_hotspots_cache_ttl_seconds: int = 600

    # Tolerâncias de ST_SimplifyPreserveTopology, em graus (SRID 4326).
    # 0.005° ~ 500 m: mantém fidelidade e sinuosidade de fronteiras estaduais.
    geometry_overview_tolerance: float = 0.005
    # 0.001° ~ 100 m: preserva contornos em alta resolução e fidelidade cartográfica.
    geometry_detail_tolerance: float = 0.001

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

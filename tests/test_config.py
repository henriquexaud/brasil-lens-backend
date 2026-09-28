from app.core.config import Settings


def test_neon_url_is_translated_to_asyncpg_with_ssl() -> None:
    settings = Settings(
        database_url=(
            "postgresql://owner:s3cr%40t@ep-x.us-east-1.aws.neon.tech/brasil_lens"
            "?sslmode=require&channel_binding=require"
        )
    )

    assert settings.database_url == (
        "postgresql+asyncpg://owner:s3cr%40t@ep-x.us-east-1.aws.neon.tech/brasil_lens?ssl=require"
    )


def test_postgres_scheme_and_asyncpg_url_without_ssl_are_accepted() -> None:
    assert (
        Settings(database_url="postgres://u:p@db:5432/x").database_url
        == "postgresql+asyncpg://u:p@db:5432/x"
    )
    assert (
        Settings(database_url="postgresql+asyncpg://u:p@db:5432/x").database_url
        == "postgresql+asyncpg://u:p@db:5432/x"
    )


def test_relay_key_pasted_with_a_line_break_still_works() -> None:
    assert Settings(open_meteo_relay_key="abc123\n").open_meteo_relay_key == "abc123"
    assert Settings(open_meteo_relay_key="  \n").open_meteo_relay_key is None

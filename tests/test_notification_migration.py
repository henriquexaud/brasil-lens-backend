import pytest
from sqlalchemy import text

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory


@pytest.mark.db
async def test_migration_preserves_following_but_requires_new_notification_authorization(session):
    migration = (
        ScriptDirectory.from_config(Config("alembic.ini")).get_revision("0011_notifications").module
    )

    def migrate(sync, direction):
        with Operations.context(MigrationContext.configure(sync.connection())):
            getattr(migration, direction)()

    try:
        await session.run_sync(migrate, "downgrade")
        await session.execute(
            text("INSERT INTO users (id, name) VALUES ('notification-migration', 'Teste')")
        )
        await session.execute(
            text("""
            INSERT INTO followed_municipalities (user_id, municipality_code)
            VALUES ('notification-migration', '9000001')
        """)
        )
        await session.run_sync(migrate, "upgrade")
        assert tuple(
            (
                await session.execute(
                    text("""
            SELECT municipality_code, notifications_enabled, notifications_opt_in_at
              FROM followed_municipalities WHERE user_id = 'notification-migration'
        """)
                )
            ).one()
        ) == ("9000001", False, None)
        await session.execute(
            text("""
            INSERT INTO followed_municipalities (user_id, municipality_code)
            VALUES ('notification-migration', '9000002')
        """)
        )
        assert (
            await session.execute(
                text("""
            SELECT bool_or(notifications_enabled)
              FROM followed_municipalities WHERE user_id = 'notification-migration'
        """)
            )
        ).scalar_one() is False
    finally:
        await session.rollback()

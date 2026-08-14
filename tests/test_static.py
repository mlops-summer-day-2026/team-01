from __future__ import annotations

import asyncio

from sqlalchemy.dialects import postgresql

from app.config import Settings
from app.db import Database
from app.handlers import create_router
from app.models import Base
from app.services import _atomic_take_statement


def test_settings_and_router_construct_without_external_connections(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@localhost/database")

    settings = Settings.from_env()
    assert settings.database_url.startswith("postgresql+asyncpg://")

    database = Database(settings.database_url)
    router = create_router(database.sessions)
    assert len(router.message.handlers) == 14
    assert len(router.my_chat_member.handlers) == 1
    asyncio.run(database.close())


def test_schema_contains_only_the_six_p0_tables() -> None:
    assert set(Base.metadata.tables) == {
        "users",
        "workspaces",
        "workspace_members",
        "teams",
        "team_members",
        "stands",
    }


def test_atomic_take_is_conditional_update_returning() -> None:
    sql = str(
        _atomic_take_statement(stand_id=10, user_id=20).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).upper()
    assert sql.startswith("UPDATE STANDS SET")
    assert "OCCUPIED_BY_USER_ID IS NULL" in sql
    assert "RETURNING STANDS.ID" in sql

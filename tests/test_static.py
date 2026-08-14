from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.dialects import postgresql

from app.config import Settings
from app.db import Database
from app.handlers import create_router
from app.models import Base
from app.main import COMMANDS
from app.models import WorkspaceRole
from app.services import DomainError, _atomic_take_statement, parse_workspace_role


def test_settings_and_router_construct_without_external_connections(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@localhost/database")

    settings = Settings.from_env()
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.wow_public_url.endswith("/boss-mode/")

    database = Database(settings.database_url)
    router = create_router(database.sessions)
    assert len(router.message.handlers) == 18
    assert len(router.my_chat_member.handlers) == 1
    asyncio.run(database.close())


def test_p1_p2_commands_are_registered() -> None:
    command_names = {item.command for item in COMMANDS}
    assert {"set_role", "my_stands", "free_stands"} <= command_names
    assert "bali" not in command_names


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("USER", WorkspaceRole.USER),
        ("пользователь", WorkspaceRole.USER),
        ("mod", WorkspaceRole.MODERATOR),
        ("МОДЕРАТОР", WorkspaceRole.MODERATOR),
        ("admin", WorkspaceRole.ADMIN),
        ("Администратор", WorkspaceRole.ADMIN),
    ],
)
def test_workspace_role_parser(value: str, expected: WorkspaceRole) -> None:
    assert parse_workspace_role(value) is expected


def test_workspace_role_parser_rejects_unknown_role() -> None:
    with pytest.raises(DomainError, match="USER, MODERATOR или ADMIN"):
        parse_workspace_role("superadmin")


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

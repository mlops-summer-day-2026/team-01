from __future__ import annotations

import asyncio
import os
import secrets

import pytest
from sqlalchemy import delete

from app import services
from app.db import Database
from app.models import User, Workspace, WorkspaceRole


DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://standbot:standbot@localhost:5432/standbot",
)


def identity(user_id: int, name: str) -> services.TelegramIdentity:
    return services.TelegramIdentity(
        telegram_user_id=user_id,
        username=name.casefold(),
        display_name=name,
    )


@pytest.mark.asyncio
async def test_p0_flow_isolation_atomic_take_and_restart() -> None:
    database = Database(DATABASE_URL)
    await database.create_schema()

    suffix = secrets.randbelow(1_000_000_000)
    chat_id = -(8_000_000_000 + suffix)
    other_chat_id = chat_id - 1_000_000_000
    owner = identity(8_000_000_000 + suffix, "Owner")
    moderator = identity(9_000_000_000 + suffix, "Moderator")
    user_a = identity(10_000_000_000 + suffix, "Alice")
    user_b = identity(11_000_000_000 + suffix, "Bob")
    all_telegram_user_ids = [
        owner.telegram_user_id,
        moderator.telegram_user_id,
        user_a.telegram_user_id,
        user_b.telegram_user_id,
    ]

    try:
        async with database.sessions() as session:
            await services.bootstrap_workspace(
                session,
                chat_id,
                "Demo chat",
                [
                    (owner, WorkspaceRole.ADMIN),
                    (moderator, WorkspaceRole.MODERATOR),
                ],
            )
            owner_context = await services.ensure_command_context(
                session, chat_id, "Demo chat", owner
            )
            moderator_context = await services.ensure_command_context(
                session, chat_id, "Demo chat", moderator
            )
            user_a_context = await services.ensure_command_context(
                session, chat_id, "Demo chat", user_a
            )
            user_b_context = await services.ensure_command_context(
                session, chat_id, "Demo chat", user_b
            )

            assert owner_context.role is WorkspaceRole.ADMIN
            assert moderator_context.role is WorkspaceRole.MODERATOR
            await services.create_team(
                session, owner_context, "Backend", "Backend Team"
            )
            await services.create_team(session, owner_context, "mobile", "Mobile")
            with pytest.raises(services.DomainError, match="уже существует"):
                await services.create_team(session, owner_context, "BACKEND", "Duplicate")

            await services.add_user_to_team(session, moderator_context, "backend", user_a)
            await services.add_user_to_team(session, moderator_context, "backend", user_b)
            await services.create_stand(
                session, moderator_context, "backend", "Dev-1"
            )
            await services.create_stand(
                session, moderator_context, "backend", "dev-2"
            )
            with pytest.raises(services.DomainError, match="уже существует"):
                await services.create_stand(
                    session, moderator_context, "backend", "DEV-1"
                )

            other_owner_context = await services.ensure_command_context(
                session, other_chat_id, "Other chat", owner
            )
            await services.bootstrap_workspace(
                session,
                other_chat_id,
                "Other chat",
                [(owner, WorkspaceRole.ADMIN)],
            )
            other_owner_context = await services.ensure_command_context(
                session, other_chat_id, "Other chat", owner
            )
            await services.create_team(
                session, other_owner_context, "backend", "Independent Backend"
            )
            await session.commit()

        async def concurrent_take(
            context: services.CommandContext,
        ) -> str:
            async with database.sessions() as session:
                result = await services.take_stand(
                    session, context, "backend", "DEV-1"
                )
                await session.commit()
                return result

        result_a, result_b = await asyncio.gather(
            concurrent_take(user_a_context), concurrent_take(user_b_context)
        )
        assert sum(result.startswith("✅") for result in (result_a, result_b)) == 1
        assert sum("уже занят" in result for result in (result_a, result_b)) == 1

        winner_context = (
            user_a_context if result_a.startswith("✅") else user_b_context
        )
        loser_context = user_b_context if winner_context is user_a_context else user_a_context

        async with database.sessions() as session:
            idempotent = await services.take_stand(
                session, winner_context, "backend", "dev-1"
            )
            assert "уже занят вами" in idempotent

            with pytest.raises(services.DomainError, match="модератор"):
                await services.release_stand(
                    session, loser_context, "backend", "dev-1"
                )
            with pytest.raises(services.DomainError, match="сначала освободите"):
                await services.delete_team(session, owner_context, "backend")
            with pytest.raises(services.DomainError, match="занятый стенд"):
                await services.remove_stand(
                    session, moderator_context, "backend", "dev-1"
                )

            await services.release_stand(
                session, moderator_context, "backend", "dev-1"
            )
            repeated_release = await services.release_stand(
                session, moderator_context, "backend", "dev-1"
            )
            assert "уже свободен" in repeated_release
            await session.commit()

        await database.close()

        restarted_database = Database(DATABASE_URL)
        try:
            async with restarted_database.sessions() as session:
                restarted_owner_context = await services.ensure_command_context(
                    session, chat_id, "Demo chat", owner
                )
                stands = await services.list_stands(
                    session, restarted_owner_context, "backend"
                )
                assert "Dev-1 — свободен" in stands
                assert "dev-2 — свободен" in stands

                other_teams = await services.list_teams(
                    session,
                    await services.ensure_command_context(
                        session, other_chat_id, "Other chat", owner
                    ),
                )
                assert "Independent Backend" in other_teams
                assert "Backend Team" not in other_teams
        finally:
            await restarted_database.close()
    finally:
        # Remove only rows created by this test; never reset the shared demo database.
        cleanup_database = Database(DATABASE_URL)
        try:
            async with cleanup_database.sessions() as session:
                await session.execute(
                    delete(Workspace).where(
                        Workspace.telegram_chat_id.in_([chat_id, other_chat_id])
                    )
                )
                await session.execute(
                    delete(User).where(
                        User.telegram_user_id.in_(all_telegram_user_ids)
                    )
                )
                await session.commit()
        finally:
            await cleanup_database.close()

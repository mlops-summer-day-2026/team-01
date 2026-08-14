from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Stand,
    Team,
    TeamMember,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)


class DomainError(Exception):
    """An expected error safe to show in Telegram."""


@dataclass(frozen=True, slots=True)
class TelegramIdentity:
    telegram_user_id: int
    username: str | None
    display_name: str
    is_bot: bool = False


@dataclass(frozen=True, slots=True)
class CommandContext:
    workspace_id: int
    user_id: int
    role: WorkspaceRole


ROLE_RANK = {
    WorkspaceRole.USER: 0,
    WorkspaceRole.MODERATOR: 1,
    WorkspaceRole.ADMIN: 2,
}


def _key(value: str, label: str, max_length: int) -> str:
    normalized = value.strip().casefold()
    if not normalized:
        raise DomainError(f"{label} не может быть пустым.")
    if len(normalized) > max_length:
        raise DomainError(f"{label} слишком длинный (максимум {max_length} символов).")
    return normalized


def _display_name(identity: TelegramIdentity) -> str:
    return identity.display_name.strip()[:255] or "Пользователь Telegram"


def _person(display_name: str, username: str | None) -> str:
    return f"{display_name} (@{username})" if username else display_name


def _require_role(context: CommandContext, minimum: WorkspaceRole) -> None:
    if ROLE_RANK[context.role] < ROLE_RANK[minimum]:
        required = {
            WorkspaceRole.MODERATOR: "модератор или администратор",
            WorkspaceRole.ADMIN: "администратор",
        }[minimum]
        raise DomainError(f"Недостаточно прав: нужен {required} этого чата.")


async def upsert_user(session: AsyncSession, identity: TelegramIdentity) -> int:
    statement = (
        pg_insert(User)
        .values(
            telegram_user_id=identity.telegram_user_id,
            username=identity.username,
            display_name=_display_name(identity),
        )
        .on_conflict_do_update(
            index_elements=[User.telegram_user_id],
            set_={
                "username": identity.username,
                "display_name": _display_name(identity),
                "updated_at": func.now(),
            },
        )
        .returning(User.id)
    )
    return (await session.execute(statement)).scalar_one()


async def upsert_workspace(
    session: AsyncSession, telegram_chat_id: int, title: str | None
) -> int:
    statement = (
        pg_insert(Workspace)
        .values(telegram_chat_id=telegram_chat_id, title=title)
        .on_conflict_do_update(
            index_elements=[Workspace.telegram_chat_id],
            set_={"title": title, "updated_at": func.now()},
        )
        .returning(Workspace.id)
    )
    return (await session.execute(statement)).scalar_one()


async def ensure_workspace_member(
    session: AsyncSession,
    workspace_id: int,
    user_id: int,
    role: WorkspaceRole = WorkspaceRole.USER,
    *,
    update_role: bool = False,
) -> WorkspaceRole:
    statement = pg_insert(WorkspaceMember).values(
        workspace_id=workspace_id, user_id=user_id, role=role.value
    )
    if update_role:
        statement = statement.on_conflict_do_update(
            constraint="uq_workspace_member",
            set_={"role": role.value, "updated_at": func.now()},
        )
    else:
        statement = statement.on_conflict_do_nothing(constraint="uq_workspace_member")
    await session.execute(statement)

    stored_role = await session.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    return WorkspaceRole(stored_role)


async def ensure_command_context(
    session: AsyncSession,
    telegram_chat_id: int,
    chat_title: str | None,
    actor: TelegramIdentity,
) -> CommandContext:
    workspace_id = await upsert_workspace(session, telegram_chat_id, chat_title)
    user_id = await upsert_user(session, actor)
    role = await ensure_workspace_member(session, workspace_id, user_id)
    return CommandContext(workspace_id=workspace_id, user_id=user_id, role=role)


async def bootstrap_workspace(
    session: AsyncSession,
    telegram_chat_id: int,
    chat_title: str | None,
    administrators: list[tuple[TelegramIdentity, WorkspaceRole]],
) -> int:
    workspace_id = await upsert_workspace(session, telegram_chat_id, chat_title)
    for identity, role in administrators:
        if identity.is_bot:
            continue
        user_id = await upsert_user(session, identity)
        await ensure_workspace_member(
            session, workspace_id, user_id, role, update_role=True
        )
    return workspace_id


async def _team(
    session: AsyncSession, workspace_id: int, slug: str, *, lock: bool = False
) -> Team:
    statement = select(Team).where(
        Team.workspace_id == workspace_id, Team.slug == _key(slug, "Team", 64)
    )
    if lock:
        statement = statement.with_for_update()
    team = await session.scalar(statement)
    if team is None:
        raise DomainError(f"Team «{slug}» не найдена в этом чате.")
    return team


async def _stand(session: AsyncSession, team_id: int, name: str) -> Stand:
    stand = await session.scalar(
        select(Stand).where(
            Stand.team_id == team_id,
            Stand.name_key == _key(name, "Имя стенда", 128),
        )
    )
    if stand is None:
        raise DomainError(f"Стенд «{name}» не найден в этой Team.")
    return stand


async def _is_team_member(session: AsyncSession, team_id: int, user_id: int) -> bool:
    return bool(
        await session.scalar(
            select(exists().where(TeamMember.team_id == team_id, TeamMember.user_id == user_id))
        )
    )


def _atomic_take_statement(stand_id: int, user_id: int):
    """Build the one-statement compare-and-set used by concurrent callers."""
    return (
        update(Stand)
        .where(Stand.id == stand_id, Stand.occupied_by_user_id.is_(None))
        .values(
            occupied_by_user_id=user_id,
            occupied_at=func.now(),
            updated_at=func.now(),
        )
        .returning(Stand.id)
    )


async def list_teams(session: AsyncSession, context: CommandContext) -> str:
    teams = (
        await session.execute(
            select(Team.slug, Team.name)
            .where(Team.workspace_id == context.workspace_id)
            .order_by(Team.slug)
        )
    ).all()
    if not teams:
        return "В этом чате пока нет Team. Администратор может создать: /create_team <slug> [название]"
    lines = ["Team этого чата:"]
    lines.extend(f"• {name} [{slug}]" for slug, name in teams)
    return "\n".join(lines)


async def list_users(session: AsyncSession, context: CommandContext) -> str:
    users = (
        await session.execute(
            select(User.display_name, User.username, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
            .where(WorkspaceMember.workspace_id == context.workspace_id)
            .order_by(User.display_name, User.id)
        )
    ).all()
    lines = ["Известные боту пользователи этого чата (список может быть неполным):"]
    lines.extend(
        f"• {_person(display_name, username)} — {role}"
        for display_name, username, role in users
    )
    return "\n".join(lines)


async def create_team(
    session: AsyncSession,
    context: CommandContext,
    slug: str,
    name: str | None,
) -> str:
    _require_role(context, WorkspaceRole.ADMIN)
    normalized_slug = _key(slug, "Slug Team", 64)
    display_name = (name or slug).strip()
    if not display_name:
        raise DomainError("Название Team не может быть пустым.")
    if len(display_name) > 255:
        raise DomainError("Название Team слишком длинное (максимум 255 символов).")

    statement = (
        pg_insert(Team)
        .values(
            workspace_id=context.workspace_id,
            slug=normalized_slug,
            name=display_name,
            created_by_user_id=context.user_id,
        )
        .on_conflict_do_nothing(constraint="uq_team_workspace_slug")
        .returning(Team.id)
    )
    if (await session.execute(statement)).scalar_one_or_none() is None:
        raise DomainError(f"Team [{normalized_slug}] уже существует в этом чате.")
    return f"✅ Team {display_name} [{normalized_slug}] создана."


async def delete_team(
    session: AsyncSession, context: CommandContext, slug: str
) -> str:
    _require_role(context, WorkspaceRole.ADMIN)
    team = await _team(session, context.workspace_id, slug, lock=True)
    has_occupied_stands = await session.scalar(
        select(
            exists().where(
                Stand.team_id == team.id, Stand.occupied_by_user_id.is_not(None)
            )
        )
    )
    if has_occupied_stands:
        raise DomainError("Нельзя удалить Team: сначала освободите все занятые стенды.")
    await session.execute(delete(Team).where(Team.id == team.id))
    return f"✅ Team {team.name} [{team.slug}] удалена."


async def add_user_to_team(
    session: AsyncSession,
    context: CommandContext,
    slug: str,
    target: TelegramIdentity,
) -> str:
    _require_role(context, WorkspaceRole.MODERATOR)
    if target.is_bot:
        raise DomainError("Бота нельзя добавить в Team как пользователя.")
    team = await _team(session, context.workspace_id, slug)
    target_user_id = await upsert_user(session, target)
    await ensure_workspace_member(session, context.workspace_id, target_user_id)
    statement = (
        pg_insert(TeamMember)
        .values(team_id=team.id, user_id=target_user_id)
        .on_conflict_do_nothing(constraint="uq_team_member")
        .returning(TeamMember.id)
    )
    inserted = (await session.execute(statement)).scalar_one_or_none()
    person = _person(_display_name(target), target.username)
    if inserted is None:
        return f"ℹ️ {person} уже состоит в Team [{team.slug}]."
    return f"✅ {person} добавлен(а) в Team [{team.slug}]."


async def remove_user_from_team(
    session: AsyncSession,
    context: CommandContext,
    slug: str,
    target: TelegramIdentity,
) -> str:
    _require_role(context, WorkspaceRole.MODERATOR)
    team = await _team(session, context.workspace_id, slug)
    target_user_id = await upsert_user(session, target)
    await ensure_workspace_member(session, context.workspace_id, target_user_id)
    owns_stand = await session.scalar(
        select(
            exists().where(
                Stand.team_id == team.id,
                Stand.occupied_by_user_id == target_user_id,
            )
        )
    )
    if owns_stand:
        raise DomainError("Нельзя удалить пользователя: сначала освободите его стенды в этой Team.")
    result = await session.execute(
        delete(TeamMember).where(
            TeamMember.team_id == team.id, TeamMember.user_id == target_user_id
        )
    )
    person = _person(_display_name(target), target.username)
    if not result.rowcount:
        return f"ℹ️ {person} уже не состоит в Team [{team.slug}]."
    return f"✅ {person} удалён(а) из Team [{team.slug}]."


async def list_team_users(
    session: AsyncSession, context: CommandContext, slug: str
) -> str:
    team = await _team(session, context.workspace_id, slug)
    users = (
        await session.execute(
            select(User.display_name, User.username)
            .join(TeamMember, TeamMember.user_id == User.id)
            .where(TeamMember.team_id == team.id)
            .order_by(User.display_name, User.id)
        )
    ).all()
    if not users:
        return f"В Team {team.name} [{team.slug}] пока нет пользователей."
    lines = [f"Пользователи Team {team.name} [{team.slug}]:"]
    lines.extend(f"• {_person(name, username)}" for name, username in users)
    return "\n".join(lines)


async def create_stand(
    session: AsyncSession,
    context: CommandContext,
    slug: str,
    stand_name: str,
) -> str:
    _require_role(context, WorkspaceRole.MODERATOR)
    team = await _team(session, context.workspace_id, slug)
    name = stand_name.strip()
    name_key = _key(stand_name, "Имя стенда", 128)
    statement = (
        pg_insert(Stand)
        .values(
            team_id=team.id,
            name=name,
            name_key=name_key,
            created_by_user_id=context.user_id,
        )
        .on_conflict_do_nothing(constraint="uq_stand_team_name_key")
        .returning(Stand.id)
    )
    if (await session.execute(statement)).scalar_one_or_none() is None:
        raise DomainError(f"Стенд «{stand_name}» уже существует в Team [{team.slug}].")
    return f"✅ Стенд «{name}» создан в Team [{team.slug}]."


async def remove_stand(
    session: AsyncSession,
    context: CommandContext,
    slug: str,
    stand_name: str,
) -> str:
    _require_role(context, WorkspaceRole.MODERATOR)
    team = await _team(session, context.workspace_id, slug)
    stand = await _stand(session, team.id, stand_name)
    result = await session.execute(
        delete(Stand).where(Stand.id == stand.id, Stand.occupied_by_user_id.is_(None))
    )
    if not result.rowcount:
        raise DomainError("Нельзя удалить занятый стенд. Сначала освободите его.")
    return f"✅ Стенд «{stand.name}» удалён из Team [{team.slug}]."


async def list_stands(
    session: AsyncSession, context: CommandContext, slug: str
) -> str:
    team = await _team(session, context.workspace_id, slug)
    if context.role == WorkspaceRole.USER and not await _is_team_member(
        session, team.id, context.user_id
    ):
        raise DomainError("Вы не состоите в этой Team.")

    stands = (
        await session.execute(
            select(Stand, User)
            .outerjoin(User, User.id == Stand.occupied_by_user_id)
            .where(Stand.team_id == team.id)
            .order_by(Stand.name_key)
        )
    ).all()
    lines = [f"Team: {team.name} [{team.slug}]"]
    for stand, owner in stands:
        if owner is None:
            lines.append(f"🟢 {stand.name} — свободен")
        else:
            occupied_time = stand.occupied_at.astimezone().strftime("%H:%M")
            lines.append(
                f"🔴 {stand.name} — {_person(owner.display_name, owner.username)}, с {occupied_time}"
            )
    free = sum(1 for stand, _ in stands if stand.occupied_by_user_id is None)
    lines.append(f"Свободно: {free} / {len(stands)}")
    return "\n".join(lines)


async def take_stand(
    session: AsyncSession,
    context: CommandContext,
    slug: str,
    stand_name: str,
    target: TelegramIdentity | None = None,
) -> str:
    team = await _team(session, context.workspace_id, slug)
    if not await _is_team_member(session, team.id, context.user_id):
        raise DomainError("Вы не состоите в этой Team и не можете занимать её стенды.")

    target_user_id = context.user_id
    if target is not None:
        _require_role(context, WorkspaceRole.MODERATOR)
        if target.is_bot:
            raise DomainError("Нельзя занять стенд для бота.")
        target_user_id = await upsert_user(session, target)
        await ensure_workspace_member(session, context.workspace_id, target_user_id)
        if not await _is_team_member(session, team.id, target_user_id):
            raise DomainError("Пользователь из Reply не состоит в этой Team.")

    stand = await _stand(session, team.id, stand_name)
    for _ in range(2):
        acquired = await session.scalar(
            _atomic_take_statement(stand.id, target_user_id)
        )
        if acquired is not None:
            target_user = await session.get(User, target_user_id)
            return f"✅ Стенд «{stand.name}» занят: {_person(target_user.display_name, target_user.username)}."

        owner_id = await session.scalar(
            select(Stand.occupied_by_user_id).where(Stand.id == stand.id)
        )
        if owner_id is None:
            continue
        owner = await session.get(User, owner_id)
        if owner_id == target_user_id:
            return f"ℹ️ Стенд «{stand.name}» уже занят вами/указанным пользователем."
        return f"⛔ Стенд «{stand.name}» уже занят: {_person(owner.display_name, owner.username)}."

    raise DomainError("Состояние стенда только что изменилось. Повторите команду.")


async def release_stand(
    session: AsyncSession,
    context: CommandContext,
    slug: str,
    stand_name: str,
    target: TelegramIdentity | None = None,
) -> str:
    team = await _team(session, context.workspace_id, slug)
    stand = await _stand(session, team.id, stand_name)
    owner_id = await session.scalar(
        select(Stand.occupied_by_user_id).where(Stand.id == stand.id)
    )
    if owner_id is None:
        return f"ℹ️ Стенд «{stand.name}» уже свободен."
    if target is not None:
        _require_role(context, WorkspaceRole.MODERATOR)
        target_user_id = await upsert_user(session, target)
        await ensure_workspace_member(session, context.workspace_id, target_user_id)
        if owner_id != target_user_id:
            owner = await session.get(User, owner_id)
            raise DomainError(
                f"Стенд занят другим пользователем: {_person(owner.display_name, owner.username)}."
            )
    if context.role == WorkspaceRole.USER and owner_id != context.user_id:
        owner = await session.get(User, owner_id)
        raise DomainError(
            f"Стенд занят пользователем {_person(owner.display_name, owner.username)}. "
            "Освободить чужой стенд может только модератор или администратор."
        )

    released = await session.scalar(
        update(Stand)
        .where(Stand.id == stand.id, Stand.occupied_by_user_id == owner_id)
        .values(occupied_by_user_id=None, occupied_at=None, updated_at=func.now())
        .returning(Stand.id)
    )
    if released is None:
        raise DomainError("Состояние стенда только что изменилось. Повторите команду.")
    return f"✅ Стенд «{stand.name}» освобождён."

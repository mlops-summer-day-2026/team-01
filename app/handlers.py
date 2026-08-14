from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Chat, ChatMemberUpdated, Message, User as TelegramUser
from aiogram.enums import ChatMemberStatus, ChatType
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import services
from app.models import WorkspaceRole


HELP_TEXT = """Управление общими стендами

Общие команды:
/teams — Team этого чата
/users — известные боту пользователи
/team_users <team>
/stands <team>
/free_stands [team] — свободные стенды
/my_stands — занятые вами стенды
/take_stand <team> <stand>
/untake_stand <team> <stand>

Модератор/администратор:
/add_user <team> — Reply на сообщение пользователя
/remove_user <team> — Reply на сообщение пользователя
/create_stand <team> <stand>
/remove_stand <team> <stand>

Администратор:
/create_team <slug> [название]
/delete_team <team>
/set_role <USER|MODERATOR|ADMIN> — Reply на сообщение пользователя"""


class DatabaseSessionMiddleware(BaseMiddleware):
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def __call__(
        self,
        handler: Callable[[object, dict[str, object]], Awaitable[object]],
        event: object,
        data: dict[str, object],
    ) -> object:
        async with self._sessions() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise


def _identity(user: TelegramUser) -> services.TelegramIdentity:
    return services.TelegramIdentity(
        telegram_user_id=user.id,
        username=user.username,
        display_name=user.full_name,
        is_bot=user.is_bot,
    )


def _args(command: CommandObject) -> str:
    return (command.args or "").strip()


def _one_arg(command: CommandObject, usage: str) -> str:
    parts = _args(command).split()
    if len(parts) != 1:
        raise services.DomainError(f"Использование: {usage}")
    return parts[0]


def _two_args(command: CommandObject, usage: str) -> tuple[str, str]:
    parts = _args(command).split()
    if len(parts) != 2:
        raise services.DomainError(f"Использование: {usage}")
    return parts[0], parts[1]


async def _context(
    message: Message, session: AsyncSession
) -> services.CommandContext | None:
    if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await message.answer("Добавьте бота в группу: каждый групповой чат — отдельный Workspace.")
        return None
    if message.from_user is None:
        await message.answer("Не удалось определить пользователя. Отключите анонимный режим администратора.")
        return None
    return await services.ensure_command_context(
        session,
        telegram_chat_id=message.chat.id,
        chat_title=message.chat.title,
        actor=_identity(message.from_user),
    )


async def _answer(message: Message, result: Awaitable[str]) -> None:
    try:
        text = await result
    except services.DomainError as error:
        text = f"⚠️ {error}"
    await message.answer(text)


def _reply_target(message: Message) -> services.TelegramIdentity | None:
    reply = message.reply_to_message
    if reply is None or reply.from_user is None:
        return None
    return _identity(reply.from_user)


async def _bootstrap_administrators(
    session: AsyncSession, bot: Bot, chat: Chat
) -> None:
    members = await bot.get_chat_administrators(chat.id)
    administrators: list[tuple[services.TelegramIdentity, WorkspaceRole]] = []
    for member in members:
        role = (
            WorkspaceRole.ADMIN
            if member.status == ChatMemberStatus.CREATOR
            else WorkspaceRole.MODERATOR
        )
        administrators.append((_identity(member.user), role))
    await services.bootstrap_workspace(
        session,
        telegram_chat_id=chat.id,
        chat_title=chat.title,
        administrators=administrators,
    )


def create_router(sessions: async_sessionmaker[AsyncSession]) -> Router:
    router = Router(name="stand-manager")
    middleware = DatabaseSessionMiddleware(sessions)
    router.message.outer_middleware(middleware)
    router.my_chat_member.outer_middleware(middleware)

    @router.my_chat_member()
    async def bot_membership_changed(
        event: ChatMemberUpdated, session: AsyncSession, bot: Bot
    ) -> None:
        was_outside = event.old_chat_member.status in {
            ChatMemberStatus.LEFT,
            ChatMemberStatus.KICKED,
        }
        is_inside = event.new_chat_member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
        }
        if (
            event.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}
            or not was_outside
            or not is_inside
        ):
            return
        await _bootstrap_administrators(session, bot, event.chat)
        await bot.send_message(
            event.chat.id,
            "👋 Workspace готов. Владельцу назначена роль ADMIN, остальным администраторам — MODERATOR.\n"
            "Начните с /help и /create_team <slug> [название].",
        )

    @router.message(CommandStart())
    async def start(message: Message, session: AsyncSession, bot: Bot) -> None:
        context = await _context(message, session)
        if context is None:
            return
        await _bootstrap_administrators(session, bot, message.chat)
        await message.answer("Workspace готов.\n\n" + HELP_TEXT)

    @router.message(Command("help"))
    async def help_command(message: Message, session: AsyncSession) -> None:
        if await _context(message, session) is not None:
            await message.answer(HELP_TEXT)

    @router.message(Command("teams"))
    async def teams(message: Message, session: AsyncSession) -> None:
        context = await _context(message, session)
        if context:
            await _answer(message, services.list_teams(session, context))

    @router.message(Command("users"))
    async def users(message: Message, session: AsyncSession) -> None:
        context = await _context(message, session)
        if context:
            await _answer(message, services.list_users(session, context))

    @router.message(Command("set_role"))
    async def set_role(
        message: Message, command: CommandObject, session: AsyncSession
    ) -> None:
        context = await _context(message, session)
        if context is None:
            return
        target = _reply_target(message)
        try:
            role_name = _one_arg(
                command,
                "/set_role <USER|MODERATOR|ADMIN> (Reply на сообщение пользователя)",
            )
            if target is None:
                raise services.DomainError(
                    "Команда /set_role должна быть Reply на сообщение пользователя."
                )
        except services.DomainError as error:
            await message.answer(f"⚠️ {error}")
            return
        await _answer(
            message,
            services.set_workspace_role(session, context, target, role_name),
        )

    @router.message(Command("my_stands"))
    async def my_stands(message: Message, session: AsyncSession) -> None:
        context = await _context(message, session)
        if context:
            await _answer(message, services.list_my_stands(session, context))

    @router.message(Command("free_stands"))
    async def free_stands(
        message: Message, command: CommandObject, session: AsyncSession
    ) -> None:
        context = await _context(message, session)
        if context is None:
            return
        try:
            parts = _args(command).split()
            if len(parts) > 1:
                raise services.DomainError("Использование: /free_stands [team]")
            slug = parts[0] if parts else None
        except services.DomainError as error:
            await message.answer(f"⚠️ {error}")
            return
        await _answer(message, services.list_free_stands(session, context, slug))

    @router.message(Command("create_team"))
    async def create_team(
        message: Message, command: CommandObject, session: AsyncSession
    ) -> None:
        context = await _context(message, session)
        if context is None:
            return
        try:
            parts = _args(command).split(maxsplit=1)
            if not parts:
                raise services.DomainError("Использование: /create_team <slug> [название]")
            slug = parts[0]
            name = parts[1] if len(parts) == 2 else None
            result = services.create_team(session, context, slug, name)
        except services.DomainError as error:
            await message.answer(f"⚠️ {error}")
            return
        await _answer(message, result)

    @router.message(Command("delete_team"))
    async def delete_team(
        message: Message, command: CommandObject, session: AsyncSession
    ) -> None:
        context = await _context(message, session)
        if context is None:
            return
        try:
            slug = _one_arg(command, "/delete_team <team>")
        except services.DomainError as error:
            await message.answer(f"⚠️ {error}")
            return
        await _answer(message, services.delete_team(session, context, slug))

    @router.message(Command("add_user"))
    async def add_user(
        message: Message, command: CommandObject, session: AsyncSession
    ) -> None:
        context = await _context(message, session)
        if context is None:
            return
        target = _reply_target(message)
        try:
            slug = _one_arg(command, "/add_user <team> (Reply на сообщение пользователя)")
            if target is None:
                raise services.DomainError("Команда /add_user должна быть Reply на сообщение пользователя.")
        except services.DomainError as error:
            await message.answer(f"⚠️ {error}")
            return
        await _answer(message, services.add_user_to_team(session, context, slug, target))

    @router.message(Command("remove_user"))
    async def remove_user(
        message: Message, command: CommandObject, session: AsyncSession
    ) -> None:
        context = await _context(message, session)
        if context is None:
            return
        target = _reply_target(message)
        try:
            slug = _one_arg(command, "/remove_user <team> (Reply на сообщение пользователя)")
            if target is None:
                raise services.DomainError("Команда /remove_user должна быть Reply на сообщение пользователя.")
        except services.DomainError as error:
            await message.answer(f"⚠️ {error}")
            return
        await _answer(message, services.remove_user_from_team(session, context, slug, target))

    @router.message(Command("team_users"))
    async def team_users(
        message: Message, command: CommandObject, session: AsyncSession
    ) -> None:
        context = await _context(message, session)
        if context is None:
            return
        try:
            slug = _one_arg(command, "/team_users <team>")
        except services.DomainError as error:
            await message.answer(f"⚠️ {error}")
            return
        await _answer(message, services.list_team_users(session, context, slug))

    @router.message(Command("create_stand"))
    async def create_stand(
        message: Message, command: CommandObject, session: AsyncSession
    ) -> None:
        context = await _context(message, session)
        if context is None:
            return
        try:
            slug, stand_name = _two_args(command, "/create_stand <team> <stand>")
        except services.DomainError as error:
            await message.answer(f"⚠️ {error}")
            return
        await _answer(message, services.create_stand(session, context, slug, stand_name))

    @router.message(Command("remove_stand"))
    async def remove_stand(
        message: Message, command: CommandObject, session: AsyncSession
    ) -> None:
        context = await _context(message, session)
        if context is None:
            return
        try:
            slug, stand_name = _two_args(command, "/remove_stand <team> <stand>")
        except services.DomainError as error:
            await message.answer(f"⚠️ {error}")
            return
        await _answer(message, services.remove_stand(session, context, slug, stand_name))

    @router.message(Command("stands"))
    async def stands(
        message: Message, command: CommandObject, session: AsyncSession
    ) -> None:
        context = await _context(message, session)
        if context is None:
            return
        try:
            slug = _one_arg(command, "/stands <team>")
        except services.DomainError as error:
            await message.answer(f"⚠️ {error}")
            return
        await _answer(message, services.list_stands(session, context, slug))

    @router.message(Command("take_stand"))
    async def take_stand(
        message: Message, command: CommandObject, session: AsyncSession
    ) -> None:
        context = await _context(message, session)
        if context is None:
            return
        try:
            slug, stand_name = _two_args(command, "/take_stand <team> <stand>")
        except services.DomainError as error:
            await message.answer(f"⚠️ {error}")
            return
        await _answer(
            message,
            services.take_stand(
                session, context, slug, stand_name, _reply_target(message)
            ),
        )

    @router.message(Command("untake_stand"))
    async def untake_stand(
        message: Message, command: CommandObject, session: AsyncSession
    ) -> None:
        context = await _context(message, session)
        if context is None:
            return
        try:
            slug, stand_name = _two_args(command, "/untake_stand <team> <stand>")
        except services.DomainError as error:
            await message.answer(f"⚠️ {error}")
            return
        await _answer(
            message,
            services.release_stand(
                session, context, slug, stand_name, _reply_target(message)
            ),
        )

    return router

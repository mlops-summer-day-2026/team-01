from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from app.config import Settings
from app.db import Database
from app.handlers import create_router


COMMANDS = [
    BotCommand(command="help", description="справка"),
    BotCommand(command="teams", description="список Team"),
    BotCommand(command="users", description="известные пользователи"),
    BotCommand(command="stands", description="стенды Team"),
    BotCommand(command="free_stands", description="свободные стенды"),
    BotCommand(command="my_stands", description="мои занятые стенды"),
    BotCommand(command="take_stand", description="занять стенд"),
    BotCommand(command="untake_stand", description="освободить стенд"),
    BotCommand(command="team_users", description="пользователи Team"),
    BotCommand(command="create_team", description="создать Team (ADMIN)"),
    BotCommand(command="delete_team", description="удалить Team (ADMIN)"),
    BotCommand(command="set_role", description="назначить роль (ADMIN)"),
    BotCommand(command="add_user", description="добавить Reply-пользователя"),
    BotCommand(command="remove_user", description="удалить Reply-пользователя"),
    BotCommand(command="create_stand", description="создать стенд"),
    BotCommand(command="remove_stand", description="удалить стенд"),
]


async def run() -> None:
    settings = Settings.from_env()
    database = Database(settings.database_url)
    bot = Bot(settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router(database.sessions))

    try:
        await database.create_schema()
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.set_my_commands(COMMANDS)
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
        await database.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()

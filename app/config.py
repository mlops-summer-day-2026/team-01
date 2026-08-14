from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    database_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        database_url = os.getenv("DATABASE_URL", "").strip()

        missing = [
            name
            for name, value in (
                ("TELEGRAM_BOT_TOKEN", token),
                ("DATABASE_URL", database_url),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Не заданы обязательные переменные окружения: " + ", ".join(missing)
            )

        if database_url.startswith("postgresql://"):
            database_url = database_url.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
        if not database_url.startswith("postgresql+asyncpg://"):
            raise RuntimeError("DATABASE_URL должен указывать на PostgreSQL через asyncpg")

        return cls(telegram_bot_token=token, database_url=database_url)

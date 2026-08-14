from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


DEFAULT_WOW_PUBLIC_URL = (
    "https://mlops-summer-day-2026.github.io/team-01/boss-mode/"
)


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    database_url: str
    wow_public_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        database_url = os.getenv("DATABASE_URL", "").strip()
        wow_public_url = os.getenv(
            "WOW_PUBLIC_URL", DEFAULT_WOW_PUBLIC_URL
        ).strip()

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
        if not wow_public_url.startswith(("https://", "http://")):
            raise RuntimeError("WOW_PUBLIC_URL должен быть HTTP(S)-адресом")

        return cls(
            telegram_bot_token=token,
            database_url=database_url,
            wow_public_url=wow_public_url,
        )

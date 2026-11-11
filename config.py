# -*- coding: utf-8 -*-
"""Настройки бота. Все значения читаются из .env рядом с bot.py."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_NAME = os.getenv("BOT_NAME", "GmodRP").strip() or "GmodRP"

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

# Один или несколько серверов, для которых команды синхронизируются сразу.
# GUILD_IDS=123,456 — тестовый и основной сервер одновременно.
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
GUILD_IDS = [
    int(part)
    for part in os.getenv("GUILD_IDS", "").replace(" ", "").split(",")
    if part.strip().isdigit()
]
if not GUILD_IDS and GUILD_ID:
    GUILD_IDS = [GUILD_ID]

OWNER_IDS = {
    int(part)
    for part in os.getenv("OWNER_IDS", "").replace(" ", "").split(",")
    if part.strip().isdigit()
}

DB_PATH = BASE_DIR / os.getenv("DB_PATH", "data/audit.db")

# Файл лога рядом с ботом (пишется всегда, даже если консоль закрыта)
LOG_PATH = BASE_DIR / os.getenv("LOG_PATH", "bot.log")

# compact | color
DEFAULT_STYLE = os.getenv("DEFAULT_STYLE", "color").strip().lower()
if DEFAULT_STYLE not in ("compact", "color"):
    DEFAULT_STYLE = "color"

# Часовой пояс для дат в сообщениях (смещение от UTC в часах)
TIMEZONE_OFFSET = int(os.getenv("TIMEZONE_OFFSET", "3") or 3)

# Сколько варнов считается пределом (можно менять командой /лимит)
try:
    WARN_LIMIT = max(1, min(10, int(os.getenv("WARN_LIMIT", "3") or 3)))
except ValueError:
    WARN_LIMIT = 3

# Названия ролей, по которым бот понимает границу администрации.
# При снятии удаляется всё от HELPER_ROLE_NAME и выше, затем выдаётся PLAYER_ROLE_NAME.
HELPER_ROLE_NAME = os.getenv("HELPER_ROLE_NAME", "Хелпер").strip() or "Хелпер"
PLAYER_ROLE_NAME = os.getenv("PLAYER_ROLE_NAME", "Игрок").strip() or "Игрок"

ADMIN_LOG_CHANNEL = os.getenv("ADMIN_LOG_CHANNEL", "").strip()

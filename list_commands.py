# -*- coding: utf-8 -*-
"""Проверка дерева слэш-команд без подключения к Discord."""

import bot

for cmd in bot.bot.tree.get_commands():
    if hasattr(cmd, "commands"):
        print(f"/{cmd.name}")
        for sub in cmd.commands:
            print(f"    /{cmd.name} {sub.name} — {sub.description}")
    else:
        print(f"/{cmd.name} — {cmd.description}")

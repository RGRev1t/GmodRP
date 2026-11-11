# -*- coding: utf-8 -*-
"""Перенос данных бота с тестового сервера на основной.

Каналы и роли не переносятся: их ID на другом сервере другие.
После переноса привяжите каналы командой `/канал` — роли бот берёт
из самого сервера, когда вы выбираете их в командах.

Примеры:

    python migrate.py --list
    python migrate.py --from 1569743432887308442 --to 123456789012345678
    python migrate.py --from 1569743432887308442 --to 123456789012345678 --overwrite
"""

from __future__ import annotations

import argparse

from database import Database


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Перенос данных администрации между серверами Discord."
    )
    parser.add_argument("--list", action="store_true", help="показать серверы в базе")
    parser.add_argument("--from", dest="src", type=int, help="откуда переносим (guild_id)")
    parser.add_argument("--to", dest="dst", type=int, help="куда переносим (guild_id)")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="удалить данные целевого сервера перед импортом",
    )
    args = parser.parse_args()

    db = Database()

    if args.list or not (args.src and args.dst):
        rows = db.known_guilds()
        if not rows:
            print("В базе нет данных ни по одному серверу.")
            return 0
        print(f"{'guild_id':>20}  {'админов':>8}  {'действий':>9}")
        for row in rows:
            mark = ""
            if args.src and row["guild_id"] == args.src:
                mark = "  <- откуда"
            if args.dst and row["guild_id"] == args.dst:
                mark = "  <- куда"
            print(f"{row['guild_id']:>20}  {row['admins']:>8}  {row['actions']:>9}{mark}")
        if not (args.src and args.dst):
            print("\nУкажите --from и --to, чтобы выполнить перенос.")
        return 0

    if args.src == args.dst:
        print("Ошибка: --from и --to совпадают.")
        return 1

    data = db.export_guild(args.src)
    if not any(data.values()):
        print(f"На сервере {args.src} нет данных.")
        return 1

    if args.overwrite:
        answer = input(
            f"Удалить ВСЕ данные сервера {args.dst} и заменить их данными {args.src}? [y/N] "
        ).strip().lower()
        if answer not in ("y", "yes", "д", "да"):
            print("Отменено.")
            return 1

    counts = db.import_guild(args.dst, data, overwrite=args.overwrite)
    print(f"Перенос {args.src} -> {args.dst} завершён:")
    for key, value in counts.items():
        print(f"  {key:<12} {value}")

    print(
        "\nКаналы и роли не переносятся — их ID на другом сервере другие.\n"
        "\nНе забудьте на новом сервере:\n"
        "  1. Привязать каналы: /канал\n"
        "  2. Поднять роль бота выше всех ролей администрации\n"
        "  3. Проверить роли «Хелпер» и «Игрок» (или задать их имена в .env)\n"
        "  4. Проверить себя: /статус\n"
        "  5. Обновить должности: /повышение или /понижение с нужной ролью\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""Формирование сообщений в двух стилях: compact (текст) и color (embed)."""

from __future__ import annotations

import discord

import config
from database import fmt_dt

BOT_NAME = config.BOT_NAME

# action -> (заголовок, цвет, глагол для общих логов, значок)
ACTIONS: dict[str, tuple[str, int, str, str]] = {
    "accept": ("Принятие администратора", 0x2ECC71, "принял(а) в администрацию", "🟢"),
    "promote": ("Повышение", 0x3B82F6, "повысил(а)", "🔵"),
    "demote": ("Понижение", 0xF59E0B, "понизил(а)", "🟠"),
    "remove": ("Снятие администратора", 0xE74C3C, "снял(а) с должности", "🔴"),
    "warn_add": ("Выдача варна", 0xE67E22, "выдал(а) варн", "⚠️"),
    "warn_remove": ("Снятие варна", 0x2ECC71, "снял(а) варн", "♻️"),
    "notice_add": ("Предупреждение", 0xF1C40F, "выдал(а) предупреждение", "📝"),
    "notice_remove": ("Снятие предупреждения", 0x2ECC71, "снял(а) предупреждение", "🧹"),
    "bl_add": ("Чёрный список проекта", 0x8B0000, "занёс(ла) в ЧСП", "⛔"),
    "bl_remove": ("Удаление из ЧСП", 0x2ECC71, "убрал(а) из ЧСП", "♻️"),
    "role_add": ("Выдача роли", 0x9B59B6, "выдал(а) роль", "🎭"),
}

DEFAULT_TITLE = "Администрация"
DEFAULT_COLOR = 0xF8141B

COMMANDS = {
    "accept": "принять",
    "promote": "повышение",
    "demote": "понижение",
    "remove": "снять",
    "warn_add": "выдать-варн",
    "warn_remove": "убрать-варн",
    "notice_add": "выдать-предупреждение",
    "notice_remove": "убрать-предупреждение",
    "bl_add": "выдать-чсп",
    "bl_remove": "убрать-чсп",
    "role_add": "выдать-роль",
}


def action_meta(action: str) -> tuple[str, int, str, str]:
    return ACTIONS.get(action, (DEFAULT_TITLE, DEFAULT_COLOR, action, "•"))


def _val(value: str | None, empty: str = "Не указан") -> str:
    value = (value or "").strip()
    return value if value else empty


def _steam(value: str | None) -> str:
    return f"`{_val(value)}`" if (value or "").strip() else "не указан"


def event_message(
    style: str,
    action: str,
    target,
    actor,
    *,
    nickname: str | None = None,
    steamid: str | None = None,
    role_text: str | None = None,
    reason: str | None = None,
    when: str | None = None,
    extra: list[tuple[str, str]] | None = None,
) -> tuple[str, discord.Embed | None]:
    """Возвращает (content, embed). Для compact embed = None."""
    title, color, _, icon = action_meta(action)
    when = when or fmt_dt(None)

    rows: list[tuple[str, str]] = [
        ("Тег", target.mention if target else "—"),
        ("Имя", _val(nickname, "Ник не указан")),
        ("SteamID", _steam(steamid)),
    ]
    if role_text:
        rows.append(("Должность", role_text))

    action_text = {
        "accept": "Принят(а) в состав администрации.",
        "promote": "Повышен(а).",
        "demote": "Понижен(а).",
        "remove": "Снят(а) с должности администратора.",
        "warn_add": "Выдан варн.",
        "warn_remove": "Варн снят.",
        "notice_add": "Выдано предупреждение.",
        "notice_remove": "Предупреждение снято.",
        "bl_add": "Занесён(а) в чёрный список проекта.",
        "bl_remove": "Убран(а) из чёрного списка проекта.",
        "role_add": "Выдана роль.",
    }.get(action, "—")
    rows.append(("Действие", action_text))

    for label, value in extra or []:
        rows.append((label, value))
    if reason:
        rows.append(("Причина", reason))
    rows.append(("Автор", actor.mention if actor else "—"))
    rows.append(("Дата", when))

    if style == "compact":
        cmd = COMMANDS.get(action, action.replace("_", " "))
        body = [f"{actor.mention} использует `/{cmd}`" if actor else f"`/{cmd}`", "", f"**{title}**"]
        body += [f"**{label}:** {value}" for label, value in rows]
        return "\n".join(body), None

    embed = discord.Embed(title=f"{icon} {title}", color=color, timestamp=discord.utils.utcnow())
    embed.set_author(name=f"{BOT_NAME} · Администрация")
    for label, value in rows:
        inline = label in ("Имя", "SteamID", "Должность")
        embed.add_field(name=label, value=str(value)[:1024], inline=inline)
    embed.set_footer(text=f"{BOT_NAME} • {when}")
    return "", embed


def short_log_line(
    action: str, target, actor, *, nickname: str | None, reason: str | None
) -> str:
    """Строка для канала общих логов."""
    _, _, verb, icon = action_meta(action)
    who = f"**{nickname}**" if nickname else (target.mention if target else "—")
    line = f"{icon} `{BOT_NAME}` — {actor.mention} {verb} {who}"
    if reason:
        line += f" · причина: {reason}"
    return line


def logs_request_line(actor, target) -> str:
    """Уведомление в общие логи о запросе истории администратора."""
    who = target.mention if target else "—"
    return f"📋 `{BOT_NAME}` — {actor.mention} запросил(а) логи администратора {who}."


def punish_card(
    style: str,
    kind: str,
    target,
    actor,
    *,
    nickname: str | None,
    steamid: str | None,
    reason: str | None,
    count: int,
    limit: int,
    removed: bool = False,
    when: str | None = None,
) -> tuple[str, discord.Embed | None]:
    """Карточка варна/предупреждения (выдача или снятие)."""
    when = when or fmt_dt(None)
    if kind == "warn":
        title = "♻️ Снятие варна" if removed else "⚠️ Выдача варна"
        color = 0x2ECC71 if removed else 0xE67E22
        label, amount = "Варны", f"{count}/{limit}"
    else:
        title = "🧹 Снятие предупреждения" if removed else "📝 Предупреждение"
        color = 0x2ECC71 if removed else 0xF1C40F
        label, amount = "Предупреждения", str(count)

    rows: list[tuple[str, str]] = [
        ("Тег", target.mention if target else "—"),
        ("Имя", _val(nickname, "Ник не указан")),
        ("SteamID", _steam(steamid)),
        (label, amount),
        ("Причина", _val(reason, "Не указана")),
        ("Автор", actor.mention if actor else "—"),
        ("Дата", when),
    ]

    if style == "compact":
        body = [f"**{title}**"] + [f"**{k}:** {v}" for k, v in rows]
        return "\n".join(body), None

    embed = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
    embed.set_author(name=f"{BOT_NAME} · Администрация")
    for key, value in rows:
        embed.add_field(name=key, value=str(value)[:1024], inline=key in ("Имя", "SteamID", label))
    embed.set_footer(text=f"{BOT_NAME} • {when}")
    return "", embed


def info_card(
    member_row: dict | None,
    user,
    role_text: str | None,
    actions_count: int,
    warns: int,
    notices: int,
    warn_limit: int,
    blacklisted: bool,
) -> discord.Embed:
    embed = discord.Embed(title="ℹ️ Карточка администратора", color=0x5865F2)
    embed.set_author(name=f"{BOT_NAME} · Администрация")
    embed.add_field(name="Тег", value=user.mention if user else "—", inline=False)
    embed.add_field(name="Имя", value=_val((member_row or {}).get("nickname"), "Ник не указан"), inline=True)
    embed.add_field(name="SteamID", value=_steam((member_row or {}).get("steamid")), inline=True)

    status = (member_row or {}).get("status", "—")
    status_text = {"active": "🟢 В составе", "removed": "🔴 Снят(а)"}.get(
        status, "не в составе" if member_row is None else str(status)
    )

    embed.add_field(name="Должность", value=role_text or "Не назначена", inline=True)
    embed.add_field(name="Статус", value=status_text, inline=True)
    embed.add_field(name="Варны", value=f"⚠️ {warns}/{warn_limit}", inline=True)
    embed.add_field(name="Предупреждения", value=str(notices), inline=True)
    embed.add_field(name="Действий", value=str(actions_count), inline=True)
    embed.add_field(name="В составе с", value=fmt_dt((member_row or {}).get("joined_at")), inline=True)
    if blacklisted:
        embed.add_field(name="⛔ ЧСП", value="Активен", inline=True)
    embed.set_footer(text=f"{BOT_NAME} • администрация")
    return embed


def logs_embed(
    target,
    member_row: dict | None,
    role_text: str | None,
    actions: list[dict],
    *,
    page: int,
    per_page: int = 8,
) -> discord.Embed:
    total_pages = max(1, (len(actions) + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    chunk = actions[(page - 1) * per_page: page * per_page]

    embed = discord.Embed(
        title="📋 История администратора",
        color=DEFAULT_COLOR,
        description=(
            f"Администратор: {target.mention}\n"
            f"Имя: **{_val((member_row or {}).get('nickname'), 'Ник не указан')}** · "
            f"SteamID: {_steam((member_row or {}).get('steamid'))}\n"
            f"Должность: **{role_text or '—'}**"
        ),
    )
    embed.set_author(name=f"{BOT_NAME} · Администрация")

    if not actions:
        embed.add_field(name="Пусто", value="По этому администратору ещё не было действий.", inline=False)
    for row in chunk:
        title, _, _, act_icon = action_meta(row["action"])
        detail = row.get("detail") or ""
        reason = row.get("reason")
        text = f"{act_icon} **{title}**\n"
        if detail:
            text += f"{detail}\n"
        if reason:
            text += f"Причина: {reason}\n"
        text += f"Автор: <@{row['actor_id']}> · {fmt_dt(row['created_at'])}"
        embed.add_field(name="\u200b", value=text[:1024], inline=False)

    embed.set_footer(text=f"{BOT_NAME} • страница {page}/{total_pages} · всего {len(actions)}")
    return embed


def blacklist_card(
    style: str,
    target,
    actor,
    *,
    nickname: str | None,
    steamid: str | None,
    reason: str | None,
    when: str | None = None,
) -> tuple[str, discord.Embed | None]:
    """Карточка занесения в чёрный список проекта (ЧСП)."""
    when = when or fmt_dt(None)
    rows: list[tuple[str, str]] = [
        ("Тег", target.mention if target else "—"),
        ("Имя", _val(nickname, "Ник не указан")),
        ("SteamID", _steam(steamid)),
        ("Причина", _val(reason, "Не указана")),
        ("Автор", actor.mention if actor else "—"),
        ("Дата", when),
    ]

    if style == "compact":
        body = ["**⛔ Чёрный список проекта**"]
        body += [f"**{k}:** {v}" for k, v in rows]
        return "\n".join(body), None

    embed = discord.Embed(title="⛔ Чёрный список проекта", color=0x8B0000,
                          timestamp=discord.utils.utcnow())
    embed.set_author(name=f"{BOT_NAME} · Администрация")
    for key, value in rows:
        embed.add_field(name=key, value=str(value)[:1024], inline=key in ("Имя", "SteamID"))
    embed.set_footer(text=f"{BOT_NAME} • {when}")
    return "", embed


def blacklist_embed(rows: list[dict]) -> discord.Embed:
    embed = discord.Embed(title="⛔ Чёрный список проекта", color=0x8B0000)
    embed.set_author(name=f"{BOT_NAME} · Администрация")
    if not rows:
        embed.description = "ЧСП пуст."
        return embed

    for row in rows[:20]:
        who = f"<@{row['user_id']}>" if row.get("user_id") else _val(row.get("nickname"), "без ника")
        text = f"Имя: {_val(row.get('nickname'), 'не указано')}\n"
        text += f"SteamID: {_steam(row.get('steamid'))}\n"
        text += f"Причина: {_val(row.get('reason'), 'не указана')}\n"
        text += f"Добавил(а): <@{row.get('added_by')}> · {fmt_dt(row.get('added_at'))}"
        embed.add_field(name=who[:256], value=text[:1024], inline=False)

    embed.set_footer(text=f"{BOT_NAME} • всего в списке: {len(rows)}")
    return embed


RELEASE_SECTIONS: tuple[tuple[str, str], ...] = (
    ("added", "🆕 Добавили"),
    ("changed", "🔧 Изменили"),
    ("fixed", "🐞 Исправили"),
    ("removed", "🗑 Убрали"),
)


def release_embed(
    *,
    version: str | None,
    build: int | None,
    title: str,
    intro: str | None = None,
    sections: dict[str, str] | None = None,
    author=None,
) -> discord.Embed:
    """Карточка обновления по шаблону: версия, билд и разделы изменений."""
    embed = discord.Embed(
        title=f"📰 {title}"[:256],
        color=0x57F287,
        description=(intro or "").strip()[:3800] or None,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=f"{BOT_NAME} · Обновления")

    for key, label in RELEASE_SECTIONS:
        value = ((sections or {}).get(key) or "").strip()
        if value:
            embed.add_field(name=label, value=value[:1024], inline=False)

    footer: list[str] = []
    if version:
        footer.append(f"Версия {version}")
    if build:
        footer.append(f"build #{build}")
    if author is not None:
        name = getattr(author, "display_name", None) or getattr(author, "name", None) or "автор"
        footer.append(f"опубликовал(а) {name}")
    embed.set_footer(text=" • ".join(footer) or BOT_NAME)
    return embed


def news_embed(
    title: str,
    lines: list[str],
    author=None,
    *,
    footer: str | None = None,
) -> discord.Embed:
    """Карточка новости/обновления для канала новостей."""
    embed = discord.Embed(
        title=f"📰 {title}"[:256],
        color=0x5865F2,
        description="\n".join(line for line in lines if line is not None)[:4000],
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=f"{BOT_NAME} · Обновления")
    if author is not None:
        name = getattr(author, "display_name", None) or getattr(author, "name", None) or "автор"
        embed.set_footer(text=f"{BOT_NAME} • {name}")
    else:
        embed.set_footer(text=footer or BOT_NAME)
    return embed


def help_embed() -> discord.Embed:
    embed = discord.Embed(
        title=f"{BOT_NAME} · Администрация",
        color=DEFAULT_COLOR,
        description=(
            "Бот ведёт состав администрации: приём, повышение, понижение, снятие, "
            "роли, варны, предупреждения и чёрный список проекта (ЧСП).\n"
            "Шаблонов нет: роль выбирается из ролей, которые уже есть на сервере."
        ),
    )
    embed.add_field(
        name="Состав",
        value=(
            "`/принять` — принять (SteamID и роль обязательны)\n"
            "`/повышение` — повысить (SteamID и роль обязательны)\n"
            "`/понижение` — понизить (роль обязательна)\n"
            "`/снять` — снять с должности (SteamID обязателен)\n"
            "`/выдать-роль` — выдать роль администратору\n"
            "`/поиск` — поиск админа по нику, SteamID или ID\n"
            "`/инфо` — карточка администратора"
        ),
        inline=False,
    )
    embed.add_field(
        name="Дисциплина",
        value=(
            "`/выдать-варн` · `/убрать-варн`\n"
            "`/выдать-предупреждение` · `/убрать-предупреждение`\n"
            "`/выдать-чсп` · `/убрать-чсп`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Настройка",
        value=(
            "`/канал` — каналы аудита, варнов, ЧСП, логов и новостей\n"
            "`/новость` — обновление по шаблону: версия, билд и разделы изменений\n"
            "`/новость версия: 2.6` — крупная версия вручную; без версии патч +1\n"
            "`/стиль` — компактный или цветной\n"
            "`/лимит` — сколько варнов до предела"
        ),
        inline=False,
    )
    embed.set_footer(text=f"{BOT_NAME}")
    return embed

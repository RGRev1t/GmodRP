# -*- coding: utf-8 -*-
"""GmodRP · Администрация — Discord-бот.

Короткие плоские команды:
    /принять  /повышение  /понижение  /снять  /выдать-роль
    /выдать-варн  /убрать-варн
    /выдать-предупреждение  /убрать-предупреждение
    /выдать-чсп  /убрать-чсп
    /поиск  /инфо  /канал  /стиль  /лимит  /помощь  /статус

Запуск:   python bot.py
Самотест: python bot.py --selftest   (БД и тексты, без подключения к Discord)
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.abc import GuildChannel
from discord.ext import commands

import config
import formatting as fmt
import updates
from database import Database, fmt_dt, now_iso
from views import ConfirmView, LogsView

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_PATH, encoding="utf-8", mode="a"),
        logging.StreamHandler(sys.stdout),
    ],
)
try:
    # Консоль Windows может не уметь в кириллицу — не даём логу уронить бота.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
log = logging.getLogger("gmodrp")


# --------------------------------------------------------------------- бот
class GmodRPBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.db = Database(config.DB_PATH)
        self.started = datetime.now(timezone.utc)

    async def setup_hook(self) -> None:
        if config.GUILD_IDS:
            for gid in config.GUILD_IDS:
                guild = discord.Object(id=gid)
                try:
                    self.tree.copy_global_to(guild=guild)
                    synced = await self.tree.sync(guild=guild)
                    log.info("Команды синхронизированы для сервера %s: %s шт.", gid, len(synced))
                except Exception:
                    log.error(
                        "Не удалось синхронизировать команды для сервера %s. "
                        "Бот добавлен на этот сервер? Проверьте GUILD_IDS в .env.",
                        gid,
                        exc_info=True,
                    )
        else:
            try:
                synced = await self.tree.sync()
                log.info("Глобальные команды синхронизированы: %s шт. (может занять до часа)", len(synced))
            except Exception:
                log.exception("Не удалось синхронизировать команды")

    async def on_ready(self) -> None:
        log.info("Вошёл как %s (%s)", self.user, self.user.id)
        if not self.guilds:
            log.error(
                "БОТ НЕ ДОБАВЛЕН НИ НА ОДИН СЕРВЕР! Ссылка приглашения должна содержать "
                "scope=bot+applications.commands (сейчас бот установлен только как приложение)."
            )
        else:
            log.info("Серверов: %s — %s", len(self.guilds), ", ".join(g.name for g in self.guilds))
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="администрацию")
        )
        for guild in self.guilds:
            try:
                await maybe_post_update(guild)
            except Exception:
                log.warning("Не удалось опубликовать обновление для сервера %s", guild.id, exc_info=True)


bot = GmodRPBot()


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    """Ловит ошибки ВСЕХ слэш-команд, включая разбор аргументов.

    Без этого Discord пишет «Приложение не отвечает», потому что ошибка
    происходит до вызова команды и никто не отвечает на interaction.
    """
    log.error("Ошибка команды %s", getattr(interaction.command, "qualified_name", "?"), exc_info=error)

    if isinstance(error, app_commands.TransformerError):
        text = (
            "Не удалось принять аргумент команды.\n\n"
            "Для канала подходит только текстовый канал или форум; "
            "для роли — обычная роль сервера, не @everyone. "
            "Проверьте, что бот видит канал и стоит выше нужных ролей."
        )
    elif isinstance(error, app_commands.CommandOnCooldown):
        text = f"Слишком часто. Попробуйте через {error.retry_after:.0f} с."
    elif isinstance(error, app_commands.MissingPermissions):
        text = "Не хватает прав: " + ", ".join(f"`{p}`" for p in error.missing_permissions)
    elif isinstance(error, app_commands.NoPrivateMessage):
        text = "Команда работает только на сервере."
    elif isinstance(error, app_commands.CheckFailure):
        text = "У вас нет доступа к этой команде."
    else:
        text = f"Ошибка: `{type(error).__name__}: {error}`\nПодробности в `bot.log`."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(f"❌ {text}", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ {text}", ephemeral=True)
    except Exception:
        log.warning("Не удалось отправить сообщение об ошибке", exc_info=True)


@bot.listen("on_interaction")
async def _log_interaction(interaction: discord.Interaction) -> None:
    """Пишет в лог каждое нажатие/команду — сразу видно, доходит ли до бота."""
    data = interaction.data or {}
    log.info(
        "interaction type=%s name=%s guild=%s user=%s",
        getattr(interaction.type, "name", interaction.type),
        data.get("name") or data.get("custom_id") or "-",
        interaction.guild_id,
        getattr(interaction.user, "id", "-"),
    )


# ---------------------------------------------------------------- утилиты
async def deny(interaction: discord.Interaction, text: str) -> None:
    msg = f"❌ {text}"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


async def ok(interaction: discord.Interaction, text: str) -> None:
    msg = f"✅ {text}"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


def is_manager(interaction: discord.Interaction) -> bool:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False
    member = interaction.user
    if member.id == interaction.guild.owner_id or member.id in config.OWNER_IDS:
        return True
    p = member.guild_permissions
    return bool(p.administrator or p.manage_roles)


def hierarchy_error(interaction: discord.Interaction, target: discord.Member) -> str | None:
    """Нельзя трогать себя, ботов и тех, кто выше или равен по роли."""
    guild = interaction.guild
    actor = interaction.user
    if guild is None:
        return "Команда работает только на сервере."
    if target.id == actor.id:
        return "Нельзя применять действие к самому себе."
    if target.bot:
        return "Нельзя применять действие к боту."
    if actor.id != guild.owner_id and actor.top_role.position <= target.top_role.position:
        return "Нельзя взаимодействовать с участником, чья роль выше или равна вашей."
    if guild.me is None or guild.me.top_role.position <= target.top_role.position:
        return "Моя роль не выше роли участника — я не смогу изменить его роли."
    return None


def role_by_name(guild: discord.Guild, name: str) -> discord.Role | None:
    """Ищет роль по названию среди существующих ролей сервера.

    Сначала точное совпадение, потом вхождение (роли часто оформляют
    значками: «⌊🔰⌉│Хелпер»). Из нескольких совпадений берём самую низкую,
    чтобы «Старший Хелпер» не подменил «Хелпера».
    """
    target = (name or "").strip().lower()
    if not target:
        return None
    exact = [r for r in guild.roles if r.name.strip().lower() == target]
    if exact:
        return exact[0]
    loose = [r for r in guild.roles if target in r.name.lower()]
    return min(loose, key=lambda r: r.position) if loose else None


def role_ceiling_error(interaction: discord.Interaction, role: discord.Role) -> str | None:
    """Роль должна быть управляемой: ниже роли бота и ниже роли автора команды."""
    guild = interaction.guild
    if guild is None or guild.me is None:
        return "Команда работает только на сервере."
    if role.is_default():
        return "Роль @everyone использовать нельзя."
    if role.managed:
        return f"Роль «{role.name}» управляется интеграцией — выдать её нельзя."
    if role.position >= guild.me.top_role.position:
        return (
            f"Роль «{role.name}» не ниже моей роли. "
            "Поднимите роль бота выше этой роли."
        )
    actor = interaction.user
    if (
        isinstance(actor, discord.Member)
        and actor.id != guild.owner_id
        and actor.id not in config.OWNER_IDS
        and actor.top_role.position <= role.position
    ):
        return f"Роль «{role.name}» не ниже вашей — вы не можете её выдать."
    return None


STEAMID_RE = re.compile(r"^(?:STEAM_[0-5]:[01]:\d{1,10}|7656119\d{10})$", re.IGNORECASE)


def normalize_steamid(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value or not STEAMID_RE.match(value):
        return None
    return value.upper() if value.upper().startswith("STEAM_") else value


def steamid_problem(value: str | None, *, required: bool = True) -> str | None:
    """Возвращает текст ошибки или None, если SteamID в порядке."""
    if not (value or "").strip():
        return "Укажите SteamID. Пример: `STEAM_0:1:1234567`." if required else None
    if normalize_steamid(value) is None:
        return (
            "Неверный SteamID. Пример: `STEAM_0:1:1234567` "
            "или `76561198000000000`."
        )
    return None


def steamid_mismatch(row: dict, steam: str) -> str | None:
    """Проверяет, что введённый SteamID совпадает с карточкой."""
    stored = (row.get("steamid") or "").strip()
    if stored and stored.lower() != steam.lower():
        return (
            f"SteamID не совпадает с карточкой: у администратора указан `{stored}`. "
            "Проверьте, кого трогаете."
        )
    return None


VERSION_RE = re.compile(r"^v?(\d{1,3})(?:[.,](\d{1,3}))?$", re.IGNORECASE)


def parse_version(value: str | None) -> tuple[int, int] | None:
    """Разбирает версию вида `2.6`, `2,6`, `2` или `v2.6`.

    Первое число — крупная версия, второе — номер патча.
    """
    match = VERSION_RE.match((value or "").strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2) or 0)


def next_release(
    current: tuple[int, int, int],
    explicit: str | None,
    *,
    versioned: bool,
) -> tuple[tuple[int, int, int], str | None]:
    """Считает версию и номер билда для новой публикации.

    `versioned=False` — обычная новость без версии: ничего не меняется.
    С указанной версией берём её как есть, без версии — первый выпуск 1.0,
    дальше патч увеличивается на единицу (1.0 -> 1.1 -> 1.2).
    """
    major, patch, build = current
    if not versioned:
        return (major, patch, build), None

    if explicit:
        parsed = parse_version(explicit)
        if parsed is None:
            raise ValueError(
                "Версия должна быть в виде `2.6` (крупная версия и номер патча)."
            )
        major, patch = parsed
    elif build == 0:
        major, patch = 1, 0
    else:
        patch += 1

    build += 1
    return (major, patch, build), f"{major}.{patch}"


def format_bullets(value: str | None) -> str:
    """Превращает строки в аккуратный список; уже готовые маркеры сохраняет."""
    lines: list[str] = []
    for raw in (value or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line[0] in "-•*—":
            line = line.lstrip("-•*—").strip()
        if line:
            lines.append(f"• {line}")
    return "\n".join(lines)


def channel_for(guild: discord.Guild, kind: str) -> discord.abc.Messageable | None:
    cid = bot.db.get_channel(guild.id, kind)
    return guild.get_channel(cid) if cid else None


async def safe_send(channel, **kwargs) -> bool:
    if channel is None:
        return False
    try:
        # Форум-каналы не принимают обычные сообщения — там нужен пост (thread).
        if isinstance(channel, discord.ForumChannel):
            embed = kwargs.get("embed")
            name = ""
            if embed is not None and getattr(embed, "title", None):
                name = str(embed.title)
            elif kwargs.get("content"):
                name = str(kwargs["content"]).splitlines()[0]
            name = (name.replace("*", "").replace("`", "").strip() or "Администрация")[:90]
            await channel.create_thread(
                name=name,
                content=kwargs.get("content") or None,
                embed=embed,
            )
            return True
        await channel.send(**kwargs)
        return True
    except Exception:
        log.warning("Не удалось отправить сообщение в канал", exc_info=True)
        return False


async def safe_dm(user, content: str) -> None:
    try:
        await user.send(content)
    except Exception:
        pass


async def post_news(
    guild: discord.Guild,
    title: str,
    lines: list[str],
    author=None,
    *,
    footer: str | None = None,
) -> bool:
    """Публикует новость в привязанном канале новостей."""
    channel = channel_for(guild, "news")
    if channel is None:
        return False
    return await safe_send(
        channel,
        embed=fmt.news_embed(title, lines, author, footer=footer),
    )


async def post_release(
    guild: discord.Guild,
    *,
    title: str,
    version: str | None,
    build: int | None,
    intro: str | None,
    sections: dict[str, str],
    author=None,
) -> bool:
    """Публикует обновление по шаблону (версия, билд, разделы)."""
    channel = channel_for(guild, "news")
    if channel is None:
        return False
    return await safe_send(
        channel,
        embed=fmt.release_embed(
            version=version,
            build=build,
            title=title,
            intro=intro,
            sections=sections,
            author=author,
        ),
    )


async def maybe_post_update(guild: discord.Guild) -> bool:
    """Публикует обновление бота один раз на версию, если канал новостей привязан."""
    latest = updates.latest()
    if bot.db.get_setting(guild.id, "news_version") == latest["version"]:
        return False
    if bot.db.get_channel(guild.id, "news") is None:
        return False
    posted = await post_news(
        guild,
        latest["title"],
        latest["lines"],
        footer=f"обновление бота • версия {latest['version']} • {updates.UPDATED_AT}",
    )
    if posted:
        bot.db.set_setting(guild.id, "news_version", latest["version"])
    return posted


def admin_role_text(guild: discord.Guild, row: dict | None) -> str | None:
    """Текущее название должности: живая роль с сервера или сохранённое имя."""
    if not row:
        return None
    rid = row.get("role_id")
    if rid:
        role = guild.get_role(int(rid))
        if role is not None:
            return role.mention
    return row.get("role_name")


async def change_roles(
    member: discord.Member,
    role: discord.Role,
    old_role_id: int | None = None,
) -> str | None:
    """Снимает прежнюю должностную роль и выдаёт новую. Возвращает текст ошибки."""
    guild = member.guild
    top = guild.me.top_role.position if guild.me else 0
    to_remove: list[discord.Role] = []
    if old_role_id:
        old = guild.get_role(int(old_role_id))
        if (
            old is not None
            and old.id != role.id
            and old in member.roles
            and not old.managed
            and old.position < top
        ):
            to_remove.append(old)
    try:
        if to_remove:
            await member.remove_roles(*to_remove, reason="Администрация: смена должности")
        if role not in member.roles:
            await member.add_roles(role, reason="Администрация: должность")
    except discord.Forbidden:
        return "Нет прав на изменение ролей этого участника."
    except discord.HTTPException:
        log.warning("Не удалось изменить роли участника", exc_info=True)
        return "Discord не дал изменить роли. Проверьте права бота."
    return None


async def give_role(member: discord.Member, role: discord.Role) -> str | None:
    """Выдаёт роль, ничего не снимая."""
    try:
        if role not in member.roles:
            await member.add_roles(role, reason="Администрация: выдача роли")
    except discord.Forbidden:
        return "Нет прав на изменение ролей этого участника."
    except discord.HTTPException:
        log.warning("Не удалось выдать роль", exc_info=True)
        return "Discord не дал выдать роль. Проверьте права бота."
    return None


async def strip_staff_roles(
    member: discord.Member,
) -> tuple[list[str], list[str], str | None]:
    """Снимает всё от «Хелпера» и выше и выдаёт «Игрок».

    Возвращает (снятые роли, роли которые бот снять не может, текст ошибки).
    Если роли «Хелпер» на сервере нет — снимаются только роли, выданные ботом,
    и сохранённая в карточке должность.
    """
    guild = member.guild
    gid = guild.id
    top = guild.me.top_role.position if guild.me else 0
    helper = role_by_name(guild, config.HELPER_ROLE_NAME)
    threshold = helper.position if helper else None

    row = bot.db.admin(gid, member.id) or {}
    stored = row.get("role_id")
    granted = set(bot.db.granted_role_ids(gid, member.id))
    player = role_by_name(guild, config.PLAYER_ROLE_NAME)
    player_id = player.id if player else None

    to_remove: list[discord.Role] = []
    blocked: list[str] = []
    for role in member.roles:
        if role.is_default() or role.managed:
            continue
        if role.id == player_id:
            continue
        is_staff = False
        if threshold is not None and role.position >= threshold:
            is_staff = True
        if role.id in granted or (stored and int(stored) == role.id):
            is_staff = True
        if not is_staff:
            continue
        if role.position >= top:
            blocked.append(role.name)
            continue
        to_remove.append(role)

    removed: list[str] = []
    try:
        if to_remove:
            await member.remove_roles(*to_remove, reason="Администрация: снятие с должности")
            removed = sorted({r.name for r in to_remove})
        if player is not None and player not in member.roles:
            if player.position < top and not player.managed:
                await member.add_roles(player, reason="Администрация: роль игрока")
            else:
                blocked.append(player.name)
    except discord.Forbidden:
        return removed, blocked, "Нет прав на изменение ролей этого участника."
    except discord.HTTPException:
        log.warning("Не удалось снять роли участника", exc_info=True)
        return removed, blocked, "Discord не дал изменить роли. Проверьте права бота."

    bot.db.clear_granted_roles(gid, member.id)
    return removed, blocked, None


async def log_action(
    guild: discord.Guild,
    action: str,
    target,
    actor,
    *,
    nickname: str | None,
    reason: str | None,
) -> None:
    await safe_send(
        channel_for(guild, "logs"),
        content=fmt.short_log_line(action, target, actor, nickname=nickname, reason=reason),
    )


async def post_event(
    guild: discord.Guild,
    action: str,
    target,
    actor,
    *,
    nickname: str | None,
    steamid: str | None,
    reason: str | None,
    role_text: str | None = None,
    extra: list[tuple[str, str]] | None = None,
) -> None:
    """Пишет карточку в канал аудита и строку в общие логи."""
    style = bot.db.get_setting(guild.id, "style", config.DEFAULT_STYLE)
    content, embed = fmt.event_message(
        style,
        action,
        target,
        actor,
        nickname=nickname,
        steamid=steamid,
        role_text=role_text,
        reason=reason,
        when=fmt_dt(now_iso()),
        extra=extra,
    )
    await safe_send(channel_for(guild, "audit"), content=content or None, embed=embed)
    await log_action(guild, action, target, actor, nickname=nickname, reason=reason)


async def post_punish(
    guild: discord.Guild,
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
) -> None:
    """Пишет карточку варна/предупреждения и строку в логи."""
    style = bot.db.get_setting(guild.id, "style", config.DEFAULT_STYLE)
    content, embed = fmt.punish_card(
        style,
        kind,
        target,
        actor,
        nickname=nickname,
        steamid=steamid,
        reason=reason,
        count=count,
        limit=limit,
        removed=removed,
        when=fmt_dt(now_iso()),
    )
    await safe_send(channel_for(guild, "warns"), content=content or None, embed=embed)
    action = f"{kind}_{'remove' if removed else 'add'}"
    await log_action(guild, action, target, actor, nickname=nickname, reason=reason)


async def require_admin(interaction: discord.Interaction, member: discord.Member) -> dict | None:
    """Возвращает карточку действующего администратора или отвечает ошибкой."""
    row = bot.db.admin(interaction.guild.id, member.id)
    if row is None:
        await deny(
            interaction,
            "Этого участника нет в составе администрации. Сначала `/принять`.",
        )
        return None
    if row.get("status") != "active":
        await deny(interaction, "Администратор снят. Сначала примите его заново.")
        return None
    return row


# =========================================================== /принять
@bot.tree.command(name="принять", description="Принять участника в администрацию")
@app_commands.describe(
    участник="Кого принимаем",
    steamid="SteamID игрока (обязательно)",
    роль="Роль-должность, которая будет выдана",
    причина="Причина принятия",
)
@app_commands.guild_only()
async def accept_cmd(
    interaction: discord.Interaction,
    участник: discord.Member,
    steamid: str,
    роль: discord.Role,
    причина: str | None = None,
):
    if not is_manager(interaction):
        return await deny(interaction, "Нужны права «Управление ролями».")
    err = hierarchy_error(interaction, участник)
    if err:
        return await deny(interaction, err)
    problem = steamid_problem(steamid)
    if problem:
        return await deny(interaction, problem)
    steam = normalize_steamid(steamid)
    ceiling = role_ceiling_error(interaction, роль)
    if ceiling:
        return await deny(interaction, ceiling)

    gid = interaction.guild.id
    bl = bot.db.blacklist_find(gid, user_id=участник.id, steamid=steam)
    if bl:
        return await deny(
            interaction,
            "Этот игрок в чёрном списке проекта. Сначала снимите его: `/убрать-чсп`.",
        )

    existing = bot.db.admin(gid, участник.id)
    if existing and existing["status"] == "active":
        return await deny(interaction, "Этот участник уже в составе администрации.")

    by_steam = bot.db.admin_by_steamid(gid, steam)
    if by_steam and by_steam["user_id"] != участник.id and by_steam["status"] == "active":
        return await deny(
            interaction,
            f"SteamID `{steam}` уже закреплён за <@{by_steam['user_id']}>.",
        )

    await interaction.response.defer(ephemeral=True)

    old_role_id = existing.get("role_id") if existing else None
    role_err = await change_roles(участник, роль, old_role_id)
    if role_err:
        return await deny(interaction, role_err)

    bot.db.upsert_admin(
        gid,
        участник.id,
        nickname=участник.display_name,
        steamid=steam,
        role_id=роль.id,
        role_name=роль.name,
        status="active",
    )
    bot.db.add_granted_role(gid, участник.id, роль.id)
    # Повторный приём начинается с чистого листа по дисциплине.
    bot.db.punish_deactivate_all(gid, участник.id, interaction.user.id, "повторный приём")

    bot.db.add_action(
        gid,
        участник.id,
        interaction.user.id,
        "accept",
        f"Должность: «{роль.name}»",
        причина,
        steam,
    )
    row = bot.db.admin(gid, участник.id) or {}
    await post_event(
        interaction.guild,
        "accept",
        участник,
        interaction.user,
        nickname=row.get("nickname"),
        steamid=row.get("steamid"),
        reason=причина,
        role_text=роль.mention,
    )

    await safe_dm(
        участник,
        f"**{config.BOT_NAME}** · вы приняты в состав администрации.\n"
        f"Должность: **{роль.name}**"
        + (f"\nПричина: {причина}" if причина else ""),
    )
    await ok(
        interaction,
        f"Администратор принят. Должность: **{роль.name}**, SteamID: `{steam}`.",
    )


# =========================================================== /повышение
@bot.tree.command(name="повышение", description="Повысить администратора")
@app_commands.describe(
    участник="Кого повышаем",
    steamid="SteamID игрока (обязательно, должен совпадать с карточкой)",
    роль="Новая роль-должность",
    причина="Причина повышения",
)
@app_commands.guild_only()
async def promote_cmd(
    interaction: discord.Interaction,
    участник: discord.Member,
    steamid: str,
    роль: discord.Role,
    причина: str | None = None,
):
    if not is_manager(interaction):
        return await deny(interaction, "Нужны права «Управление ролями».")
    err = hierarchy_error(interaction, участник)
    if err:
        return await deny(interaction, err)
    problem = steamid_problem(steamid)
    if problem:
        return await deny(interaction, problem)
    steam = normalize_steamid(steamid)
    ceiling = role_ceiling_error(interaction, роль)
    if ceiling:
        return await deny(interaction, ceiling)

    row = await require_admin(interaction, участник)
    if row is None:
        return
    mismatch = steamid_mismatch(row, steam)
    if mismatch:
        return await deny(interaction, mismatch)

    gid = interaction.guild.id
    current_role = interaction.guild.get_role(int(row["role_id"])) if row.get("role_id") else None
    if current_role is not None and current_role.position >= роль.position:
        return await deny(
            interaction,
            f"Новая роль должна быть выше текущей «{current_role.name}».",
        )
    if current_role is not None and current_role.id == роль.id:
        return await deny(interaction, "У администратора уже эта роль.")

    await interaction.response.defer(ephemeral=True)
    role_err = await change_roles(участник, роль, row.get("role_id"))
    if role_err:
        return await deny(interaction, role_err)

    if not (row.get("steamid") or "").strip():
        bot.db.set_admin_steamid(gid, участник.id, steam)
    bot.db.set_admin_role(gid, участник.id, роль.id, роль.name)
    bot.db.add_granted_role(gid, участник.id, роль.id)

    was = current_role.name if current_role else (row.get("role_name") or "—")
    bot.db.add_action(
        gid,
        участник.id,
        interaction.user.id,
        "promote",
        f"с «{was}» на «{роль.name}»",
        причина,
        steam,
    )
    await post_event(
        interaction.guild,
        "promote",
        участник,
        interaction.user,
        nickname=row.get("nickname"),
        steamid=row.get("steamid") or steam,
        reason=причина,
        role_text=роль.mention,
        extra=[("Было", was)],
    )
    await safe_dm(
        участник,
        f"**{config.BOT_NAME}** · вы повышены до должности **{роль.name}**."
        + (f"\nПричина: {причина}" if причина else ""),
    )
    await ok(interaction, f"Повышен(а) до **{роль.name}**.")


# =========================================================== /понижение
@bot.tree.command(name="понижение", description="Понизить администратора")
@app_commands.describe(
    участник="Кого понижаем",
    роль="Новая роль-должность",
    steamid="SteamID (необязательно, но должен совпадать с карточкой)",
    причина="Причина понижения",
)
@app_commands.guild_only()
async def demote_cmd(
    interaction: discord.Interaction,
    участник: discord.Member,
    роль: discord.Role,
    steamid: str | None = None,
    причина: str | None = None,
):
    if not is_manager(interaction):
        return await deny(interaction, "Нужны права «Управление ролями».")
    err = hierarchy_error(interaction, участник)
    if err:
        return await deny(interaction, err)
    problem = steamid_problem(steamid, required=False)
    if problem:
        return await deny(interaction, problem)
    ceiling = role_ceiling_error(interaction, роль)
    if ceiling:
        return await deny(interaction, ceiling)

    row = await require_admin(interaction, участник)
    if row is None:
        return
    if steamid:
        steam = normalize_steamid(steamid)
        mismatch = steamid_mismatch(row, steam)
        if mismatch:
            return await deny(interaction, mismatch)

    gid = interaction.guild.id
    current_role = interaction.guild.get_role(int(row["role_id"])) if row.get("role_id") else None
    if current_role is not None and current_role.position <= роль.position:
        return await deny(
            interaction,
            f"Новая роль должна быть ниже текущей «{current_role.name}».",
        )
    if current_role is not None and current_role.id == роль.id:
        return await deny(interaction, "У администратора уже эта роль.")

    await interaction.response.defer(ephemeral=True)
    role_err = await change_roles(участник, роль, row.get("role_id"))
    if role_err:
        return await deny(interaction, role_err)

    bot.db.set_admin_role(gid, участник.id, роль.id, роль.name)
    bot.db.add_granted_role(gid, участник.id, роль.id)

    was = current_role.name if current_role else (row.get("role_name") or "—")
    bot.db.add_action(
        gid,
        участник.id,
        interaction.user.id,
        "demote",
        f"с «{was}» на «{роль.name}»",
        причина,
        row.get("steamid"),
    )
    await post_event(
        interaction.guild,
        "demote",
        участник,
        interaction.user,
        nickname=row.get("nickname"),
        steamid=row.get("steamid"),
        reason=причина,
        role_text=роль.mention,
        extra=[("Было", was)],
    )
    await safe_dm(
        участник,
        f"**{config.BOT_NAME}** · вы понижены до должности **{роль.name}**."
        + (f"\nПричина: {причина}" if причина else ""),
    )
    await ok(interaction, f"Понижен(а) до **{роль.name}**.")


# =============================================================== /снять
@bot.tree.command(name="снять", description="Снять с должности: остаётся только роль «Игрок»")
@app_commands.describe(
    участник="Кого снимаем",
    steamid="SteamID игрока (обязательно, должен совпадать с карточкой)",
    причина="Причина снятия",
)
@app_commands.guild_only()
async def remove_cmd(
    interaction: discord.Interaction,
    участник: discord.Member,
    steamid: str,
    причина: str | None = None,
):
    if not is_manager(interaction):
        return await deny(interaction, "Нужны права «Управление ролями».")
    err = hierarchy_error(interaction, участник)
    if err:
        return await deny(interaction, err)
    problem = steamid_problem(steamid)
    if problem:
        return await deny(interaction, problem)
    steam = normalize_steamid(steamid)

    row = await require_admin(interaction, участник)
    if row is None:
        return
    mismatch = steamid_mismatch(row, steam)
    if mismatch:
        return await deny(interaction, mismatch)

    gid = interaction.guild.id
    current = admin_role_text(interaction.guild, row) or "без должности"

    confirmed = await ConfirmView.ask(
        interaction,
        f"Снять **{участник.display_name}** с должности {current}?\n\n"
        f"Будут сняты все роли от «{config.HELPER_ROLE_NAME}» и выше, "
        f"останется роль «{config.PLAYER_ROLE_NAME}». Это не исключение из Discord.",
    )
    if not confirmed:
        return

    removed, blocked, role_err = await strip_staff_roles(участник)

    bot.db.set_admin_status(gid, участник.id, "removed")
    cleared = bot.db.punish_deactivate_all(
        gid, участник.id, interaction.user.id, "снятие администратора"
    )
    bot.db.add_action(
        gid,
        участник.id,
        interaction.user.id,
        "remove",
        f"снят(а) с должности «{current}»",
        причина,
        row.get("steamid"),
    )

    extra: list[tuple[str, str]] = [("Активные варны и предупреждения", f"снято: {cleared}")]
    if removed:
        extra.append(("Сняты роли", ", ".join(removed)[:1024]))
    await post_event(
        interaction.guild,
        "remove",
        участник,
        interaction.user,
        nickname=row.get("nickname"),
        steamid=row.get("steamid"),
        reason=причина,
        role_text=current,
        extra=extra,
    )
    await safe_dm(
        участник,
        f"**{config.BOT_NAME}** · вы сняты с должности администратора."
        + (f"\nПричина: {причина}" if причина else ""),
    )

    msg = f"**{участник.display_name}** снят(а) с должности {current}."
    if role_err:
        msg += f"\n⚠️ {role_err}"
    if blocked:
        msg += (
            "\n⚠️ Не смог снять роли выше моей: "
            + ", ".join(blocked[:10])
            + ". Поднимите роль бота выше."
        )
    await ok(interaction, msg)


# ========================================================= /выдать-роль
@bot.tree.command(name="выдать-роль", description="Выдать роль участнику")
@app_commands.describe(
    участник="Кому выдаём роль",
    роль="Какую роль выдать",
    причина="Причина выдачи",
)
@app_commands.guild_only()
async def give_role_cmd(
    interaction: discord.Interaction,
    участник: discord.Member,
    роль: discord.Role,
    причина: str | None = None,
):
    if not is_manager(interaction):
        return await deny(interaction, "Нужны права «Управление ролями».")
    err = hierarchy_error(interaction, участник)
    if err:
        return await deny(interaction, err)
    ceiling = role_ceiling_error(interaction, роль)
    if ceiling:
        return await deny(interaction, ceiling)
    if роль in участник.roles:
        return await deny(interaction, f"У участника уже есть роль «{роль.name}».")

    await interaction.response.defer(ephemeral=True)
    role_err = await give_role(участник, роль)
    if role_err:
        return await deny(interaction, role_err)

    gid = interaction.guild.id
    bot.db.add_granted_role(gid, участник.id, роль.id)
    row = bot.db.admin(gid, участник.id) or {}
    nickname = row.get("nickname") or участник.display_name
    steam = row.get("steamid")
    bot.db.add_action(
        gid,
        участник.id,
        interaction.user.id,
        "role_add",
        f"выдана роль «{роль.name}»",
        причина,
        steam,
    )
    await post_event(
        interaction.guild,
        "role_add",
        участник,
        interaction.user,
        nickname=nickname,
        steamid=steam,
        reason=причина,
        role_text=роль.mention,
    )
    await ok(interaction, f"Роль **{роль.name}** выдана.")


# ====================================================== /выдать-варн и др.
async def issue_punishment(
    interaction: discord.Interaction,
    участник: discord.Member,
    причина: str,
    kind: str,
) -> None:
    if not is_manager(interaction):
        return await deny(interaction, "Нужны права «Управление ролями».")
    err = hierarchy_error(interaction, участник)
    if err:
        return await deny(interaction, err)

    row = await require_admin(interaction, участник)
    if row is None:
        return

    gid = interaction.guild.id
    limit = bot.db.warn_limit(gid)
    count = bot.db.punish_count(gid, участник.id, kind) + 1

    await interaction.response.defer(ephemeral=True)
    bot.db.punish_add(gid, участник.id, kind, причина, interaction.user.id)
    label = "Варн" if kind == "warn" else "Предупреждение"
    amount = f"{count}/{limit}" if kind == "warn" else str(count)
    bot.db.add_action(
        gid,
        участник.id,
        interaction.user.id,
        f"{kind}_add",
        f"{label}: {amount}",
        причина,
        row.get("steamid"),
    )
    await post_punish(
        interaction.guild,
        kind,
        участник,
        interaction.user,
        nickname=row.get("nickname"),
        steamid=row.get("steamid"),
        reason=причина,
        count=count,
        limit=limit,
    )
    await safe_dm(
        участник,
        f"**{config.BOT_NAME}** · вам "
        + ("выдан варн" if kind == "warn" else "выдано предупреждение")
        + f" ({amount}).\nПричина: {причина}",
    )

    text = f"{label}: **{amount}**."
    if kind == "warn" and count >= limit:
        text += "\n⚠️ Достигнут предел варнов — пора снять администратора или варн."
    await ok(interaction, text)


async def remove_punishment(
    interaction: discord.Interaction,
    участник: discord.Member,
    причина: str | None,
    kind: str,
) -> None:
    if not is_manager(interaction):
        return await deny(interaction, "Нужны права «Управление ролями».")
    err = hierarchy_error(interaction, участник)
    if err:
        return await deny(interaction, err)
    row = await require_admin(interaction, участник)
    if row is None:
        return

    gid = interaction.guild.id
    label = "Варн" if kind == "warn" else "Предупреждение"
    removed = bot.db.punish_deactivate_last(
        gid, участник.id, kind, interaction.user.id, причина
    )
    if removed is None:
        return await deny(interaction, f"Активных наказаний «{label.lower()}» нет.")

    limit = bot.db.warn_limit(gid)
    count = bot.db.punish_count(gid, участник.id, kind)
    amount = f"{count}/{limit}" if kind == "warn" else str(count)
    bot.db.add_action(
        gid,
        участник.id,
        interaction.user.id,
        f"{kind}_remove",
        f"{label} снят, осталось: {amount}",
        причина,
        row.get("steamid"),
    )
    await post_punish(
        interaction.guild,
        kind,
        участник,
        interaction.user,
        nickname=row.get("nickname"),
        steamid=row.get("steamid"),
        reason=причина,
        count=count,
        limit=limit,
        removed=True,
    )
    await ok(interaction, f"{label} снят. Сейчас: **{amount}**.")


@bot.tree.command(name="выдать-варн", description="Выдать варн администратору")
@app_commands.describe(участник="Кому выдаём", причина="За что выдан варн")
@app_commands.guild_only()
async def warn_add_cmd(interaction: discord.Interaction, участник: discord.Member, причина: str):
    await issue_punishment(interaction, участник, причина, "warn")


@bot.tree.command(name="убрать-варн", description="Снять последний активный варн")
@app_commands.describe(участник="У кого снимаем", причина="Причина снятия")
@app_commands.guild_only()
async def warn_remove_cmd(
    interaction: discord.Interaction,
    участник: discord.Member,
    причина: str | None = None,
):
    await remove_punishment(interaction, участник, причина, "warn")


@bot.tree.command(name="выдать-предупреждение", description="Выдать предупреждение администратору")
@app_commands.describe(участник="Кому выдаём", причина="За что предупреждение")
@app_commands.guild_only()
async def notice_add_cmd(interaction: discord.Interaction, участник: discord.Member, причина: str):
    await issue_punishment(interaction, участник, причина, "notice")


@bot.tree.command(name="убрать-предупреждение", description="Снять последнее предупреждение")
@app_commands.describe(участник="У кого снимаем", причина="Причина снятия")
@app_commands.guild_only()
async def notice_remove_cmd(
    interaction: discord.Interaction,
    участник: discord.Member,
    причина: str | None = None,
):
    await remove_punishment(interaction, участник, причина, "notice")


# ============================================================ /выдать-чсп
@bot.tree.command(name="выдать-чсп", description="Занести игрока в чёрный список проекта (ЧСП)")
@app_commands.describe(
    участник="Игрок",
    steamid="SteamID (если не указан — берётся из карточки)",
    причина="Причина",
)
@app_commands.guild_only()
async def blacklist_add_cmd(
    interaction: discord.Interaction,
    участник: discord.User,
    steamid: str | None = None,
    причина: str | None = None,
):
    if not is_manager(interaction):
        return await deny(interaction, "Нужны права «Управление ролями».")
    problem = steamid_problem(steamid, required=False)
    if problem:
        return await deny(interaction, problem)

    gid = interaction.guild.id
    if isinstance(участник, discord.Member):
        err = hierarchy_error(interaction, участник)
        if err:
            return await deny(interaction, err)

    row = bot.db.admin(gid, участник.id) or {}
    if row.get("status") == "active":
        return await deny(
            interaction,
            "Сначала снимите администратора: `/снять`, потом заносите в ЧСП.",
        )
    if bot.db.blacklist_by_user(gid, участник.id):
        return await deny(interaction, "Этот участник уже в чёрном списке проекта.")

    nick = row.get("nickname")
    steam = normalize_steamid(steamid) or row.get("steamid") or ""

    await interaction.response.defer(ephemeral=True)
    bot.db.blacklist_add(
        gid,
        user_id=участник.id,
        nickname=nick,
        steamid=steam,
        reason=причина,
        added_by=interaction.user.id,
    )
    bot.db.add_action(
        gid,
        участник.id,
        interaction.user.id,
        "bl_add",
        "занесён(а) в чёрный список проекта",
        причина,
        steam,
    )

    style = bot.db.get_setting(gid, "style", config.DEFAULT_STYLE)
    content, embed = fmt.blacklist_card(
        style,
        участник,
        interaction.user,
        nickname=nick,
        steamid=steam,
        reason=причина,
        when=fmt_dt(now_iso()),
    )
    await safe_send(channel_for(interaction.guild, "blacklist"), content=content or None, embed=embed)
    await log_action(
        interaction.guild,
        "bl_add",
        участник,
        interaction.user,
        nickname=nick or getattr(участник, "display_name", None),
        reason=причина,
    )
    await ok(interaction, "Игрок занесён в чёрный список проекта.")


@bot.tree.command(name="убрать-чсп", description="Убрать игрока из чёрного списка проекта")
@app_commands.describe(участник="Кого убираем")
@app_commands.guild_only()
async def blacklist_remove_cmd(interaction: discord.Interaction, участник: discord.User):
    if not is_manager(interaction):
        return await deny(interaction, "Нужны права «Управление ролями».")
    gid = interaction.guild.id
    if not bot.db.blacklist_by_user(gid, участник.id):
        return await deny(interaction, "Этого участника нет в чёрном списке проекта.")

    await interaction.response.defer(ephemeral=True)
    bot.db.blacklist_remove(gid, участник.id)
    row = bot.db.admin(gid, участник.id) or {}
    bot.db.add_action(
        gid,
        участник.id,
        interaction.user.id,
        "bl_remove",
        "убран(а) из чёрного списка проекта",
        None,
        row.get("steamid"),
    )
    await log_action(
        interaction.guild,
        "bl_remove",
        участник,
        interaction.user,
        nickname=row.get("nickname") or getattr(участник, "display_name", None),
        reason=None,
    )
    await ok(interaction, "Участник убран из чёрного списка проекта.")


# =============================================================== /поиск
@bot.tree.command(name="поиск", description="Найти администратора по нику, SteamID или Discord ID")
@app_commands.describe(запрос="Ник, SteamID или Discord ID")
@app_commands.guild_only()
async def search_cmd(interaction: discord.Interaction, запрос: str):
    if not is_manager(interaction):
        return await deny(interaction, "Нужны права «Управление ролями».")
    gid = interaction.guild.id
    rows = bot.db.search_admins(gid, запрос)
    if not rows:
        return await deny(interaction, "Ничего не найдено.")

    embed = discord.Embed(title="🔎 Результаты поиска", color=0xF8141B)
    for r in rows[:10]:
        status = "🟢 в составе" if r.get("status") == "active" else "🔴 снят(а)"
        embed.add_field(
            name=f"<@{r['user_id']}> · {r.get('nickname') or 'без ника'}",
            value=(
                f"SteamID: `{r.get('steamid') or '—'}`\n"
                f"Должность: {r.get('role_name') or '—'} · {status}"
            ),
            inline=False,
        )
    embed.set_footer(text=f"{config.BOT_NAME} • найдено: {len(rows)}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ================================================================ /инфо
@bot.tree.command(name="инфо", description="Карточка администратора")
@app_commands.describe(участник="Чью карточку показать")
@app_commands.guild_only()
async def info_cmd(interaction: discord.Interaction, участник: discord.Member):
    if not is_manager(interaction):
        return await deny(interaction, "Нужны права «Управление ролями».")
    gid = interaction.guild.id
    row = bot.db.admin(gid, участник.id)
    bl = bot.db.blacklist_by_user(gid, участник.id)
    embed = fmt.info_card(
        row,
        участник,
        admin_role_text(interaction.guild, row),
        bot.db.action_count(gid, участник.id),
        warns=bot.db.punish_count(gid, участник.id, "warn"),
        notices=bot.db.punish_count(gid, участник.id, "notice"),
        warn_limit=bot.db.warn_limit(gid),
        blacklisted=bl is not None,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ============================================================== /новость
@bot.tree.command(name="новость", description="Опубликовать обновление или новость по шаблону")
@app_commands.describe(
    заголовок="Заголовок (по умолчанию: «Обновление 1.0»)",
    версия="Версия вручную, например 2.6 (2 — крупная версия, 6 — патчи)",
    добавили="Что добавили — по пункту на строку",
    изменили="Что изменили — по пункту на строку",
    исправили="Что исправили — по пункту на строку",
    убрали="Что убрали — по пункту на строку",
    текст="Обычный текст новости (если это не обновление)",
)
@app_commands.guild_only()
async def news_cmd(
    interaction: discord.Interaction,
    заголовок: str | None = None,
    версия: str | None = None,
    добавили: str | None = None,
    изменили: str | None = None,
    исправили: str | None = None,
    убрали: str | None = None,
    текст: str | None = None,
):
    if not is_manager(interaction):
        return await deny(interaction, "Нужны права «Управление ролями».")
    gid = interaction.guild.id
    if bot.db.get_channel(gid, "news") is None:
        return await deny(
            interaction,
            "Канал новостей не привязан. Сначала: `/канал тип: Новости и обновления`.",
        )

    sections = {
        "added": format_bullets(добавили),
        "changed": format_bullets(изменили),
        "fixed": format_bullets(исправили),
        "removed": format_bullets(убрали),
    }
    intro = (текст or "").strip()
    versioned = bool((версия or "").strip()) or any(sections.values())
    if not any(sections.values()) and not intro:
        return await deny(
            interaction,
            "Заполните хотя бы один раздел: `добавили`, `изменили`, `исправили`, "
            "`убрали` или `текст`.",
        )

    state = bot.db.release_state(gid)
    try:
        (major, patch, build), shown = next_release(
            state, версия, versioned=versioned
        )
    except ValueError as exc:
        return await deny(interaction, str(exc))

    title = (заголовок or "").strip()
    if not title:
        title = f"Обновление {shown}" if versioned else "Новость"

    await interaction.response.defer(ephemeral=True)
    posted = await post_release(
        interaction.guild,
        title=title,
        version=shown,
        build=build if versioned else None,
        intro=intro,
        sections=sections,
        author=interaction.user,
    )
    if not posted:
        return await deny(
            interaction,
            "Не удалось отправить сообщение. Проверьте права бота в канале новостей.",
        )

    if versioned:
        bot.db.set_release_state(gid, major, patch, build)
        suffix = f" · версия {shown} · build #{build}"
    else:
        suffix = ""
    await safe_send(
        channel_for(interaction.guild, "logs"),
        content=f"📰 {interaction.user.mention} опубликовал(а) «{title}»{suffix}.",
    )
    if versioned:
        await ok(interaction, f"Опубликовано: {title} · версия {shown} · build #{build}.")
    else:
        await ok(interaction, f"Опубликовано: {title}.")



# =============================================================== /канал
@bot.tree.command(name="канал", description="Привязать канал для логов")
@app_commands.describe(тип="Что писать в канал", канал="Текстовый канал или форум")
@app_commands.choices(
    тип=[
        app_commands.Choice(name="Кадровый аудит", value="audit"),
        app_commands.Choice(name="Варны и предупреждения", value="warns"),
        app_commands.Choice(name="Чёрный список", value="blacklist"),
        app_commands.Choice(name="Общие логи", value="logs"),
        app_commands.Choice(name="Новости и обновления", value="news"),
    ]
)
@app_commands.guild_only()
async def channel_cmd(
    interaction: discord.Interaction,
    тип: app_commands.Choice[str],
    канал: GuildChannel,
):
    if not is_manager(interaction):
        return await deny(interaction, "Нужны права «Управление ролями».")

    if isinstance(канал, discord.ForumChannel):
        perms = канал.permissions_for(interaction.guild.me)
        missing = [
            name
            for name, allowed in (
                ("Просмотр канала", perms.view_channel),
                ("Создание публичных веток", perms.create_public_threads),
                ("Отправка сообщений в ветках", perms.send_messages_in_threads),
            )
            if not allowed
        ]
        bot.db.set_channel(interaction.guild.id, тип.value, канал.id)
        text = (
            f"✅ Форум «{канал.mention}» привязан к разделу **{тип.name}**. "
            "Каждое событие бот создаст отдельным постом."
        )
    elif isinstance(канал, discord.TextChannel):
        bot.db.set_channel(interaction.guild.id, тип.value, канал.id)
        perms = канал.permissions_for(interaction.guild.me)
        missing = [
            name
            for name, allowed in (
                ("Просмотр канала", perms.view_channel),
                ("Отправка сообщений", perms.send_messages),
                ("Встраивать ссылки", perms.embed_links),
            )
            if not allowed
        ]
        text = f"✅ Канал «{канал.mention}» привязан к разделу **{тип.name}**."
    else:
        return await deny(
            interaction,
            "Это не текстовый канал и не форум. Выберите текстовый канал или форум — "
            "категории, голосовые и трибуны не подходят.",
        )

    if missing:
        text += "\n⚠️ Боту не хватает прав: " + ", ".join(missing) + "."
    if тип.value == "news":
        posted = await maybe_post_update(interaction.guild)
        text += (
            "\n📰 Последнее обновление бота опубликовано."
            if posted
            else "\n📰 Следующее обновление бот опубликует сам."
        )
    await interaction.response.send_message(text, ephemeral=True)


# =============================================================== /стиль
@bot.tree.command(name="стиль", description="Стиль оформления сообщений")
@app_commands.describe(стиль="Компактный или цветной")
@app_commands.choices(
    стиль=[
        app_commands.Choice(name="Компактный", value="compact"),
        app_commands.Choice(name="Цветной", value="color"),
    ]
)
@app_commands.guild_only()
async def style_cmd(interaction: discord.Interaction, стиль: app_commands.Choice[str]):
    if not is_manager(interaction):
        return await deny(interaction, "Нужны права «Управление ролями».")
    bot.db.set_setting(interaction.guild.id, "style", стиль.value)
    await interaction.response.send_message(f"✅ Стиль: **{стиль.name}**.", ephemeral=True)


# =============================================================== /лимит
@bot.tree.command(name="лимит", description="Сколько варнов считается пределом")
@app_commands.describe(значение="От 1 до 10 (по умолчанию 3)")
@app_commands.guild_only()
async def limit_cmd(
    interaction: discord.Interaction,
    значение: app_commands.Range[int, 1, 10],
):
    if not is_manager(interaction):
        return await deny(interaction, "Нужны права «Управление ролями».")
    bot.db.set_setting(interaction.guild.id, "warn_limit", str(значение))
    await interaction.response.send_message(
        f"✅ Предел варнов: **{значение}**.", ephemeral=True
    )


# ============================================================== /помощь
@bot.tree.command(name="помощь", description="Как работает бот администрации")
@app_commands.guild_only()
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(embed=fmt.help_embed(), ephemeral=True)


# ============================================================== /статус
@bot.tree.command(name="статус", description="Проверка: бот жив и всё настроено")
@app_commands.guild_only()
async def status_cmd(interaction: discord.Interaction):
    """Диагностика: если команда ответила — бот онлайн."""
    gid = interaction.guild_id
    guild = interaction.guild
    up = datetime.now(timezone.utc) - bot.started
    hours, rem = divmod(int(up.total_seconds()), 3600)
    minutes, seconds = divmod(rem, 60)

    ch = bot.db.channels(gid)
    helper = role_by_name(guild, config.HELPER_ROLE_NAME)
    player = role_by_name(guild, config.PLAYER_ROLE_NAME)
    bot_top = guild.me.top_role if guild.me else None

    embed = discord.Embed(title="✅ Бот на связи", color=0x2ECC71)
    embed.add_field(name="Пинг", value=f"{round(bot.latency * 1000)} мс", inline=True)
    embed.add_field(name="Аптайм", value=f"{hours} ч {minutes} мин {seconds} с", inline=True)
    embed.add_field(name="Сервер", value=f"`{gid}`", inline=True)
    embed.add_field(
        name="Каналы",
        value=(
            f"аудит: {'✅' if ch.get('audit') else '❌'} · "
            f"варны: {'✅' if ch.get('warns') else '❌'} · "
            f"ЧСП: {'✅' if ch.get('blacklist') else '❌'} · "
            f"логи: {'✅' if ch.get('logs') else '❌'} · "
            f"новости: {'✅' if ch.get('news') else '❌'}"
        ),
        inline=False,
    )
    def role_state(role) -> str:
        if role is None:
            return "❌"
        if bot_top is not None and role.position >= bot_top.position:
            return "⚠️ выше роли бота"
        return "✅"

    embed.add_field(
        name="Роли",
        value=(
            f"«{config.HELPER_ROLE_NAME}»: {role_state(helper)} · "
            f"«{config.PLAYER_ROLE_NAME}»: {role_state(player)}\n"
            f"Роль бота: {bot_top.mention if bot_top else '—'} "
            f"({bot_top.position if bot_top else 0})"
        ),
        inline=False,
    )
    embed.add_field(name="Стиль", value=bot.db.get_setting(gid, "style", config.DEFAULT_STYLE), inline=True)
    major, patch, build = bot.db.release_state(gid)
    embed.add_field(name="Версия", value=f"`{major}.{patch}` · build #{build}", inline=True)
    embed.add_field(name="Лимит варнов", value=str(bot.db.warn_limit(gid)), inline=True)
    embed.add_field(name="Администраторов", value=str(len(bot.db.admins_list(gid))), inline=True)
    embed.add_field(name="Серверов у бота", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="База", value=f"`{config.DB_PATH.name}`", inline=True)
    embed.set_footer(text=f"{config.BOT_NAME} • если это сообщение пришло — бот работает")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --------------------------------------------------------------- запуск
def selftest() -> int:
    """Проверка БД и текстов без подключения к Discord."""
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    tmp = Path(tempfile.mkdtemp()) / "test.db"
    db = Database(tmp)
    gid = 1

    # SteamID
    assert normalize_steamid("steam_0:1:123") == "STEAM_0:1:123"
    assert normalize_steamid(" 76561198000000000 ") == "76561198000000000"
    assert normalize_steamid("abc") is None
    assert steamid_problem("") is not None
    assert steamid_problem(None, required=False) is None
    assert steamid_mismatch({"steamid": "STEAM_0:1:1"}, "STEAM_0:1:2")
    assert steamid_mismatch({"steamid": "STEAM_0:1:1"}, "steam_0:1:1") is None

    # Карточки администраторов
    db.upsert_admin(
        gid, 100, nickname="Test", steamid="STEAM_0:1:1",
        role_id=501, role_name="Хелпер",
    )
    row = db.admin(gid, 100)
    assert row["role_id"] == 501 and row["role_name"] == "Хелпер"
    assert db.admin_by_steamid(gid, "steam_0:1:1")["user_id"] == 100
    db.set_admin_role(gid, 100, 502, "Модератор")
    assert db.admin(gid, 100)["role_name"] == "Модератор"
    assert db.search_admins(gid, "Test")[0]["user_id"] == 100
    assert db.search_admins(gid, "STEAM_0:1:1")[0]["user_id"] == 100

    # Выданные ботом роли
    db.add_granted_role(gid, 100, 502)
    assert db.granted_role_ids(gid, 100) == [502]
    db.clear_granted_roles(gid, 100)
    assert db.granted_role_ids(gid, 100) == []

    # История действий
    db.add_action(gid, 100, 200, "accept", "Должность: Хелпер", "тест", "STEAM_0:1:1")
    db.add_action(gid, 100, 200, "promote", "с «Хелпер» на «Модератор»", None, "STEAM_0:1:1")
    assert db.action_count(gid, 100) == 2
    assert db.actions_for(gid, 100)[0]["action"] == "promote"

    # Варны и предупреждения
    db.punish_add(gid, 100, "warn", "нарушение", 200)
    db.punish_add(gid, 100, "warn", "повторно", 200)
    db.punish_add(gid, 100, "notice", "мелочь", 200)
    assert db.punish_count(gid, 100, "warn") == 2
    assert db.punish_count(gid, 100, "notice") == 1
    removed = db.punish_deactivate_last(gid, 100, "warn", 200, "прощение")
    assert removed is not None and removed["active"] == 0
    assert db.punish_count(gid, 100, "warn") == 1
    assert db.punish_deactivate_all(gid, 100, 200, "снятие") == 2
    assert db.punish_count(gid, 100, "warn") == 0

    # Чёрный список проекта
    db.blacklist_add(
        gid, user_id=100, nickname="Test", steamid="STEAM_0:1:1",
        reason="нарушение", added_by=200,
    )
    assert db.blacklist_by_user(gid, 100) is not None
    assert db.blacklist_find(gid, steamid="steam_0:1:1") is not None
    assert db.blacklist_find(gid, nickname="test") is not None
    assert db.blacklist_remove(gid, 100) == 1
    assert db.blacklist_by_user(gid, 100) is None

    # Настройки
    assert db.get_setting(gid, "style", "color") == "color"
    db.set_setting(gid, "style", "compact")
    assert db.get_setting(gid, "style") == "compact"
    db.set_setting(gid, "warn_limit", "5")
    assert db.warn_limit(gid) == 5

    # Экспорт / импорт
    data = db.export_guild(gid)
    assert data["admins"] and data["actions"]
    counts = db.import_guild(999, data, overwrite=True)
    assert counts["admins"] == 1
    assert db.admin(999, 100)["steamid"] == "STEAM_0:1:1"
    assert db.known_guilds()

    # Проверка иерархии ролей
    class FakeRole:
        def __init__(self, name, position, rid=0):
            self.name = name
            self.position = position
            self.id = rid
            self.managed = False

        def is_default(self):
            return self.id == 0

    low = FakeRole("Хелпер", 10, 501)
    high = FakeRole("Майор", 40, 502)
    player = FakeRole("Игрок", 5, 503)

    class FakeGuild:
        def __init__(self):
            self.id = gid
            self.roles = [low, player, high]
            self.owner_id = 1
            self.me = SimpleNamespace(top_role=SimpleNamespace(position=32))

    fake_guild = FakeGuild()
    assert role_by_name(fake_guild, "хелпер") is low
    assert role_by_name(fake_guild, "нет такой") is None
    inter = SimpleNamespace(guild=fake_guild, user=SimpleNamespace(id=2, top_role=SimpleNamespace(position=30)))
    assert role_ceiling_error(inter, low) is None
    assert role_ceiling_error(inter, high)

    # Тексты
    class FakeUser:
        id = 1
        mention = "@tester"
        display_name = "tester"

    for style in ("compact", "color"):
        content, embed = fmt.event_message(
            style, "promote", FakeUser(), FakeUser(),
            nickname="Test", steamid="STEAM_0:1:1",
            role_text="Модератор", reason="хорошая работа", when="09.07.2024, 18:43",
        )
        assert (embed is None) == (style == "compact")
        if content:
            assert "Повышение" in content
        if embed:
            assert embed.title and "Повышение" in embed.title

    for style in ("compact", "color"):
        content, embed = fmt.punish_card(
            style, "warn", FakeUser(), FakeUser(),
            nickname="Test", steamid="STEAM_0:1:1", reason="нарушение",
            count=2, limit=3, when="09.07.2024, 18:43",
        )
        assert (embed is None) == (style == "compact")
        if content:
            assert "2/3" in content

    assert fmt.info_card(db.admin(gid, 100), FakeUser(), "Модератор", 2, 1, 0, 3, False)
    assert fmt.logs_embed(FakeUser(), db.admin(gid, 100), "Модератор",
                          db.actions_for(gid, 100), page=1)
    assert fmt.help_embed()

    # Новости и обновления
    news = fmt.news_embed("Тест", ["первая строка", "вторая"], FakeUser())
    assert news.title and "Тест" in news.title
    assert "первая строка" in news.description

    # Версии и билды
    assert parse_version("2.6") == (2, 6)
    assert parse_version("v2.6") == (2, 6)
    assert parse_version("2,6") == (2, 6)
    assert parse_version("1") == (1, 0)
    assert parse_version("beta") is None
    assert next_release((1, 0, 0), None, versioned=True) == ((1, 0, 1), "1.0")
    assert next_release((1, 0, 1), None, versioned=True) == ((1, 1, 2), "1.1")
    assert next_release((1, 1, 2), "2.0", versioned=True) == ((2, 0, 3), "2.0")
    assert next_release((2, 0, 3), None, versioned=False) == ((2, 0, 3), None)
    try:
        next_release((1, 0, 0), "abc", versioned=True)
    except ValueError:
        pass
    else:
        raise AssertionError("неверная версия должна давать ошибку")
    assert format_bullets("- одно\nдва\n\n• три") == "• одно\n• два\n• три"
    assert format_bullets(None) == ""

    db.set_release_state(gid, 2, 6, 12)
    assert db.release_state(gid) == (2, 6, 12)
    assert db.release_state(999) == (1, 0, 0)

    release = fmt.release_embed(
        version="2.6",
        build=12,
        title="Обновление 2.6",
        intro="Что нового",
        sections={"added": "• новое", "fixed": "• баг"},
        author=FakeUser(),
    )
    assert release.title and "Обновление 2.6" in release.title
    assert release.description == "Что нового"
    assert [f.name for f in release.fields] == ["🆕 Добавили", "🐞 Исправили"]
    assert "Версия 2.6" in release.footer.text and "build #12" in release.footer.text

    latest = updates.latest()
    assert latest["version"] == updates.BOT_VERSION
    assert latest["lines"]
    help_fields = "\n".join(f.value or "" for f in fmt.help_embed().fields)
    assert "`/новость`" in help_fields
    channel_cmd = next(c for c in bot.tree.get_commands() if c.name == "канал")
    assert "news" in {ch.value for ch in channel_cmd.parameters[0].choices}

    db.close()
    print("SELFTEST OK")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    if not config.TOKEN:
        print("Нет DISCORD_TOKEN в .env — создайте файл .env по образцу .env.example")
        return 1
    bot.run(config.TOKEN, log_handler=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

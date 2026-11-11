# -*- coding: utf-8 -*-
"""Хранилище данных бота: SQLite, без внешних зависимостей.

Все данные привязаны к guild_id, поэтому один бот может обслуживать
несколько серверов и легко переносится с тестового на основной.

Должность администратора — это обычная роль Discord: её ID и название
хранятся прямо в карточке администратора, отдельного списка «рангов» нет.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import config

KINDS = {"warn": "Предупреждение"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER NOT NULL,
    key      TEXT    NOT NULL,
    value    TEXT,
    PRIMARY KEY (guild_id, key)
);

CREATE TABLE IF NOT EXISTS channels (
    guild_id   INTEGER NOT NULL,
    kind       TEXT    NOT NULL,           -- audit | warns | blacklist | logs
    channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, kind)
);

CREATE TABLE IF NOT EXISTS admins (
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    nickname   TEXT,
    steamid    TEXT    NOT NULL DEFAULT '',
    role_id    INTEGER,
    role_name  TEXT,
    status     TEXT    NOT NULL DEFAULT 'active',  -- active | removed
    joined_at  TEXT,
    updated_at TEXT,
    PRIMARY KEY (guild_id, user_id)
);

-- Роли, которые бот выдал сам: при снятии снимаются все.
CREATE TABLE IF NOT EXISTS granted_roles (
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    role_id    INTEGER NOT NULL,
    granted_at TEXT    NOT NULL,
    PRIMARY KEY (guild_id, user_id, role_id)
);

CREATE TABLE IF NOT EXISTS punishments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    kind          TEXT    NOT NULL DEFAULT 'warn',
    reason        TEXT,
    actor_id      INTEGER,
    created_at    TEXT    NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    removed_by    INTEGER,
    removed_at    TEXT,
    remove_reason TEXT
);

CREATE TABLE IF NOT EXISTS actions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    target_id  INTEGER NOT NULL,
    actor_id   INTEGER NOT NULL,
    action     TEXT    NOT NULL,   -- accept | promote | demote | remove | warn_* | bl_* | role_add
    detail     TEXT,
    reason     TEXT,
    steamid    TEXT,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS blacklist (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id  INTEGER NOT NULL,
    user_id   INTEGER,
    nickname  TEXT,
    steamid   TEXT,
    reason    TEXT,
    added_by  INTEGER,
    added_at  TEXT    NOT NULL,
    active    INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_actions_target ON actions (guild_id, target_id);
CREATE INDEX IF NOT EXISTS idx_actions_actor  ON actions (guild_id, actor_id);
CREATE INDEX IF NOT EXISTS idx_admins_steam   ON admins (guild_id, steamid);
CREATE INDEX IF NOT EXISTS idx_punish_user    ON punishments (guild_id, user_id, active);
CREATE INDEX IF NOT EXISTS idx_bl_active      ON blacklist (guild_id, active);
"""

TZ = timezone(timedelta(hours=config.TIMEZONE_OFFSET))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fmt_dt(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ).strftime("%d.%m.%Y, %H:%M")
    except Exception:
        return "—"


class Database:
    def __init__(self, path: Path | str = config.DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        self._ensure_columns()
        self._migrate_legacy()

    # ------------------------------------------------------------------ utils
    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def _one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        with self._lock:
            row = self._conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def _write(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur.lastrowid or 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -------------------------------------------------------------- миграция
    def _ensure_columns(self) -> None:
        """Досоздаёт колонки, которых нет в старой базе.

        CREATE TABLE IF NOT EXISTS не меняет уже существующие таблицы,
        поэтому старый файл базы без этих колонок ломал запись.
        """
        wanted: dict[str, dict[str, str]] = {
            "admins": {
                "role_id": "INTEGER",
                "role_name": "TEXT",
                "steamid": "TEXT NOT NULL DEFAULT ''",
                "status": "TEXT NOT NULL DEFAULT 'active'",
            },
            "actions": {"steamid": "TEXT"},
            "punishments": {"remove_reason": "TEXT", "kind": "TEXT NOT NULL DEFAULT 'warn'"},
            "blacklist": {"steamid": "TEXT", "active": "INTEGER NOT NULL DEFAULT 1"},
        }
        for table, columns in wanted.items():
            existing = {c["name"] for c in self._all(f"PRAGMA table_info({table})")}
            if not existing:
                continue
            for name, ddl in columns.items():
                if name not in existing:
                    try:
                        self._write(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                    except sqlite3.OperationalError:
                        pass

    def _migrate_legacy(self) -> None:
        """Переносит данные старых версий бота (организации, паспорта, ранги)."""
        tables = {r["name"] for r in self._all("SELECT name FROM sqlite_master WHERE type='table'")}

        # Совсем старая таблица members -> admins (без паспортов).
        if "members" in tables:
            if not self._one("SELECT 1 FROM admins LIMIT 1"):
                for row in self._all("SELECT * FROM members"):
                    status = "active" if row.get("status") == "active" else "removed"
                    self._write(
                        "INSERT OR IGNORE INTO admins "
                        "(guild_id, user_id, nickname, steamid, status, joined_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            row["guild_id"],
                            row["user_id"],
                            row.get("nickname"),
                            (row.get("steamid") or "").strip(),
                            status,
                            row.get("joined_at"),
                            row.get("updated_at"),
                        ),
                    )
            try:
                self._write("ALTER TABLE members RENAME TO members_legacy")
            except sqlite3.OperationalError:
                pass

        # Старый чёрный список содержал паспорт — колонка больше не нужна.
        if "blacklist" in tables:
            cols = {c["name"] for c in self._all("PRAGMA table_info(blacklist)")}
            if "passport" in cols:
                try:
                    self._write("ALTER TABLE blacklist DROP COLUMN passport")
                except sqlite3.OperationalError:
                    pass

        # Старое «увольнение» теперь называется снятием.
        self._write("UPDATE actions SET action = 'remove' WHERE action = 'fire'")

        # Старый список рангов превращаем в роль прямо в карточке администратора.
        if "ranks" in tables:
            admins_cols = {c["name"] for c in self._all("PRAGMA table_info(admins)")}
            if "rank_id" in admins_cols:
                rows = self._all(
                    "SELECT a.guild_id, a.user_id, a.rank_id, r.role_id AS rid, r.name AS rname "
                    "FROM admins a LEFT JOIN ranks r ON r.id = a.rank_id "
                    "WHERE a.rank_id IS NOT NULL"
                )
                for row in rows:
                    self._write(
                        "UPDATE admins SET role_id = COALESCE(?, role_id), "
                        "role_name = COALESCE(?, role_name) WHERE guild_id = ? AND user_id = ?",
                        (row.get("rid"), row.get("rname"), row["guild_id"], row["user_id"]),
                    )
            try:
                self._write("DROP TABLE ranks")
            except sqlite3.OperationalError:
                pass

        # Опустевшую старую таблицу с паспортами удаляем совсем.
        current = {r["name"] for r in self._all("SELECT name FROM sqlite_master WHERE type='table'")}
        if "members_legacy" in current:
            row = self._one("SELECT COUNT(*) AS n FROM members_legacy")
            if row and row["n"] == 0:
                self._write("DROP TABLE members_legacy")

    # --------------------------------------------------------------- settings
    def get_setting(self, guild_id: int, key: str, default: str | None = None) -> str | None:
        row = self._one(
            "SELECT value FROM settings WHERE guild_id = ? AND key = ?", (guild_id, key)
        )
        return row["value"] if row and row["value"] is not None else default

    def set_setting(self, guild_id: int, key: str, value: str) -> None:
        self._write(
            "INSERT INTO settings (guild_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, key) DO UPDATE SET value = excluded.value",
            (guild_id, key, value),
        )

    def warn_limit(self, guild_id: int) -> int:
        raw = self.get_setting(guild_id, "warn_limit", str(config.WARN_LIMIT))
        try:
            return max(1, min(10, int(raw or config.WARN_LIMIT)))
        except (TypeError, ValueError):
            return config.WARN_LIMIT

    # ------------------------------------------------------- версия и билд
    def release_state(self, guild_id: int) -> tuple[int, int, int]:
        """Текущая версия сервера: (крупная, патч, билд)."""
        def _int(key: str, default: int) -> int:
            try:
                return max(0, int(self.get_setting(guild_id, key, str(default)) or default))
            except (TypeError, ValueError):
                return default

        return (
            _int("release_major", 1),
            _int("release_patch", 0),
            _int("release_build", 0),
        )

    def set_release_state(self, guild_id: int, major: int, patch: int, build: int) -> None:
        self.set_setting(guild_id, "release_major", str(major))
        self.set_setting(guild_id, "release_patch", str(patch))
        self.set_setting(guild_id, "release_build", str(build))

    # --------------------------------------------------------------- channels
    def get_channel(self, guild_id: int, kind: str) -> int | None:
        row = self._one(
            "SELECT channel_id FROM channels WHERE guild_id = ? AND kind = ?", (guild_id, kind)
        )
        return row["channel_id"] if row else None

    def set_channel(self, guild_id: int, kind: str, channel_id: int) -> None:
        self._write(
            "INSERT INTO channels (guild_id, kind, channel_id) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, kind) DO UPDATE SET channel_id = excluded.channel_id",
            (guild_id, kind, channel_id),
        )

    def channels(self, guild_id: int) -> dict[str, int]:
        rows = self._all("SELECT kind, channel_id FROM channels WHERE guild_id = ?", (guild_id,))
        return {r["kind"]: r["channel_id"] for r in rows}

    # ----------------------------------------------------------------- admins
    def admin(self, guild_id: int, user_id: int) -> dict | None:
        return self._one(
            "SELECT * FROM admins WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )

    def admin_by_steamid(self, guild_id: int, steamid: str) -> dict | None:
        return self._one(
            "SELECT * FROM admins WHERE guild_id = ? AND lower(steamid) = lower(?)",
            (guild_id, steamid.strip()),
        )

    def admins_list(self, guild_id: int, *, include_removed: bool = False) -> list[dict]:
        sql = "SELECT * FROM admins WHERE guild_id = ?"
        if not include_removed:
            sql += " AND status = 'active'"
        sql += " ORDER BY (status = 'active') DESC, COALESCE(role_name, '') ASC, nickname ASC"
        return self._all(sql, (guild_id,))

    def upsert_admin(
        self,
        guild_id: int,
        user_id: int,
        *,
        nickname: str | None = None,
        steamid: str | None = None,
        role_id: int | None = None,
        role_name: str | None = None,
        status: str | None = None,
    ) -> None:
        existing = self.admin(guild_id, user_id)
        ts = now_iso()
        steam = (steamid or "").strip()
        if existing is None:
            self._write(
                "INSERT INTO admins "
                "(guild_id, user_id, nickname, steamid, role_id, role_name, status, joined_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (guild_id, user_id, nickname, steam, role_id, role_name,
                 status or "active", ts, ts),
            )
            return

        self._write(
            "UPDATE admins SET nickname = ?, steamid = ?, role_id = ?, role_name = ?, "
            "status = ?, updated_at = ? WHERE guild_id = ? AND user_id = ?",
            (
                nickname if nickname is not None else existing["nickname"],
                steam if steam else existing["steamid"],
                role_id if role_id is not None else existing.get("role_id"),
                role_name if role_name is not None else existing.get("role_name"),
                status if status is not None else existing["status"],
                ts,
                guild_id,
                user_id,
            ),
        )

    def set_admin_role(
        self, guild_id: int, user_id: int, role_id: int | None, role_name: str | None = None
    ) -> None:
        self._write(
            "UPDATE admins SET role_id = ?, role_name = COALESCE(?, role_name), updated_at = ? "
            "WHERE guild_id = ? AND user_id = ?",
            (role_id, role_name, now_iso(), guild_id, user_id),
        )

    def set_admin_steamid(self, guild_id: int, user_id: int, steamid: str) -> None:
        self._write(
            "UPDATE admins SET steamid = ?, updated_at = ? WHERE guild_id = ? AND user_id = ?",
            (steamid.strip(), now_iso(), guild_id, user_id),
        )

    def set_admin_status(self, guild_id: int, user_id: int, status: str) -> None:
        self._write(
            "UPDATE admins SET status = ?, role_id = NULL, updated_at = ? "
            "WHERE guild_id = ? AND user_id = ?",
            (status, now_iso(), guild_id, user_id),
        )

    def search_admins(self, guild_id: int, query: str, limit: int = 15) -> list[dict]:
        q = f"%{query.strip()}%"
        return self._all(
            "SELECT * FROM admins WHERE guild_id = ? AND ("
            "nickname LIKE ? OR steamid LIKE ? OR CAST(user_id AS TEXT) LIKE ?"
            ") ORDER BY (status = 'active') DESC, updated_at DESC LIMIT ?",
            (guild_id, q, q, q, limit),
        )

    # --------------------------------------------------------- выданные роли
    def add_granted_role(self, guild_id: int, user_id: int, role_id: int) -> None:
        self._write(
            "INSERT OR IGNORE INTO granted_roles (guild_id, user_id, role_id, granted_at) "
            "VALUES (?, ?, ?, ?)",
            (guild_id, user_id, role_id, now_iso()),
        )

    def granted_role_ids(self, guild_id: int, user_id: int) -> list[int]:
        rows = self._all(
            "SELECT role_id FROM granted_roles WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return [int(r["role_id"]) for r in rows]

    def clear_granted_roles(self, guild_id: int, user_id: int) -> None:
        self._write(
            "DELETE FROM granted_roles WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )

    # ------------------------------------------------------------ punishments
    def punish_add(
        self, guild_id: int, user_id: int, kind: str, reason: str | None, actor_id: int
    ) -> int:
        return self._write(
            "INSERT INTO punishments (guild_id, user_id, kind, reason, actor_id, created_at, active) "
            "VALUES (?, ?, ?, ?, ?, ?, 1)",
            (guild_id, user_id, kind, reason, actor_id, now_iso()),
        )

    def punish_count(self, guild_id: int, user_id: int, kind: str) -> int:
        row = self._one(
            "SELECT COUNT(*) AS n FROM punishments "
            "WHERE guild_id = ? AND user_id = ? AND kind = ? AND active = 1",
            (guild_id, user_id, kind),
        )
        return int(row["n"]) if row else 0

    def punish_deactivate_last(
        self,
        guild_id: int,
        user_id: int,
        kind: str,
        actor_id: int,
        reason: str | None = None,
    ) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM punishments "
                "WHERE guild_id = ? AND user_id = ? AND kind = ? AND active = 1 "
                "ORDER BY id DESC LIMIT 1",
                (guild_id, user_id, kind),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE punishments SET active = 0, removed_by = ?, removed_at = ?, remove_reason = ? "
                "WHERE id = ?",
                (actor_id, now_iso(), reason, row["id"]),
            )
            self._conn.commit()
            return self._one("SELECT * FROM punishments WHERE id = ?", (row["id"],))

    def punish_deactivate_all(
        self, guild_id: int, user_id: int, actor_id: int, reason: str | None = None
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE punishments SET active = 0, removed_by = ?, removed_at = ?, remove_reason = ? "
                "WHERE guild_id = ? AND user_id = ? AND active = 1",
                (actor_id, now_iso(), reason, guild_id, user_id),
            )
            self._conn.commit()
            return cur.rowcount

    def punish_history(
        self, guild_id: int, user_id: int, kind: str | None = None, limit: int = 50
    ) -> list[dict]:
        sql = (
            "SELECT p.*, a.nickname, a.steamid FROM punishments p "
            "LEFT JOIN admins a ON a.guild_id = p.guild_id AND a.user_id = p.user_id "
            "WHERE p.guild_id = ? AND p.user_id = ?"
        )
        params: list[Any] = [guild_id, user_id]
        if kind:
            sql += " AND p.kind = ?"
            params.append(kind)
        sql += " ORDER BY p.id DESC LIMIT ?"
        params.append(limit)
        return self._all(sql, params)

    # ---------------------------------------------------------------- actions
    def add_action(
        self,
        guild_id: int,
        target_id: int,
        actor_id: int,
        action: str,
        detail: str | None = None,
        reason: str | None = None,
        steamid: str | None = None,
    ) -> int:
        return self._write(
            "INSERT INTO actions (guild_id, target_id, actor_id, action, detail, reason, steamid, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, target_id, actor_id, action, detail, reason, steamid, now_iso()),
        )

    def actions_for(self, guild_id: int, target_id: int, limit: int = 100) -> list[dict]:
        return self._all(
            "SELECT * FROM actions WHERE guild_id = ? AND target_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (guild_id, target_id, limit),
        )

    def action_count(self, guild_id: int, target_id: int) -> int:
        row = self._one(
            "SELECT COUNT(*) AS n FROM actions WHERE guild_id = ? AND target_id = ?",
            (guild_id, target_id),
        )
        return int(row["n"]) if row else 0

    # -------------------------------------------------------------- blacklist
    def blacklist_add(
        self,
        guild_id: int,
        *,
        user_id: int | None,
        nickname: str | None,
        steamid: str | None,
        reason: str | None,
        added_by: int,
    ) -> int:
        return self._write(
            "INSERT INTO blacklist (guild_id, user_id, nickname, steamid, reason, added_by, added_at, active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (guild_id, user_id, nickname, steamid, reason, added_by, now_iso()),
        )

    def blacklist_find(
        self,
        guild_id: int,
        *,
        user_id: int | None = None,
        nickname: str | None = None,
        steamid: str | None = None,
    ) -> dict | None:
        clauses, params = [], [guild_id]
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if nickname:
            clauses.append("lower(nickname) = lower(?)")
            params.append(nickname.strip())
        if steamid:
            clauses.append("lower(steamid) = lower(?)")
            params.append(steamid.strip())
        if not clauses:
            return None
        sql = (
            "SELECT * FROM blacklist WHERE guild_id = ? AND active = 1 AND ("
            + " OR ".join(clauses)
            + ") ORDER BY id DESC LIMIT 1"
        )
        return self._one(sql, params)

    def blacklist_by_user(self, guild_id: int, user_id: int) -> dict | None:
        return self._one(
            "SELECT * FROM blacklist WHERE guild_id = ? AND user_id = ? AND active = 1 "
            "ORDER BY id DESC LIMIT 1",
            (guild_id, user_id),
        )

    def blacklist_remove(self, guild_id: int, user_id: int) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE blacklist SET active = 0 WHERE guild_id = ? AND user_id = ? AND active = 1",
                (guild_id, user_id),
            )
            self._conn.commit()
            return cur.rowcount

    def blacklist_all(self, guild_id: int, limit: int = 50) -> list[dict]:
        return self._all(
            "SELECT * FROM blacklist WHERE guild_id = ? AND active = 1 ORDER BY id DESC LIMIT ?",
            (guild_id, limit),
        )

    # ------------------------------------------------------------- перенос ДБ
    def export_guild(self, guild_id: int) -> dict:
        """Выгрузка всех данных сервера для переноса на другой guild_id.

        Роли и каналы не переносятся: их ID на другом сервере другие.
        """
        return {
            "settings": self._all(
                "SELECT key, value FROM settings WHERE guild_id = ?", (guild_id,)
            ),
            "admins": self._all("SELECT * FROM admins WHERE guild_id = ?", (guild_id,)),
            "punishments": self._all(
                "SELECT user_id, kind, reason, actor_id, created_at, active, "
                "removed_by, removed_at, remove_reason FROM punishments WHERE guild_id = ?",
                (guild_id,),
            ),
            "actions": self._all(
                "SELECT target_id, actor_id, action, detail, reason, steamid, created_at "
                "FROM actions WHERE guild_id = ?",
                (guild_id,),
            ),
            "blacklist": self._all(
                "SELECT user_id, nickname, steamid, reason, added_by, added_at, active "
                "FROM blacklist WHERE guild_id = ?",
                (guild_id,),
            ),
        }

    def import_guild(self, guild_id: int, data: dict, *, overwrite: bool = False) -> dict:
        """Импорт данных, полученных export_guild. Возвращает счётчики."""
        if overwrite:
            self.wipe_guild(guild_id)

        counts = {"settings": 0, "admins": 0, "punishments": 0, "actions": 0, "blacklist": 0}

        for row in data.get("settings", []):
            self.set_setting(guild_id, row["key"], row.get("value"))
            counts["settings"] += 1

        for row in data.get("admins", []):
            self._write(
                "INSERT OR REPLACE INTO admins "
                "(guild_id, user_id, nickname, steamid, role_id, role_name, status, joined_at, updated_at) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?)",
                (
                    guild_id,
                    row["user_id"],
                    row.get("nickname"),
                    row.get("steamid") or "",
                    row.get("role_name"),
                    row.get("status") or "active",
                    row.get("joined_at"),
                    row.get("updated_at"),
                ),
            )
            counts["admins"] += 1

        for row in data.get("punishments", []):
            self._write(
                "INSERT INTO punishments "
                "(guild_id, user_id, kind, reason, actor_id, created_at, active, "
                "removed_by, removed_at, remove_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    guild_id,
                    row["user_id"],
                    row.get("kind") or "warn",
                    row.get("reason"),
                    row.get("actor_id"),
                    row.get("created_at") or now_iso(),
                    row.get("active", 1),
                    row.get("removed_by"),
                    row.get("removed_at"),
                    row.get("remove_reason"),
                ),
            )
            counts["punishments"] += 1

        for row in data.get("actions", []):
            self._write(
                "INSERT INTO actions "
                "(guild_id, target_id, actor_id, action, detail, reason, steamid, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    guild_id,
                    row["target_id"],
                    row["actor_id"],
                    row["action"],
                    row.get("detail"),
                    row.get("reason"),
                    row.get("steamid"),
                    row.get("created_at") or now_iso(),
                ),
            )
            counts["actions"] += 1

        for row in data.get("blacklist", []):
            self._write(
                "INSERT INTO blacklist "
                "(guild_id, user_id, nickname, steamid, reason, added_by, added_at, active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    guild_id,
                    row.get("user_id"),
                    row.get("nickname"),
                    row.get("steamid"),
                    row.get("reason"),
                    row.get("added_by"),
                    row.get("added_at") or now_iso(),
                    row.get("active", 1),
                ),
            )
            counts["blacklist"] += 1

        return counts

    def known_guilds(self) -> list[dict]:
        rows = self._all(
            "SELECT guild_id, "
            "(SELECT COUNT(*) FROM admins a WHERE a.guild_id = x.guild_id AND a.status = 'active') AS admins, "
            "(SELECT COUNT(*) FROM actions ac WHERE ac.guild_id = x.guild_id) AS actions "
            "FROM (SELECT DISTINCT guild_id FROM settings "
            "UNION SELECT DISTINCT guild_id FROM admins "
            "UNION SELECT DISTINCT guild_id FROM actions) x ORDER BY guild_id"
        )
        return rows

    # ------------------------------------------------------------ full cleanup
    def wipe_guild(self, guild_id: int) -> None:
        with self._lock:
            for table in ("actions", "punishments", "admins", "granted_roles",
                          "blacklist", "channels", "settings"):
                self._conn.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
            self._conn.commit()

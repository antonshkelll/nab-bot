"""
База данных бота «Спроси ССт НАБ» (SQLite, файл data/nab.db).

Здесь только хранение: таблицы, миграции, настройки, пользователи и анонимные
счётчики статистики. Логика обращений, анонсов и опросов — в reports.py и content.py.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import config

DB_PATH: Path = config.DATA_DIR / "nab.db"
_LOCK = threading.RLock()
_DB: sqlite3.Connection | None = None

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY, first_name TEXT, username TEXT,
    started INTEGER DEFAULT 0, created_at INTEGER, last_seen INTEGER, blocked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reports(
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, created_at INTEGER, source TEXT,
    user_id INTEGER, user_name TEXT, anon INTEGER DEFAULT 0, anon_ref TEXT, anon_tag TEXT,
    text TEXT, location TEXT, photos INTEGER DEFAULT 0,
    status TEXT DEFAULT 'new', taken_by TEXT, taken_at INTEGER, resolved_by TEXT, resolved_at INTEGER,
    chat_id TEXT, message_id INTEGER, last_team_at INTEGER, user_seen_at INTEGER, sla_at INTEGER,
    rating INTEGER, rating_text TEXT, rated_at INTEGER);
CREATE INDEX IF NOT EXISTS ix_reports_status ON reports(status);
CREATE INDEX IF NOT EXISTS ix_reports_user ON reports(user_id);
CREATE INDEX IF NOT EXISTS ix_reports_tag ON reports(anon_tag);
CREATE TABLE IF NOT EXISTS report_msgs(
    id INTEGER PRIMARY KEY AUTOINCREMENT, report_id INTEGER, author TEXT, who TEXT, text TEXT, created_at INTEGER);
CREATE INDEX IF NOT EXISTS ix_report_msgs ON report_msgs(report_id);
CREATE TABLE IF NOT EXISTS msg_map(
    chat_key TEXT, message_id INTEGER, report_id INTEGER, kind TEXT, PRIMARY KEY(chat_key, message_id));
CREATE TABLE IF NOT EXISTS news(
    id INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT, title TEXT, body TEXT, image TEXT,
    btn_text TEXT, btn_url TEXT, pinned INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0,
    created_at INTEGER, author TEXT, sent INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, descr TEXT, place TEXT, url TEXT, kind TEXT,
    date TEXT, time TEXT, rsvp INTEGER DEFAULT 1, notify_all INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0,
    created_at INTEGER, created_by INTEGER);
CREATE TABLE IF NOT EXISTS event_rsvp(
    event_id INTEGER, uid INTEGER, answer TEXT, name TEXT, at INTEGER, PRIMARY KEY(event_id, uid));
CREATE TABLE IF NOT EXISTS event_subs(event_id INTEGER, uid INTEGER, PRIMARY KEY(event_id, uid));
CREATE TABLE IF NOT EXISTS event_sent(event_id INTEGER, uid INTEGER, stage TEXT, PRIMARY KEY(event_id, uid, stage));
CREATE TABLE IF NOT EXISTS polls(
    id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT, options TEXT, multi INTEGER DEFAULT 0,
    closed INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0, created_at INTEGER, created_by INTEGER, ends_at INTEGER);
CREATE TABLE IF NOT EXISTS poll_votes(poll_id INTEGER, uid INTEGER, opt INTEGER, at INTEGER, PRIMARY KEY(poll_id, uid, opt));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS stat(day TEXT, name TEXT, key TEXT, n INTEGER DEFAULT 0, PRIMARY KEY(day, name, key));
CREATE TABLE IF NOT EXISTS stat_uniq(period TEXT, uh TEXT, PRIMARY KEY(period, uh));
"""


def db() -> sqlite3.Connection:
    global _DB
    if _DB is None:
        with _LOCK:
            if _DB is None:
                DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
                conn.row_factory = sqlite3.Row
                conn.executescript(SCHEMA)
                conn.commit()
                _DB = conn
                _migrate_legacy_users()
    return _DB


def reset_for_tests(path: Path) -> None:
    """Тесты работают с отдельным файлом базы."""
    global _DB, DB_PATH
    with _LOCK:
        if _DB is not None:
            _DB.close()
        _DB = None
        DB_PATH = path


def q(sql: str, args: Iterable[Any] = (), one: bool = False):
    with _LOCK:
        cur = db().execute(sql, tuple(args))
        rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def x(sql: str, args: Iterable[Any] = ()) -> int:
    """Изменение данных. Возвращает id новой строки (для INSERT) или число затронутых строк."""
    with _LOCK:
        conn = db()
        cur = conn.execute(sql, tuple(args))
        conn.commit()
        return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") and cur.rowcount else cur.rowcount


def changed(sql: str, args: Iterable[Any] = ()) -> int:
    with _LOCK:
        conn = db()
        cur = conn.execute(sql, tuple(args))
        conn.commit()
        return cur.rowcount


def count(sql: str, args: Iterable[Any] = ()) -> int:
    r = q(sql, args, one=True)
    return int(r[0] or 0) if r else 0


# ---------------------------------------------------------------- настройки

def setting(key: str, default: Any = None) -> Any:
    r = q("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return r["value"] if r else default


def set_setting(key: str, value: Any) -> None:
    x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def setting_json(key: str, default: Any = None) -> Any:
    raw = setting(key)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def set_setting_json(key: str, value: Any) -> None:
    set_setting(key, json.dumps(value, ensure_ascii=False))


# ---------------------------------------------------------------- пользователи

def touch_user(uid: int, first_name: str = "", username: str = "", started: bool = False) -> None:
    now = int(time.time())
    now -= now % 86400              # «был в боте» — с точностью до дня, чтобы время не совпадало с анонимными обращениями
    x("""INSERT INTO users(id, first_name, username, started, created_at, last_seen) VALUES(?,?,?,?,?,?)
         ON CONFLICT(id) DO UPDATE SET first_name=COALESCE(NULLIF(excluded.first_name,''), users.first_name),
         username=COALESCE(NULLIF(excluded.username,''), users.username), last_seen=excluded.last_seen,
         blocked=0""",
      (int(uid), first_name or "", username or "", 1 if started else 0, now, now))
    if started:
        x("UPDATE users SET started=1 WHERE id=?", (int(uid),))


def all_user_ids() -> list[int]:
    return [r["id"] for r in q("SELECT id FROM users WHERE blocked=0 ORDER BY id")]


def mark_blocked(uid: int) -> None:
    x("UPDATE users SET blocked=1 WHERE id=?", (int(uid),))


def user_name(uid: int) -> str:
    r = q("SELECT first_name, username FROM users WHERE id=?", (int(uid),), one=True)
    if not r:
        return "Пользователь"
    return ("@" + r["username"]) if r["username"] else (r["first_name"] or "Пользователь")


def _migrate_legacy_users() -> None:
    """Подписчики из старой базы users_nab.db переезжают в новую — для рассылок."""
    if setting("legacy_users_migrated") == "1":
        return
    legacy = config.BASE_DIR / "users_nab.db"
    moved = 0
    if legacy.exists():
        try:
            old = sqlite3.connect(legacy)
            ids = [r[0] for r in old.execute("SELECT user_id FROM users").fetchall()]
            old.close()
            now = int(time.time())
            with _LOCK:
                conn = db()
                for uid in ids:
                    cur = conn.execute("INSERT OR IGNORE INTO users(id, started, created_at, last_seen) VALUES(?,1,?,?)",
                                       (int(uid), now, now))
                    moved += cur.rowcount
                conn.commit()
        except sqlite3.Error:
            pass
    set_setting("legacy_users_migrated", "1")
    if moved:
        set_setting("legacy_users_count", moved)


# ---------------------------------------------------------------- секреты

_SECRET: bytes | None = None


def secret() -> bytes:
    """Ключ сервера для шифрования анонимной связи и подписей. Хранится в .env (SECRET_KEY),
    в базе его нет — копия базы без .env не раскрывает авторов анонимных обращений."""
    global _SECRET
    if _SECRET is None:
        import os
        key = os.getenv("SECRET_KEY") or ("token|" + (config.TOKEN or "dev"))
        _SECRET = hashlib.sha256(("nab|" + key).encode()).digest()
    return _SECRET


def sign(msg: str) -> str:
    return hmac.new(secret(), msg.encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- анонимная статистика

def _periods(ts: float | None = None) -> list[str]:
    t = time.gmtime((ts or time.time()) + 3 * 3600)        # МСК
    day = time.strftime("%Y-%m-%d", t)
    iso = time.strftime("%G-W%V", t)
    month = time.strftime("%Y-%m", t)
    return [f"d:{day}", f"w:{iso}", f"m:{month}"]


def today() -> str:
    return _periods()[0][2:]


def hit(name: str, key: str = "", n: int = 1, day: str | None = None) -> None:
    """Счётчик события за день: открытия экранов, нажатия, поиски. Без привязки к людям."""
    x("""INSERT INTO stat(day,name,key,n) VALUES(?,?,?,?)
         ON CONFLICT(day,name,key) DO UPDATE SET n=n+excluded.n""", (day or today(), name, (key or "")[:80], int(n)))


def seen(uid: int) -> None:
    """Уникальные посетители за день/неделю/месяц. Хранится только хеш с солью периода —
    по нему нельзя узнать человека и нельзя связать разные периоды между собой."""
    with _LOCK:
        conn = db()
        for period in _periods():
            uh = hmac.new(secret() + period.encode(), str(int(uid)).encode(), hashlib.sha256).hexdigest()[:20]
            conn.execute("INSERT OR IGNORE INTO stat_uniq(period, uh) VALUES(?,?)", (period, uh))
        conn.commit()


def uniques(period: str) -> int:
    return count("SELECT COUNT(*) FROM stat_uniq WHERE period=?", (period,))

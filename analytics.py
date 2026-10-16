"""
Продуктовая аналитика бота: событийный лог в SQLite и отчёт по метрикам.

Что пишется:
  * каждое сообщение и нажатие кнопки (middleware) — тип, текст кнопки или команда,
    FSM-состояние;
  * бизнес-события через track(): report_sent (обращение ушло в рабочий чат),
    admin_reply_delivered (студент получил ответ).

Приватность: вместо Telegram ID хранится HMAC-хеш, текст обращений не сохраняется —
только названия кнопок и команд. Ошибки записи бота не роняют.

Отчёт: python tools/stats.py   или   nab stats
"""
from __future__ import annotations

import hashlib
import hmac
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        INTEGER NOT NULL,
    user_hash TEXT    NOT NULL,
    event     TEXT    NOT NULL,
    label     TEXT,
    state     TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_ts   ON events(ts);
CREATE INDEX IF NOT EXISTS ix_events_user ON events(user_hash, ts);
"""

MAX_LABEL = 40          # короче — кнопка или команда; длиннее — текст обращения, его не храним

_conn: sqlite3.Connection | None = None
_salt: bytes = b""
_lock = threading.Lock()


def init(db_path: Path | str, salt: str) -> None:
    global _conn, _salt
    _salt = salt.encode()
    _conn = sqlite3.connect(str(db_path), check_same_thread=False)
    _conn.executescript(SCHEMA)
    _conn.commit()


def user_hash(user_id: int) -> str:
    return hmac.new(_salt, str(user_id).encode(), hashlib.sha256).hexdigest()[:16]


def track(user_id: int, event: str, label: str | None = None, state: str | None = None) -> None:
    if _conn is None:
        return
    try:
        with _lock:
            _conn.execute("INSERT INTO events (ts, user_hash, event, label, state) VALUES (?, ?, ?, ?, ?)",
                          (int(time.time()), user_hash(user_id), event, label, state))
            _conn.commit()
    except sqlite3.Error:
        pass


def classify(update: Any) -> tuple[int, str, str | None] | None:
    """Апдейт Telegram → (user_id, тип события, метка). Остальные типы апдейтов пропускаем."""
    msg = getattr(update, "message", None)
    if msg is not None and getattr(msg, "from_user", None):
        text = msg.text or ""
        if text.startswith("/"):
            return msg.from_user.id, "command", text.split()[0][:MAX_LABEL]
        if msg.photo:
            return msg.from_user.id, "photo", None
        if getattr(msg, "web_app_data", None):
            return msg.from_user.id, "webapp", None
        if text and len(text) <= MAX_LABEL and "\n" not in text:
            return msg.from_user.id, "button", text
        return msg.from_user.id, "message", None
    cb = getattr(update, "callback_query", None)
    if cb is not None and getattr(cb, "from_user", None):
        return cb.from_user.id, "callback", (cb.data or "")[:MAX_LABEL]
    return None


class AnalyticsMiddleware:
    """Outer-middleware для dp.update: aiogram принимает любой callable(handler, event, data)."""

    async def __call__(self, handler, event, data):
        try:
            info = classify(event)
            if info:
                ctx = data.get("state")
                track(*info, state=await ctx.get_state() if ctx else None)
        except Exception:  # noqa: BLE001 — аналитика вторична
            pass
        return await handler(event, data)


# ---------- отчёт ----------
def report(db_path: Path | str, days: int = 30) -> str:
    con = sqlite3.connect(str(db_path))
    now = int(time.time())
    since = now - days * 86400

    def one(sql, *a):
        return con.execute(sql, a).fetchone()[0] or 0

    def uniq(sec):
        return one("SELECT count(DISTINCT user_hash) FROM events WHERE ts >= ?", now - sec)

    dau, wau, mau = uniq(86400), uniq(7 * 86400), uniq(30 * 86400)
    lines = [f"Пользователи: DAU {dau} · WAU {wau} · MAU {mau}"
             + (f" · stickiness {dau / mau:.0%}" if mau else ""), ""]

    sent = con.execute("SELECT label, count(*) FROM events WHERE event = 'report_sent' AND ts >= ? "
                       "GROUP BY label ORDER BY 2 DESC", (since,)).fetchall()
    lines.append(f"Обращения за {days} дней: " + (", ".join(f"{k} {n}" for k, n in sent) or "нет"))

    top = con.execute("SELECT label, count(DISTINCT user_hash) FROM events WHERE event = 'button' AND ts >= ? "
                      "GROUP BY label ORDER BY 2 DESC LIMIT 8", (since,)).fetchall()
    if top:
        lines += ["", "Популярные разделы (уникальные пользователи):"]
        lines += [f"  {label:<28}{n:>5}" for label, n in top]

    started = one("SELECT count(*) FROM events WHERE event = 'button' AND label LIKE '%Поломки%' AND ts >= ?", since)
    repairs = one("SELECT count(*) FROM events WHERE event = 'report_sent' AND label = 'repair' AND ts >= ?", since)
    if started:
        lines += ["", f"Воронка «Поломки»: открыли {started} → отправили {repairs} ({repairs / started:.0%})"]

    waits = [r[0] for r in con.execute("""
        SELECT min(a.ts) - s.ts FROM events s
        JOIN events a ON a.user_hash = s.user_hash AND a.event = 'admin_reply_delivered' AND a.ts > s.ts
        WHERE s.event = 'report_sent' AND s.ts >= ? GROUP BY s.id""", (since,))]
    if waits:
        waits.sort()
        lines.append(f"Ответ Студсовета: медиана {waits[len(waits) // 2] / 3600:.1f} ч, ответили на {len(waits)} обращений")

    cohorts = con.execute("""
        WITH first AS (SELECT user_hash, min(ts) AS t0 FROM events GROUP BY user_hash)
        SELECT count(*), sum(EXISTS (SELECT 1 FROM events e WHERE e.user_hash = f.user_hash
                                     AND e.ts BETWEEN f.t0 + 7 * 86400 AND f.t0 + 14 * 86400))
        FROM first f WHERE f.t0 BETWEEN ? AND ?""", (now - 60 * 86400, now - 14 * 86400)).fetchone()
    if cohorts[0]:
        lines.append(f"Retention 2-й недели (пришли 2–8 недель назад): {cohorts[1] / cohorts[0]:.0%} из {cohorts[0]}")
    con.close()
    return "\n".join(lines)

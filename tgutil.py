"""Мелкие помощники для работы с Telegram: клавиатуры и «безопасная» отправка."""
from __future__ import annotations

import asyncio
import html
import logging
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

import config
import store

log = logging.getLogger("sprosinab.tg")


def esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


def short(s: Any, n: int) -> str:
    """Обрезает ДО экранирования — чтобы не разрезать «&amp;»."""
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def btn(text: str, data: str | None = None, url: str | None = None, app: str | None = None) -> dict:
    b: dict = {"text": text}
    if data is not None:
        b["callback_data"] = data
    elif url:
        b["url"] = url
    elif app is not None:
        b["app"] = app
    return b


def ikb(rows: list[list[dict]]) -> InlineKeyboardMarkup:
    out = []
    for row in rows:
        r = []
        for b in row:
            if "app" in b:
                if not config.WEBAPP_URL:
                    continue
                path = b["app"].lstrip("/")
                url = f"{config.WEBAPP_URL}/{path}" if path else config.WEBAPP_URL
                r.append(InlineKeyboardButton(text=b["text"], web_app=WebAppInfo(url=url)))
            elif "url" in b:
                r.append(InlineKeyboardButton(text=b["text"], url=b["url"]))
            else:
                r.append(InlineKeyboardButton(text=b["text"], callback_data=b["callback_data"]))
        if r:
            out.append(r)
    return InlineKeyboardMarkup(inline_keyboard=out)


def _is_blocked(e: Exception) -> bool:
    t = str(e).lower()
    return "blocked" in t or "deactivated" in t or "chat not found" in t or "forbidden" in t


async def safe(coro, uid: int | None = None):
    """Выполняет вызов Telegram и не роняет бота: возвращает результат или None."""
    try:
        return await coro
    except Exception as e:  # noqa: BLE001
        if uid and _is_blocked(e):
            store.mark_blocked(uid)
        log.warning("Telegram: %s", e)
        return None


async def send(bot, chat_id: int | str, text: str, kb=None, **kw):
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}
    if kb is not None:
        params["reply_markup"] = kb
    params.update(kw)
    uid = chat_id if isinstance(chat_id, int) and chat_id > 0 else None
    return await safe(bot.send_message(**params), uid)


async def broadcast(bot, uids: list[int], text: str, kb=None, photo=None, pause: float = 0.05) -> tuple[int, int]:
    """Рассылка с паузой — бережно к лимитам Telegram. Возвращает (доставлено, всего)."""
    ok = 0
    for uid in uids:
        if photo is not None:
            m = await safe(bot.send_photo(chat_id=uid, photo=photo, caption=text, parse_mode="HTML", reply_markup=kb), uid)
            if m is not None and getattr(m, "photo", None):
                photo = m.photo[-1].file_id          # дальше шлём по file_id, без повторной загрузки
        else:
            m = await send(bot, uid, text, kb)
        if m is not None:
            ok += 1
        await asyncio.sleep(pause)
    return ok, len(uids)


def stars(n: int) -> str:
    n = max(0, min(5, int(n or 0)))
    return "★" * n + "☆" * (5 - n)

"""
Обращения: создание, статусы «в работе / решено», переписка с автором (в том числе
анонимным), оценка после решения, напоминание о просроченных, сводка и выгрузка.

Анонимность. Для анонимного обращения имя и Telegram ID в базе не хранятся.
Хранится только номер чата автора, зашифрованный ключом сервера (anon_ref), —
чтобы бот мог переслать ему ответ руководства. Ни в группе, ни в приложении,
ни в выгрузке связь «человек ↔ обращение» не видна.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

from aiogram.types import BufferedInputFile, InputMediaPhoto

import config
import store
from delivery import KINDS, STATUS_TITLE, display_name, group_text, what
from tgutil import btn, esc, ikb, safe, send, short, stars

log = logging.getLogger("sprosinab.reports")
MAX_TEXT = 3000


class _Limit:
    """Не больше n действий за window секунд — чтобы рабочий чат нельзя было завалить сообщениями."""
    def __init__(self, n: int, window: int):
        self.n, self.window, self.hits = n, window, {}

    def allow(self, key) -> bool:
        now = time.time()
        q = [t for t in self.hits.get(key, []) if now - t < self.window]
        if len(q) >= self.n:
            self.hits[key] = q
            return False
        q.append(now)
        self.hits[key] = q
        return True


REPLY_LIMIT = _Limit(8, 600)
STAR_LIMIT = _Limit(12, 600)
REVIEW_LIMIT = _Limit(4, 600)


# ---------------------------------------------------------------- анонимная связь

def _ks(rid: int) -> bytes:
    return hashlib.sha256(store.secret() + b"|anon|" + str(int(rid)).encode()).digest()[:8]


def anon_seal(rid: int, uid: int) -> str:
    b = int(uid).to_bytes(8, "big", signed=True)
    return bytes(a ^ k for a, k in zip(b, _ks(rid))).hex()


def anon_open(rid: int, ref: str) -> int | None:
    try:
        b = bytes.fromhex(ref)
        return int.from_bytes(bytes(a ^ k for a, k in zip(b, _ks(rid))), "big", signed=True)
    except (ValueError, TypeError):
        return None


def anon_tag(uid: int, rid: int) -> str:
    """Свой отпечаток у каждого анонимного обращения — их нельзя сгруппировать по одному человеку."""
    return store.sign(f"anontag.{int(uid)}.{int(rid)}")


def author_of(r) -> int | None:
    if not r:
        return None
    if r["user_id"]:
        return int(r["user_id"])
    if r["anon_ref"]:
        return anon_open(r["id"], r["anon_ref"])
    return None


def is_author(r, uid: int) -> bool:
    if not r:
        return False
    if r["user_id"] and int(r["user_id"]) == int(uid):
        return True
    return bool(r["anon_tag"]) and hmac.compare_digest(r["anon_tag"], anon_tag(uid, r["id"]))


# ---------------------------------------------------------------- карта сообщений

def _ckey(chat_id, message_id) -> str:
    """Личные чаты храним хешем от пары «чат + сообщение»: у каждого сообщения свой ключ,
    поэтому по базе нельзя собрать обращения одного человека (и связать именное с анонимным)."""
    s = str(chat_id)
    return s if s.startswith("-") else "u:" + store.sign(f"mm.{s}.{int(message_id)}")[:24]


def map_msg(chat_id, message_id, rid: int, kind: str) -> None:
    store.x("INSERT OR REPLACE INTO msg_map(chat_key, message_id, report_id, kind) VALUES(?,?,?,?)",
            (_ckey(chat_id, message_id), int(message_id), int(rid), kind))


def lookup(chat_id, message_id):
    return store.q("SELECT report_id, kind FROM msg_map WHERE chat_key=? AND message_id=?",
                   (_ckey(chat_id, message_id), int(message_id)), one=True)


def get(rid) -> Any:
    try:
        return store.q("SELECT * FROM reports WHERE id=?", (int(rid),), one=True)
    except (TypeError, ValueError):
        return None


def thread(rid: int) -> list[dict]:
    return [{"author": m["author"], "who": m["who"] if m["author"] == "note" else "", "text": m["text"], "at": m["created_at"]}
            for m in store.q("SELECT * FROM report_msgs WHERE report_id=? ORDER BY id", (int(rid),))]


def _add_msg(rid: int, author: str, who: str, text: str) -> None:
    store.x("INSERT INTO report_msgs(report_id, author, who, text, created_at) VALUES(?,?,?,?,?)",
            (int(rid), author, who, text, int(time.time())))


# ---------------------------------------------------------------- клавиатуры

def group_kb(r):
    rid, st = r["id"], r["status"] or "new"
    rows = []
    if st == "done":
        rows.append([btn("✅ Решено · " + short(r["resolved_by"] or "", 26), "noop")])
        rows.append([btn("↩️ Вернуть в работу", f"rp:reopen:{rid}")])
    elif st == "work":
        rows.append([btn("⏳ " + short(r["taken_by"] or "в работе", 24), "noop"), btn("✅ Решено", f"rp:done:{rid}")])
    else:
        rows.append([btn("✦ Взять в работу", f"rp:take:{rid}"), btn("✅ Решено", f"rp:done:{rid}")])
    if author_of(r):
        rows.append([btn("💬 Ответить автору" + (" (анонимно)" if r["anon"] else ""), f"rp:ask:{rid}")])
    return ikb(rows)


def author_kb(rid: int):
    return ikb([[btn("↩️ Ответить руководству", f"ur:{rid}")], [btn("🗂 Мои обращения", app="#/my")]])


def rate_kb(rid: int):
    return ikb([[btn(f"{i} ⭐", f"rate:{rid}:{i}") for i in range(1, 6)]])


async def refresh_kb(bot, rid: int) -> None:
    r = get(rid)
    if r and r["chat_id"] and r["message_id"]:
        await safe(bot.edit_message_reply_markup(chat_id=r["chat_id"], message_id=r["message_id"], reply_markup=group_kb(r)))


# ---------------------------------------------------------------- создание

async def create(bot, kind: str, user: dict, text: str, *, location: str = "", anon: bool = False,
                 photos: list[bytes] | None = None, photo_ids: list[str] | None = None, source: str = "bot"):
    """Сохраняет обращение и отправляет его в рабочий чат. Возвращает (ok, id, текст ошибки)."""
    if kind not in KINDS:
        return False, None, "Неизвестный тип обращения"
    text = (text or "").strip()[:MAX_TEXT]
    photos = (photos or [])[: config.MAX_PHOTOS]
    photo_ids = (photo_ids or [])[: config.MAX_PHOTOS - len(photos)]
    n_photos = len(photos) + len(photo_ids)
    if not text and not n_photos:
        return False, None, "Добавь описание или фото"
    uid = int(user["id"])
    now = int(time.time())
    if not anon:
        store.touch_user(uid, user.get("first_name", ""), user.get("username", ""))
    name = display_name(user.get("username"), user.get("first_name"), anon)
    rid = store.x("""INSERT INTO reports(kind, created_at, source, user_id, user_name, anon, text, location, photos, status)
                     VALUES(?,?,?,?,?,?,?,?,?,'new')""",
                  (kind, now, source, None if anon else uid, name, 1 if anon else 0, text or "Без описания",
                   (location or "")[:200], n_photos))
    if anon:
        store.x("UPDATE reports SET anon_ref=?, anon_tag=? WHERE id=?", (anon_seal(rid, uid), anon_tag(uid, rid), rid))
    store.hit("report", kind)
    store.hit("report_src", source)
    if not await deliver(bot, rid, photos, photo_ids):
        return False, rid, "Сохранили, но не смогли доставить руководству. Попробуй позже."
    return True, rid, ""


async def deliver(bot, rid: int, photos: list[bytes] | None = None, photo_ids: list[str] | None = None) -> bool:
    r = get(rid)
    media = [BufferedInputFile(raw, filename=f"photo_{i + 1}.jpg") for i, raw in enumerate(photos or [])] + list(photo_ids or [])
    target = dict(chat_id=config.SUPER_CHAT_ID)
    if config.TOPIC_ID:
        target["message_thread_id"] = config.TOPIC_ID
    album_ids: list[int] = []
    file_ids: list[str] = []
    if media:
        sent = await safe(bot.send_media_group(media=[
            InputMediaPhoto(media=m, caption=f"📎 К обращению №{rid}" if i == 0 else None) for i, m in enumerate(media)], **target))
        for m in sent or []:
            album_ids.append(m.message_id)
            if getattr(m, "photo", None):
                file_ids.append(m.photo[-1].file_id)
    kw = dict(target)
    if album_ids:
        kw["reply_parameters"] = {"message_id": album_ids[0], "allow_sending_without_reply": True}
    msg = await send(bot, text=group_text(r), kb=group_kb(r), **kw)
    if msg is None and "message_thread_id" in kw:          # тема удалена — отправим в общий чат
        kw.pop("message_thread_id", None)
        msg = await send(bot, text=group_text(r), kb=group_kb(r), **kw)
    if msg is None:
        return False
    store.x("UPDATE reports SET chat_id=?, message_id=? WHERE id=?", (str(msg.chat.id), msg.message_id, rid))
    map_msg(msg.chat.id, msg.message_id, rid, "report")
    for mid in album_ids:
        map_msg(msg.chat.id, mid, rid, "report")
    # копии админам в личку (если включено) — на них тоже можно отвечать Reply
    if config.NOTIFY_ADMINS_DM:
        r = get(rid)
        for aid in config.ADMIN_IDS:
            if file_ids:
                await safe(bot.send_media_group(chat_id=aid, media=[InputMediaPhoto(media=f) for f in file_ids]))
            m = await send(bot, aid, group_text(r), group_kb(r))
            if m is not None:
                map_msg(aid, m.message_id, rid, "report")
    return True


def ack_text(kind: str, rid: int, anon: bool) -> str:
    k = KINDS[kind]
    extra = (f"\n\n🔖 Номер обращения: <b>№{rid}</b>. Статус и ответы — в «🗂 Мои обращения»."
             + ("\n🕵️ Обращение анонимное: имя не видит никто. Ответ руководства всё равно придёт сюда." if anon else ""))
    return k.user_ack + extra


# ---------------------------------------------------------------- статусы

async def notify_author(bot, r, text: str, kb=None) -> bool:
    uid = author_of(r)
    if not uid:
        return False
    m = await send(bot, uid, text, kb if kb is not None else author_kb(r["id"]))
    if m is not None:
        map_msg(uid, m.message_id, r["id"], "private")
    return m is not None


async def set_status(bot, rid, action: str, actor: str):
    """take / done / reopen — общая логика для кнопок в группе и Пульта в приложении."""
    r = get(rid)
    if not r:
        return None, "Обращение не найдено"
    now = int(time.time())
    st = r["status"] or "new"
    if action == "take":
        if st == "work":
            return r, f"Уже в работе: {r['taken_by'] or ''}"
        if st == "done":
            return r, "Обращение уже решено"
        store.x("UPDATE reports SET status='work', taken_by=?, taken_at=? WHERE id=?", (actor, now, r["id"]))
        _add_msg(r["id"], "note", actor, "взял(а) в работу")
        await notify_author(bot, r, f"✦ <b>{what(r)} взяли в работу.</b>\nЕсли понадобятся детали — руководство напишет сюда.")
    elif action == "done":
        if st == "done":
            return r, "Уже отмечено решённым"
        store.x("""UPDATE reports SET status='done', resolved_by=?, resolved_at=?,
                   taken_by=COALESCE(NULLIF(taken_by,''), ?), taken_at=COALESCE(taken_at, ?) WHERE id=?""",
                (actor, now, actor, now, r["id"]))
        _add_msg(r["id"], "note", actor, "отметил(а) решённым")
        await notify_author(bot, r, f"✅ <b>{what(r)} отмечено решённым.</b>\n\n"
                                    "Помогло? Оцени, пожалуйста, — это займёт секунду 👇", rate_kb(r["id"]))
    elif action == "reopen":
        if st != "done":
            return r, "Обращение и так открыто"
        store.x("UPDATE reports SET status='work', resolved_by=NULL, resolved_at=NULL, rating=NULL, rating_text=NULL, rated_at=NULL WHERE id=?",
                (r["id"],))
        _add_msg(r["id"], "note", actor, "вернул(а) в работу")
    else:
        return r, "Неизвестное действие"
    store.hit("report_action", action)
    await refresh_kb(bot, r["id"])
    return get(r["id"]), ""


# ---------------------------------------------------------------- переписка

async def team_reply(bot, rid, text: str, who: str, *, via_app: bool = False, media_from: tuple | None = None):
    """Ответ руководства автору (и анонимному) — через бота. media_from=(chat_id, message_id) — переслать фото/файл."""
    r = get(rid)
    if not r:
        return False, "Обращение не найдено"
    uid = author_of(r)
    if not uid:
        return False, "Автору нельзя ответить: обращение отправлено без обратной связи."
    text = (text or "").strip()[:3500]
    if not text and not media_from:
        return False, "Пустой ответ"
    k = KINDS.get(r["kind"], KINDS["question"])
    head = f"📩 <b>Ответ руководства ССт НАБ</b> · {k.word} №{r['id']}"
    body = f"{head}\n\n{esc(text)}" if text else head
    m = await send(bot, uid, body + "\n\n<i>Ответить — кнопкой ниже или Reply на это сообщение.</i>", author_kb(r["id"]))
    if m is None:
        return False, "Не удалось доставить: возможно, автор остановил бота."
    map_msg(uid, m.message_id, r["id"], "private")
    if media_from:
        await safe(bot.copy_message(chat_id=uid, from_chat_id=media_from[0], message_id=media_from[1]))
    now = int(time.time())
    _add_msg(r["id"], "team", who, text or "📎 вложение")
    store.x("UPDATE reports SET last_team_at=? WHERE id=?", (now, r["id"]))
    if (r["status"] or "new") == "new":
        store.x("UPDATE reports SET status='work', taken_by=?, taken_at=? WHERE id=?", (who, now, r["id"]))
    await refresh_kb(bot, r["id"])
    if via_app and r["chat_id"] and r["message_id"]:
        kw = {"reply_parameters": {"message_id": int(r["message_id"]), "allow_sending_without_reply": True}}
        if config.TOPIC_ID and str(r["chat_id"]) == str(config.SUPER_CHAT_ID):
            kw["message_thread_id"] = config.TOPIC_ID
        await send(bot, r["chat_id"], f"💬 <b>Ответ автору №{r['id']}</b> (из приложения) · {esc(who)}\n<blockquote>{esc(text)}</blockquote>", **kw)
    store.hit("report_action", "reply")
    return True, ""


async def user_reply(bot, rid, uid: int, text: str):
    """Ответ автора руководству: уходит в рабочий чат ответом на исходное обращение."""
    r = get(rid)
    if not r or not is_author(r, uid):
        return False, "Это обращение не найдено среди твоих."
    text = (text or "").strip()[:3000]
    if not text:
        return False, "Напиши текст ответа."
    if not REPLY_LIMIT.allow(int(uid)):
        return False, "Слишком много сообщений подряд — подожди несколько минут."
    reopened = (r["status"] or "new") == "done"
    k = KINDS.get(r["kind"], KINDS["question"])
    who = "анонимно" if r["anon"] else (r["user_name"] or "автор")
    lines = [f"↩️ <b>Ответ автора</b> · {k.word} №{r['id']} · {esc(who)}", f"<blockquote>{esc(text)}</blockquote>"]
    if reopened:
        lines.append("<i>Обращение было решено — автор ответил, оно снова в работе.</i>")
    kw: dict = {}
    if config.TOPIC_ID and str(r["chat_id"] or config.SUPER_CHAT_ID) == str(config.SUPER_CHAT_ID):
        kw["message_thread_id"] = config.TOPIC_ID
    if r["message_id"]:
        kw["reply_parameters"] = {"message_id": int(r["message_id"]), "allow_sending_without_reply": True}
    kb = ikb([[btn("💬 Ответить автору" + (" (анонимно)" if r["anon"] else ""), f"rp:ask:{r['id']}")]])
    m = await send(bot, r["chat_id"] or config.SUPER_CHAT_ID, "\n".join(lines), kb, **kw)
    if m is None:
        return False, "Не получилось доставить руководству. Попробуй позже."
    map_msg(m.chat.id, m.message_id, r["id"], "report")
    _add_msg(r["id"], "user", "", text)
    if reopened:
        store.x("UPDATE reports SET status='work', resolved_by=NULL, resolved_at=NULL, rating=NULL, rating_text=NULL, rated_at=NULL WHERE id=?",
                (r["id"],))
    await refresh_kb(bot, r["id"])
    return True, ""


async def rate(bot, rid, uid: int, value: int | None = None, review: str | None = None):
    """Оценка 1–5 и/или отзыв словами после «Решено». Итог уходит в рабочий чат."""
    r = get(rid)
    if not r or not is_author(r, uid):
        return False, "Обращение не найдено"
    if (r["status"] or "") != "done":
        return False, "Оценить можно, когда обращение отмечено решённым"
    if value is None and review is None:
        return False, "Поставь оценку или напиши отзыв"
    if value is not None and not (isinstance(value, int) and 1 <= value <= 5):
        return False, "Оценка — от 1 до 5"
    if value is not None and not STAR_LIMIT.allow(int(uid)):
        return False, "Слишком часто — попробуй через пару минут"
    if review is not None and review.strip() and not REVIEW_LIMIT.allow(int(uid)):
        return False, "Слишком много отзывов подряд — попробуй через пару минут"
    now = int(time.time())
    first = r["rating"] is None
    if value is not None:
        store.x("UPDATE reports SET rating=?, rated_at=? WHERE id=?", (value, now, r["id"]))
        store.hit("rating", str(value))
    if review is not None:
        review = review.strip()[:1000]
        store.x("UPDATE reports SET rating_text=?, rated_at=? WHERE id=?", (review or None, now, r["id"]))
    r = get(rid)
    # в рабочий чат — первая оценка и каждый отзыв словами (повторные тапы по звёздам чат не засоряют)
    if r["chat_id"] and r["message_id"] and ((value is not None and first) or review):
        kw = {"reply_parameters": {"message_id": int(r["message_id"]), "allow_sending_without_reply": True}}
        if config.TOPIC_ID and str(r["chat_id"]) == str(config.SUPER_CHAT_ID):
            kw["message_thread_id"] = config.TOPIC_ID
        t = f"⭐ <b>Оценка автора</b> · №{r['id']}: {stars(r['rating'] or 0)} ({r['rating'] or '—'} из 5)"
        if r["rating_text"]:
            t += f"\n<blockquote>{esc(r['rating_text'])}</blockquote>"
        await send(bot, r["chat_id"], t, **kw)
    return True, ""


# ---------------------------------------------------------------- представления

def _status_key(r) -> str:
    return r["status"] or "new"


def public(r, with_thread: bool = True) -> dict:
    """Обращение глазами автора (без имён руководства и служебных полей)."""
    k = KINDS.get(r["kind"], KINDS["question"])
    d = {"id": r["id"], "kind": r["kind"], "kindTitle": k.word, "icon": k.icon, "createdAt": r["created_at"],
         "text": r["text"], "location": r["location"] or "", "anon": bool(r["anon"]), "photos": r["photos"],
         "status": _status_key(r), "statusTitle": STATUS_TITLE.get(_status_key(r), ""),
         "canReply": bool(author_of(r)), "rating": r["rating"], "review": r["rating_text"] or "",
         "unread": bool(r["last_team_at"] and r["last_team_at"] > (r["user_seen_at"] or 0))}
    if with_thread:
        d["messages"] = [m for m in thread(r["id"]) if m["author"] != "note"]
    return d


def admin_view(r) -> dict:
    d = public(r)
    d.update({"from": r["user_name"] or "", "takenBy": r["taken_by"] or "", "takenAt": r["taken_at"],
              "resolvedBy": r["resolved_by"] or "", "resolvedAt": r["resolved_at"], "messages": thread(r["id"]),
              "link": tme_link(r["chat_id"], r["message_id"]), "source": r["source"] or "bot"})
    return d


def mine(uid: int, limit: int = 30) -> list:
    rows = list(store.q("SELECT * FROM reports WHERE user_id=? ORDER BY id DESC LIMIT ?", (int(uid), limit)))
    # анонимные: проверяем отпечаток у каждого (лёгкий запрос только по id и тегу, без лимита по давности)
    ids = [t["id"] for t in store.q("SELECT id, anon_tag FROM reports WHERE anon=1 AND anon_tag IS NOT NULL ORDER BY id DESC")
           if hmac.compare_digest(t["anon_tag"], anon_tag(uid, t["id"]))][:limit]
    if ids:
        rows += store.q(f"SELECT * FROM reports WHERE id IN ({','.join('?' * len(ids))})", ids)
    return sorted(rows, key=lambda r: -r["id"])[:limit]


def mark_seen(uid: int) -> None:
    now = int(time.time())
    for r in mine(uid):
        if r["last_team_at"] and r["last_team_at"] > (r["user_seen_at"] or 0):
            store.x("UPDATE reports SET user_seen_at=? WHERE id=?", (now, r["id"]))


def unread(uid: int) -> int:
    return sum(1 for r in mine(uid) if r["last_team_at"] and r["last_team_at"] > (r["user_seen_at"] or 0))


def admin_list(flt: str = "open", kind: str = "", query: str = "", limit: int = 80) -> list:
    where = {"open": "COALESCE(status,'new')<>'done'", "new": "COALESCE(status,'new')='new'",
             "work": "status='work'", "done": "status='done'"}.get(flt, "1=1")
    args: list = []
    if kind in KINDS:
        where += " AND kind=?"
        args.append(kind)
    if query:
        if query.lstrip("№#").isdigit():
            where += " AND id=?"
            args.append(int(query.lstrip("№#")))
        else:
            where += " AND (text LIKE ? OR location LIKE ? OR user_name LIKE ?)"
            args += [f"%{query}%"] * 3
    order = "created_at ASC" if flt in ("open", "new") else "id DESC"
    return store.q(f"SELECT * FROM reports WHERE {where} ORDER BY {order} LIMIT {int(limit)}", args)


def tme_link(chat_id, message_id) -> str:
    s = str(chat_id or "")
    if not s.startswith("-100") or not message_id:
        return ""
    if config.TOPIC_ID and s == str(config.SUPER_CHAT_ID):
        return f"https://t.me/c/{s[4:]}/{config.TOPIC_ID}/{int(message_id)}"
    return f"https://t.me/c/{s[4:]}/{int(message_id)}"


# ---------------------------------------------------------------- просроченные обращения

def sla_hours() -> int:
    try:
        return max(1, int(store.setting("sla_hours", config.SLA_HOURS)))
    except (TypeError, ValueError):
        return config.SLA_HOURS


def overdue(now: float | None = None) -> list:
    now = now or time.time()
    return store.q("""SELECT * FROM reports WHERE COALESCE(status,'new')='new' AND message_id IS NOT NULL
                      AND created_at <= ? ORDER BY created_at""", (now - sla_hours() * 3600,))


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    a, b = n % 10, n % 100
    return one if a == 1 and b != 11 else (few if 2 <= a <= 4 and not 12 <= b <= 14 else many)


def age_text(seconds: float) -> str:
    h = int(seconds // 3600)
    if h < 48:
        return f"{h} {_plural(h, 'час', 'часа', 'часов')}"
    d = h // 24
    return f"{d} {_plural(d, 'день', 'дня', 'дней')}"


async def sla_tick(bot, now: float | None = None) -> int:
    """Обращение без ответственного дольше N часов — бот напоминает в группе (раз в сутки)."""
    if store.setting("sla_on", "1") != "1":
        return 0
    now = now or time.time()
    sent = 0
    for r in overdue(now):
        if r["sla_at"] and now - r["sla_at"] < 24 * 3600:
            continue
        k = KINDS.get(r["kind"], KINDS["question"])
        kw: dict = {"reply_parameters": {"message_id": int(r["message_id"]), "allow_sending_without_reply": True}}
        if config.TOPIC_ID and str(r["chat_id"]) == str(config.SUPER_CHAT_ID):
            kw["message_thread_id"] = config.TOPIC_ID
        t = (f"⏰ <b>{k.icon} №{r['id']} ждёт уже {age_text(now - r['created_at'])}</b> — никто не взял в работу.\n"
             f"<i>{esc(short(r['text'], 120))}</i>")
        kb = ikb([[btn("✦ Взять в работу", f"rp:take:{r['id']}")]] +
                 ([[btn("💬 Ответить автору", f"rp:ask:{r['id']}")]] if author_of(r) else []))
        m = await send(bot, r["chat_id"], t, kb, **kw)
        if m is not None:
            map_msg(m.chat.id, m.message_id, r["id"], "report")
            store.x("UPDATE reports SET sla_at=? WHERE id=?", (int(now), r["id"]))
            sent += 1
    return sent


# ---------------------------------------------------------------- сводка

def digest_text(days: int = 7) -> str:
    now = time.time()
    since = now - days * 86400
    by_kind = {r["kind"]: r["c"] for r in store.q("SELECT kind, COUNT(*) c FROM reports WHERE created_at>=? GROUP BY kind", (since,))}
    total = sum(by_kind.values())
    resolved = store.count("SELECT COUNT(*) FROM reports WHERE status='done' AND resolved_at>=?", (since,))
    open_rows = store.q("SELECT * FROM reports WHERE COALESCE(status,'new')<>'done' ORDER BY created_at")
    unowned = [r for r in open_rows if (r["status"] or "new") == "new"]
    avg = store.q("SELECT AVG(rating) a, COUNT(rating) c FROM reports WHERE rated_at>=?", (since,), one=True)
    kinds = " · ".join(f"{KINDS[k].icon} {by_kind[k]}" for k in KINDS if by_kind.get(k))
    lines = [f"📊 <b>Сводка обращений за {days} дней</b>", "",
             f"Новых: <b>{total}</b>" + (f"  ({kinds})" if kinds else ""),
             f"Решено: <b>{resolved}</b>",
             f"Сейчас открыто: <b>{len(open_rows)}</b>" + (f" — без ответственного: <b>{len(unowned)}</b>" if unowned else "")]
    if avg and avg["c"]:
        lines.append(f"Средняя оценка: <b>{avg['a']:.1f}</b> ⭐ ({avg['c']} {_plural(avg['c'], 'отзыв', 'отзыва', 'отзывов')})")
    if open_rows:
        lines += ["", "<b>Открытые</b> (сначала самые старые):"]
        for r in open_rows[:15]:
            k = KINDS.get(r["kind"], KINDS["question"])
            link = tme_link(r["chat_id"], r["message_id"])
            num = f'<a href="{link}">№{r["id"]}</a>' if link else f"№{r['id']}"
            who = "без ответственного" if (r["status"] or "new") == "new" else "в работе · " + esc(short(r["taken_by"] or "", 24))
            lines.append(f"{k.icon} {num} · {age_text(now - r['created_at'])} · {who}\n   <i>{esc(short(r['text'], 70))}</i>")
        if len(open_rows) > 15:
            lines.append(f"…и ещё {len(open_rows) - 15}")
    else:
        lines += ["", "Всё закрыто — открытых обращений нет ✦"]
    return "\n".join(lines)


async def send_digest(bot) -> bool:
    kw = {"message_thread_id": config.TOPIC_ID} if config.TOPIC_ID else {}
    return await send(bot, config.SUPER_CHAT_ID, digest_text(), **kw) is not None

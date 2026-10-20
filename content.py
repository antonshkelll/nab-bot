"""
Всё, что руководство меняет через Пульт без правки кода:
новости, анонсы (с явкой «приду / не приду» и напоминаниями), опросы,
важные группы, контакты и документы НПБ.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
from pathlib import Path

import config
import store
from tgutil import btn, esc, ikb, safe, send, short

log = logging.getLogger("sprosinab.content")
MSK = dt.timezone(dt.timedelta(hours=config.TZ_OFFSET))
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_S = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def now_msk() -> dt.datetime:
    return dt.datetime.now(MSK)


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    a, b = n % 10, n % 100
    return one if a == 1 and b != 11 else (few if 2 <= a <= 4 and not 12 <= b <= 14 else many)


def _url(u: str) -> str:
    u = (u or "").strip()[:500]
    if not u or u.startswith("#"):
        return u
    if u.startswith("@"):
        return "https://t.me/" + u[1:]
    if not re.match(r"^(https?|tg)://", u):
        u = "https://" + u
    return u


# ================================================================ новости

def news_view(n) -> dict:
    t = time.localtime(n["created_at"])
    return {"id": n["id"], "tag": n["tag"] or "", "title": n["title"], "body": n["body"] or "",
            "image": f"api/media/{n['image']}" if n["image"] else "", "btnText": n["btn_text"] or "",
            "btnUrl": n["btn_url"] or "", "pinned": bool(n["pinned"]), "hidden": bool(n["hidden"]),
            "date": f"{t.tm_mday} {MONTHS[t.tm_mon - 1]}", "createdAt": n["created_at"], "sent": n["sent"]}


def news_list(include_hidden: bool = False, limit: int = 30) -> list[dict]:
    where = "" if include_hidden else "WHERE hidden=0"
    return [news_view(n) for n in store.q(f"SELECT * FROM news {where} ORDER BY pinned DESC, id DESC LIMIT {int(limit)}")]


def news_save(b: dict, author: str) -> tuple[int | None, str]:
    title = str(b.get("title") or "").strip()[:140]
    body = str(b.get("body") or "").strip()[:4000]
    if len(title) < 2:
        return None, "Добавь заголовок"
    tag = str(b.get("tag") or "").strip()[:30]
    btn_text = str(b.get("btnText") or "").strip()[:40]
    btn_url = _url(str(b.get("btnUrl") or ""))
    if btn_text and not btn_url:
        return None, "Для кнопки нужна ссылка"
    image = str(b.get("image") or "").strip()
    if image and not re.match(r"^[a-f0-9]{16}\.(jpg|png|webp)$", image):
        image = ""
    nid = b.get("id")
    if nid not in (None, "", 0):
        try:
            nid = int(nid)
        except (TypeError, ValueError):
            return None, "Новость не найдена"
    vals = (tag, title, body, image or None, btn_text, btn_url if btn_text else "", 1 if b.get("pinned") else 0, 1 if b.get("hidden") else 0)
    if nid:
        if not store.q("SELECT 1 FROM news WHERE id=?", (int(nid),), one=True):
            return None, "Новость не найдена"
        store.x("UPDATE news SET tag=?, title=?, body=?, image=?, btn_text=?, btn_url=?, pinned=?, hidden=? WHERE id=?", vals + (int(nid),))
        return int(nid), ""
    nid = store.x("INSERT INTO news(tag,title,body,image,btn_text,btn_url,pinned,hidden,created_at,author) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  vals + (int(time.time()), author))
    return nid, ""


def news_delete(nid: int) -> None:
    n = store.q("SELECT image FROM news WHERE id=?", (int(nid),), one=True)
    store.x("DELETE FROM news WHERE id=?", (int(nid),))
    if n and n["image"]:
        try:
            (config.MEDIA_DIR / n["image"]).unlink()
        except OSError:
            pass


def _u16(s: str) -> int:
    """Telegram считает длину в UTF-16: эмодзи — это 2 символа."""
    return len(s.encode("utf-16-le")) // 2


def news_text(n: dict) -> str:
    head = f"📰 {n['title']}"                               # видимый текст — теги <b> в лимит не входят
    t = f"📰 <b>{esc(n['title'])}</b>"
    if n["body"]:
        limit = 1000 if n["image"] else 4000                 # подпись к фото — до 1024, сообщение — до 4096
        body = "\n".join(" ".join(ln.split()) for ln in str(n["body"]).strip().splitlines())   # абзацы сохраняем
        while _u16(head) + 2 + _u16(body) > limit and len(body) > 50:
            cut = len(body) - max(20, (_u16(head) + 2 + _u16(body) - limit + 1) // 2 + 1)
            body = body[:max(49, cut)].rstrip() + "…"
        t += "\n\n" + esc(body)
    return t


def news_kb(n: dict):
    rows = []
    if n["btnText"] and n["btnUrl"]:
        if n["btnUrl"].startswith("#"):
            rows.append([btn(n["btnText"], app=n["btnUrl"])])
        else:
            rows.append([btn(n["btnText"], url=n["btnUrl"])])
    rows.append([btn("✨ Открыть в приложении", app=f"#/news/{n['id']}")])
    return ikb(rows)


async def news_broadcast(bot, nid: int) -> tuple[int, int]:
    from tgutil import broadcast
    from aiogram.types import FSInputFile
    row = store.q("SELECT * FROM news WHERE id=?", (int(nid),), one=True)
    if not row:
        return 0, 0
    n = news_view(row)
    photo = FSInputFile(config.MEDIA_DIR / row["image"]) if row["image"] and (config.MEDIA_DIR / row["image"]).exists() else None
    ok, total = await broadcast(bot, store.all_user_ids(), news_text(n), news_kb(n), photo=photo)
    store.x("UPDATE news SET sent=? WHERE id=?", (ok, int(nid)))
    return ok, total


def save_media(raw: bytes) -> str | None:
    """Картинка новости (уже сжатая в приложении). Возвращает имя файла."""
    import hashlib
    if raw[:3] == b"\xff\xd8\xff" and raw.rstrip(b"\x00").endswith(b"\xff\xd9"):
        ext = "jpg"
    elif raw[:8] == b"\x89PNG\r\n\x1a\n" and b"IEND" in raw[-16:]:
        ext = "png"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP" and int.from_bytes(raw[4:8], "little") + 8 == len(raw):
        ext = "webp"
    else:
        return None
    if b"<script" in raw.lower() or b"<html" in raw.lower():
        return None
    name = hashlib.sha256(raw).hexdigest()[:16] + "." + ext
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    (config.MEDIA_DIR / name).write_bytes(raw)
    return name


# ================================================================ анонсы

EVENT_KINDS = {"event": "Событие", "meeting": "Собрание", "deadline": "Дедлайн", "selection": "Отбор", "trip": "Выезд"}


def event_dt(e) -> dt.datetime:
    d = dt.date.fromisoformat(e["date"])
    if e["time"]:
        h, m = (int(v) for v in e["time"].split(":"))
        return dt.datetime(d.year, d.month, d.day, h, m, tzinfo=MSK)
    return dt.datetime(d.year, d.month, d.day, 23, 59, tzinfo=MSK)


def rsvp_counts(eid: int) -> dict:
    c = {r["answer"]: r["c"] for r in store.q("SELECT answer, COUNT(*) c FROM event_rsvp WHERE event_id=? GROUP BY answer", (eid,))}
    return {"yes": c.get("yes", 0), "no": c.get("no", 0)}


def event_view(e, uid: int | None = None, today: dt.date | None = None) -> dict:
    today = today or now_msk().date()
    d = dt.date.fromisoformat(e["date"])
    days = (d - today).days
    tm = e["time"] or ""
    when = ("прошло" if days < 0 else ("сегодня" if days == 0 else "завтра" if days == 1 else
            f"через {days} {plural(days, 'день', 'дня', 'дней')}"))
    if 0 <= days <= 1 and tm:
        when += f" в {tm}"
    v = {"id": e["id"], "title": e["title"], "descr": e["descr"] or "", "place": e["place"] or "", "url": e["url"] or "",
         "kind": e["kind"] or "event", "kindTitle": EVENT_KINDS.get(e["kind"] or "event", "Событие"),
         "date": e["date"], "time": tm, "day": d.day, "mon": MONTHS_S[d.month - 1], "wday": WDAYS[d.weekday()],
         "dateLabel": f"{d.day} {MONTHS[d.month - 1]}, {WDAYS[d.weekday()]}" + (f" · {tm}" if tm else ""),
         "days": days, "when": when, "rsvp": bool(e["rsvp"]), "notifyAll": bool(e["notify_all"]),
         "hidden": bool(e["hidden"]), "counts": rsvp_counts(e["id"])}
    if uid:
        a = store.q("SELECT answer FROM event_rsvp WHERE event_id=? AND uid=?", (e["id"], int(uid)), one=True)
        v["my"] = a["answer"] if a else ""
        v["subscribed"] = bool(store.q("SELECT 1 FROM event_subs WHERE event_id=? AND uid=?", (e["id"], int(uid)), one=True))
    return v


def events_list(uid: int | None = None, past_days: int = 1, include_hidden: bool = False) -> list[dict]:
    today = now_msk().date()
    out = []
    for e in store.q("SELECT * FROM events ORDER BY date, COALESCE(time,'')"):
        if e["hidden"] and not include_hidden:
            continue
        try:
            v = event_view(e, uid, today)
        except (ValueError, TypeError):
            continue
        if v["days"] < -past_days:
            continue
        out.append(v)
    return out


def event_parse(b: dict) -> tuple[dict | None, str]:
    title = str(b.get("title") or "").strip()[:120]
    date = str(b.get("date") or "").strip()
    tm = str(b.get("time") or "").strip()
    if len(title) < 2:
        return None, "Название — минимум 2 символа"
    try:
        date = dt.date.fromisoformat(date).isoformat()
    except ValueError:
        return None, "Выбери дату"
    if tm and not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", tm):
        return None, "Время в формате ЧЧ:ММ"
    kind = b.get("kind") if b.get("kind") in EVENT_KINDS else "event"
    return {"title": title, "date": date, "time": tm, "descr": str(b.get("descr") or "").strip()[:1500],
            "place": str(b.get("place") or "").strip()[:120], "url": _url(str(b.get("url") or "")), "kind": kind,
            "rsvp": 1 if b.get("rsvp", True) else 0, "notify_all": 1 if b.get("notifyAll") else 0,
            "hidden": 1 if b.get("hidden") else 0}, ""


def event_save(b: dict, uid: int) -> tuple[int | None, str]:
    ev, err = event_parse(b)
    if not ev:
        return None, err
    eid = b.get("id")
    if eid not in (None, "", 0):
        try:
            eid = int(eid)
        except (TypeError, ValueError):
            return None, "Анонс не найден"
    cols = ("title", "descr", "place", "url", "kind", "date", "time", "rsvp", "notify_all", "hidden")
    if eid:
        old = store.q("SELECT * FROM events WHERE id=?", (int(eid),), one=True)
        if not old:
            return None, "Анонс не найден"
        store.x("UPDATE events SET " + ", ".join(f"{c}=?" for c in cols) + " WHERE id=?", tuple(ev[c] for c in cols) + (int(eid),))
        if old["date"] != ev["date"] or (old["time"] or "") != ev["time"]:
            store.x("DELETE FROM event_sent WHERE event_id=?", (int(eid),))      # дата сдвинулась — напомним заново
        return int(eid), ""
    eid = store.x(f"INSERT INTO events({', '.join(cols)}, created_at, created_by) VALUES({', '.join('?' * len(cols))},?,?)",
                  tuple(ev[c] for c in cols) + (int(time.time()), uid))
    return eid, ""


def event_delete(eid: int) -> None:
    for t in ("events WHERE id", "event_subs WHERE event_id", "event_sent WHERE event_id", "event_rsvp WHERE event_id"):
        store.x(f"DELETE FROM {t}=?", (int(eid),))


def set_rsvp(eid: int, uid: int, answer: str, name: str = "") -> tuple[bool, str]:
    e = store.q("SELECT * FROM events WHERE id=?", (int(eid),), one=True)
    if not e:
        return False, "Анонс уже удалён"
    if not e["rsvp"]:
        return False, "Для этого анонса отметка не нужна"
    if e["hidden"]:
        return False, "Анонс скрыт"
    try:
        if event_dt(e) < now_msk():
            return False, "Событие уже прошло"
    except (ValueError, TypeError):
        return False, "Анонс с ошибкой в дате"
    if answer not in ("yes", "no", ""):
        return False, "Неизвестный ответ"
    if not answer:
        store.x("DELETE FROM event_rsvp WHERE event_id=? AND uid=?", (int(eid), int(uid)))
    else:
        store.x("""INSERT INTO event_rsvp(event_id, uid, answer, name, at) VALUES(?,?,?,?,?)
                   ON CONFLICT(event_id, uid) DO UPDATE SET answer=excluded.answer, name=excluded.name, at=excluded.at""",
                (int(eid), int(uid), answer, name[:80], int(time.time())))
    if answer == "yes":           # идущим — напоминание автоматически
        store.x("INSERT OR IGNORE INTO event_subs(event_id, uid) VALUES(?,?)", (int(eid), int(uid)))
    else:                         # передумал — не напоминаем
        store.x("DELETE FROM event_subs WHERE event_id=? AND uid=?", (int(eid), int(uid)))
    store.hit("rsvp", answer or "clear")
    return True, ""


def set_sub(eid: int, uid: int, on: bool) -> tuple[bool, str]:
    if not store.q("SELECT 1 FROM events WHERE id=?", (int(eid),), one=True):
        return False, "Анонс уже удалён"
    if on:
        store.x("INSERT OR IGNORE INTO event_subs(event_id, uid) VALUES(?,?)", (int(eid), int(uid)))
    else:
        store.x("DELETE FROM event_subs WHERE event_id=? AND uid=?", (int(eid), int(uid)))
    return True, ""


def attendees(eid: int) -> dict:
    rows = store.q("SELECT uid, answer, name, at FROM event_rsvp WHERE event_id=? ORDER BY at", (int(eid),))
    out = {"yes": [], "no": []}
    for r in rows:
        out.setdefault(r["answer"], []).append(r["name"] or store.user_name(r["uid"]))
    return out


def event_text(e, head: str) -> str:
    v = event_view(e)
    t = [f"{head} <b>{esc(e['title'])}</b>", f"📅 {v['dateLabel']} · <i>{v['when']}</i>"]
    if e["place"]:
        t.append(f"📍 {esc(e['place'])}")
    if e["descr"]:
        t += ["", esc(short(e["descr"], 900))]
    if e["rsvp"]:
        c = v["counts"]
        t += ["", f"Идут: <b>{c['yes']}</b>"]
    return "\n".join(t)


def event_kb(e, uid: int | None = None, with_rsvp: bool = True):
    rows = []
    if e["rsvp"] and with_rsvp:
        my = ""
        if uid:
            a = store.q("SELECT answer FROM event_rsvp WHERE event_id=? AND uid=?", (e["id"], int(uid)), one=True)
            my = a["answer"] if a else ""
        rows.append([btn(("✅ " if my == "yes" else "") + "Приду", f"ev:yes:{e['id']}"),
                     btn(("❌ " if my == "no" else "") + "Не приду", f"ev:no:{e['id']}")])
    elif not e["notify_all"]:
        on = uid and store.q("SELECT 1 FROM event_subs WHERE event_id=? AND uid=?", (e["id"], int(uid)), one=True)
        rows.append([btn("🔕 Не напоминать" if on else "🔔 Напомнить мне", f"ev:sub:{e['id']}")])
    if e["url"]:
        rows.append([btn("Ссылка ↗", url=e["url"])])
    rows.append([btn("📅 Все анонсы", app="#/events")])
    return ikb(rows)


async def announce(bot, eid: int) -> tuple[int, int]:
    e = store.q("SELECT * FROM events WHERE id=?", (int(eid),), one=True)
    if not e:
        return 0, 0
    ok = 0
    uids = store.all_user_ids()
    import asyncio
    for uid in uids:
        if await send(bot, uid, event_text(e, "📅 <b>Новый анонс</b>\n"), event_kb(e, uid)) is not None:
            ok += 1
        await asyncio.sleep(0.05)
    return ok, len(uids)


def _recipients(e) -> list[int]:
    if e["notify_all"]:
        return store.all_user_ids()
    ids = {r["uid"] for r in store.q("SELECT uid FROM event_subs WHERE event_id=?", (e["id"],))}
    ids |= {r["uid"] for r in store.q("SELECT uid FROM event_rsvp WHERE event_id=? AND answer='yes'", (e["id"],))}
    return sorted(ids)


async def event_tick(bot, now: dt.datetime | None = None) -> int:
    """Напоминания: за сутки и за 2 часа (или в 9:00 в день события без времени)."""
    now = now or now_msk()
    sent = 0
    for e in store.q("SELECT * FROM events WHERE hidden=0 AND date>=? AND date<=?",
                     ((now.date() - dt.timedelta(days=1)).isoformat(), (now.date() + dt.timedelta(days=2)).isoformat())):
        try:
            at = event_dt(e)
        except (ValueError, TypeError):
            continue
        if now >= at:
            continue
        d = at.date()
        if e["time"]:
            stages = [("d1", at - dt.timedelta(hours=24)), ("d0", at - dt.timedelta(hours=2))]
        else:
            stages = [("d1", dt.datetime(d.year, d.month, d.day, 12, 0, tzinfo=MSK) - dt.timedelta(days=1)),
                      ("d0", dt.datetime(d.year, d.month, d.day, 9, 0, tzinfo=MSK))]
        due = [st for st, t in stages if now >= t]
        if not due:
            continue
        stage = due[-1]                  # после простоя не шлём два напоминания подряд
        if at.date() != now.date():
            head = "⏰ <b>Напоминание на завтра:</b>"
        elif e["time"]:
            head = "⏰ <b>Уже скоро:</b>" if at - now <= dt.timedelta(hours=3) else "🔔 <b>Сегодня:</b>"
        else:
            head = "🔔 <b>Сегодня последний день:</b>" if e["kind"] == "deadline" else "🔔 <b>Сегодня:</b>"
        import asyncio
        for uid in _recipients(e):
            if not store.changed("INSERT OR IGNORE INTO event_sent(event_id, uid, stage) VALUES(?,?,?)", (e["id"], uid, stage)):
                continue
            if stage == "d0":
                store.x("INSERT OR IGNORE INTO event_sent(event_id, uid, stage) VALUES(?,?,?)", (e["id"], uid, "d1"))
            if await send(bot, uid, event_text(e, head + "\n"), event_kb(e, uid)) is not None:
                sent += 1
            await asyncio.sleep(0.05)
    return sent


# ================================================================ опросы

def poll_view(p, uid: int | None = None, admin: bool = False) -> dict:
    opts = json.loads(p["options"] or "[]")
    votes = [0] * len(opts)
    for r in store.q("SELECT opt, COUNT(*) c FROM poll_votes WHERE poll_id=? GROUP BY opt", (p["id"],)):
        if 0 <= r["opt"] < len(votes):
            votes[r["opt"]] = r["c"]
    voters = store.count("SELECT COUNT(DISTINCT uid) FROM poll_votes WHERE poll_id=?", (p["id"],))
    mine: list[int] = []
    if uid:
        mine = [r["opt"] for r in store.q("SELECT opt FROM poll_votes WHERE poll_id=? AND uid=?", (p["id"], int(uid)))]
    closed = bool(p["closed"]) or bool(p["ends_at"] and p["ends_at"] < time.time())
    show = admin or bool(mine) or closed
    return {"id": p["id"], "question": p["question"], "options": opts, "multi": bool(p["multi"]), "closed": closed,
            "hidden": bool(p["hidden"]), "voters": voters, "mine": mine,
            "votes": votes if show else None, "createdAt": p["created_at"]}


def polls_list(uid: int | None = None, admin: bool = False) -> list[dict]:
    where = "" if admin else "WHERE hidden=0"
    return [poll_view(p, uid, admin) for p in store.q(f"SELECT * FROM polls {where} ORDER BY closed, id DESC LIMIT 30")]


def poll_save(b: dict, uid: int) -> tuple[int | None, str]:
    qn = str(b.get("question") or "").strip()[:200]
    raw = b.get("options")
    if not isinstance(raw, list):
        return None, "Варианты передаются списком"
    opts = [str(o).strip()[:80] for o in raw if str(o).strip()]
    if len({o.lower() for o in opts}) != len(opts):
        return None, "Варианты не должны повторяться"
    if len(qn) < 3:
        return None, "Напиши вопрос"
    if not 2 <= len(opts) <= 8:
        return None, "Нужно от 2 до 8 вариантов"
    pid = b.get("id")
    if pid:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return None, "Опрос не найден"
        p = store.q("SELECT * FROM polls WHERE id=?", (int(pid),), one=True)
        if not p:
            return None, "Опрос не найден"
        if store.count("SELECT COUNT(*) FROM poll_votes WHERE poll_id=?", (int(pid),)) and json.loads(p["options"]) != opts:
            return None, "Варианты нельзя менять после первых голосов — создай новый опрос"
        if store.count("SELECT COUNT(*) FROM poll_votes WHERE poll_id=?", (int(pid),)) and \
                bool(p["multi"]) != bool(b.get("multi")):
            return None, "Тип опроса (один/несколько ответов) нельзя менять после первых голосов"
        store.x("UPDATE polls SET question=?, options=?, multi=?, hidden=? WHERE id=?",
                (qn, json.dumps(opts, ensure_ascii=False), 1 if b.get("multi") else 0, 1 if b.get("hidden") else 0, int(pid)))
        return int(pid), ""
    pid = store.x("INSERT INTO polls(question, options, multi, created_at, created_by) VALUES(?,?,?,?,?)",
                  (qn, json.dumps(opts, ensure_ascii=False), 1 if b.get("multi") else 0, int(time.time()), uid))
    return pid, ""


def poll_vote(pid: int, uid: int, opts: list[int]) -> tuple[dict | None, str]:
    p = store.q("SELECT * FROM polls WHERE id=?", (int(pid),), one=True)
    if not p or p["hidden"]:
        return None, "Опрос не найден"
    v = poll_view(p)
    if v["closed"]:
        return None, "Опрос уже закрыт"
    clean: list[int] = []
    for o in opts:
        try:
            o = int(o)
        except (TypeError, ValueError):
            continue
        if 0 <= o < len(v["options"]) and o not in clean:
            clean.append(o)
    if not p["multi"]:
        clean = clean[:1]
    if not clean and not p["multi"]:
        return None, "Выбери вариант"
    opts = clean
    store.x("DELETE FROM poll_votes WHERE poll_id=? AND uid=?", (int(pid), int(uid)))
    for o in opts:
        store.x("INSERT INTO poll_votes(poll_id, uid, opt, at) VALUES(?,?,?,?)", (int(pid), int(uid), o, int(time.time())))
    store.hit("poll_vote", str(pid))
    return poll_view(store.q("SELECT * FROM polls WHERE id=?", (int(pid),), one=True), uid), ""


def poll_bars(v: dict) -> str:
    total = max(1, sum(v["votes"] or []))
    lines = []
    for i, o in enumerate(v["options"]):
        c = (v["votes"] or [0] * len(v["options"]))[i]
        pct = round(c * 100 / total)
        bar = "▰" * round(pct / 10) + "▱" * (10 - round(pct / 10))
        mark = "✅ " if i in v["mine"] else ""
        lines.append(f"{mark}{esc(o)}\n{bar} {pct}% ({c})")
    return "\n".join(lines)


def poll_text(v: dict) -> str:
    t = f"📊 <b>Опрос ССт НАБ</b>\n\n<b>{esc(v['question'])}</b>"
    if v["votes"] is not None and v["mine"]:
        t += "\n\n" + poll_bars(v) + f"\n\nПроголосовали: {v['voters']}"
    elif v["closed"]:
        t += "\n\n<i>Опрос закрыт.</i>\n\n" + poll_bars(v)
    else:
        t += "\n\n<i>Выбери вариант — результаты появятся после голоса. Опрос анонимный.</i>"
    return t


def poll_kb(v: dict):
    if v["closed"]:
        return ikb([[btn("📊 Опросы в приложении", app="#/polls")]])
    rows = [[btn(("✅ " if i in v["mine"] else "") + short(o, 40), f"pv:{v['id']}:{i}")] for i, o in enumerate(v["options"])]
    rows.append([btn("📊 Открыть в приложении", app="#/polls")])
    return ikb(rows)


async def poll_broadcast(bot, pid: int) -> tuple[int, int]:
    import asyncio
    p = store.q("SELECT * FROM polls WHERE id=?", (int(pid),), one=True)
    if not p:
        return 0, 0
    ok = 0
    uids = store.all_user_ids()
    for uid in uids:
        v = poll_view(p, uid)
        if await send(bot, uid, poll_text(v), poll_kb(v)) is not None:
            ok += 1
        await asyncio.sleep(0.05)
    return ok, len(uids)


# ================================================================ группы, контакты, документы

DEFAULT_LINKS = [
    {"id": "nablabel", "t": "ТГК NAB LABEL", "k": "Telegram · главный", "s": "Официальный канал Студсовета НАБ", "u": "https://t.me/NABLABFU"},
    {"id": "magfnab", "t": "ТГК Финунивер | НАБ", "k": "Telegram · факультет", "s": "Новости и объявления факультета", "u": "https://t.me/magfnab"},
    {"id": "vktax", "t": "ВК НАБ | Налоги, аудит", "k": "ВКонтакте", "s": "Сообщество факультета", "u": "https://vk.ru/taxaudit"},
    {"id": "vkfa", "t": "ВК Финунивер | НАБ", "k": "ВКонтакте · официально", "s": "Официальная группа ВКонтакте", "u": "https://vk.ru/timetotaxesfa"},
]
DEFAULT_PEOPLE = [
    {"id": "p1", "ini": "АЗ", "role": "Председатель Студенческого совета", "name": "Алина Зотова", "tel": "+7 (937) 666-11-84", "tg": "jannalli", "note": "пиши — ответит"},
    {"id": "p2", "ini": "ММ", "role": "Заместитель председателя по учебно-социальной деятельности", "name": "Мусо Мирзоев", "tel": "+7 (912) 029-37-35", "tg": "YotaHiro2", "note": "учёба и соцвопросы"},
]
DOC_SUBTITLES = {"1": "Главный документ вуза", "2": "Права, обязанности, дисциплина", "3": "Зачёты, экзамены, пересдачи",
                 "4": "Проживание и порядок", "5": "Перевод на бюджет", "6": "Требования и сроки", "7": "Роль и полномочия",
                 "8": "Скидки на обучение", "9": "Как устроен ССт"}


def links() -> list[dict]:
    v = store.setting_json("links", None)
    return [dict(x) for x in DEFAULT_LINKS] if v is None else v


def people() -> list[dict]:
    v = store.setting_json("people", None)
    return [dict(x) for x in DEFAULT_PEOPLE] if v is None else v


def _initials(name: str) -> str:
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    return ("".join(p[0] for p in parts[:2]) or "?").upper()


def links_save(items: list) -> tuple[bool, str]:
    out = []
    for i, it in enumerate(items or []):
        t = str(it.get("t") or "").strip()[:60]
        u = _url(str(it.get("u") or ""))
        if not t or not u:
            return False, f"Строка {i + 1}: нужны название и ссылка"
        out.append({"id": str(it.get("id") or f"l{int(time.time() * 1000) % 10**8}{i}")[:20], "t": t,
                    "k": str(it.get("k") or "").strip()[:40], "s": str(it.get("s") or "").strip()[:120], "u": u})
    if len(out) > 20:
        return False, "Не больше 20 ссылок"
    store.set_setting_json("links", out)
    return True, ""


def people_save(items: list) -> tuple[bool, str]:
    out = []
    for i, it in enumerate(items or []):
        name = str(it.get("name") or "").strip()[:60]
        if not name:
            return False, f"Контакт {i + 1}: нужно имя"
        tg = str(it.get("tg") or "").strip().lstrip("@").replace("https://t.me/", "")[:40]
        out.append({"id": str(it.get("id") or f"p{int(time.time() * 1000) % 10**8}{i}")[:20], "name": name,
                    "ini": _initials(name), "role": str(it.get("role") or "").strip()[:100],
                    "tel": str(it.get("tel") or "").strip()[:30], "tg": tg, "note": str(it.get("note") or "").strip()[:40]})
    if len(out) > 12:
        return False, "Не больше 12 контактов"
    store.set_setting_json("people", out)
    return True, ""


def _doc_file(num: str) -> Path | None:
    num = str(num or "")
    if not num.isdigit() or len(num) > 4:        # только номер документа — никаких путей и шаблонов
        return None
    for ext in ("pdf", "docx", "doc"):
        for p in sorted(config.DOCS_DIR.glob(f"{num}.*.{ext}")) + sorted(config.DOCS_DIR.glob(f"{num} *.{ext}")):
            return p
    return None


def docs(include_hidden: bool = False) -> list[dict]:
    """Документы НПБ: стандартные 1–9 из config.py + загруженные через Пульт."""
    meta = store.setting_json("docs_meta", {}) or {}
    ids = list(config.NPB_TITLES.keys())
    extra = sorted({p.name.split(".")[0] for p in config.DOCS_DIR.glob("*.pdf") if p.name.split(".")[0].isdigit()}
                   - set(ids), key=int) if config.DOCS_DIR.exists() else []
    out = []
    for i in ids + extra:
        m = meta.get(i, {})
        f = _doc_file(i)
        title = m.get("title") or config.NPB_TITLES.get(i) or (re.sub(r"^\d+[.\s]+", "", f.stem) if f else f"Документ {i}")
        d = {"id": i, "title": title, "sub": m.get("sub", DOC_SUBTITLES.get(i, "")), "hidden": bool(m.get("hidden")),
             "file": f.name if f else "", "size": f.stat().st_size if f else 0, "custom": i not in config.NPB_TITLES}
        if d["hidden"] and not include_hidden:
            continue
        out.append(d)
    return out


def doc_files(doc_id: str, include_hidden: bool = False) -> list[Path]:
    doc_id = str(doc_id or "")
    if doc_id not in {d["id"] for d in docs(include_hidden)}:
        return []
    if doc_id in config.NPB_FILES:
        return [config.BASE_DIR / rel for rel in config.NPB_FILES[doc_id] if (config.BASE_DIR / rel).exists()] or \
               ([_doc_file(doc_id)] if _doc_file(doc_id) else [])
    f = _doc_file(doc_id)
    return [f] if f else []


def doc_meta_save(doc_id: str, title: str | None = None, sub: str | None = None, hidden: bool | None = None) -> None:
    meta = store.setting_json("docs_meta", {}) or {}
    m = meta.get(doc_id, {})
    if title is not None:
        m["title"] = title.strip()[:120]
    if sub is not None:
        m["sub"] = sub.strip()[:80]
    if hidden is not None:
        m["hidden"] = bool(hidden)
    meta[doc_id] = m
    store.set_setting_json("docs_meta", meta)
    _write_registry()


def doc_upload(raw: bytes, title: str, sub: str = "") -> tuple[str | None, str]:
    if not raw.startswith(b"%PDF"):
        return None, "Это не PDF"
    title = " ".join(str(title or "").split())[:100] or "Документ"
    fname = re.sub(r'[\\/:*?"<>|.]+', " ", title).strip()[:80] or "Документ"
    nums = [int(d["id"]) for d in docs(True) if d["id"].isdigit()]
    new_id = str(max(nums + [9]) + 1)
    config.DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (config.DOCS_DIR / f"{new_id}. {fname}.pdf").write_bytes(raw)
    doc_meta_save(new_id, title=title, sub=sub)
    return new_id, ""


def doc_delete(doc_id: str) -> tuple[bool, str]:
    doc_id = str(doc_id or "")
    if doc_id not in {d["id"] for d in docs(True)}:
        return False, "Документ не найден"
    if doc_id in config.NPB_TITLES:          # стандартные документы только прячем
        doc_meta_save(doc_id, hidden=True)
        return True, ""
    f = _doc_file(doc_id)
    if f:
        try:
            f.unlink()
        except OSError:
            return False, "Не удалось удалить файл"
    meta = store.setting_json("docs_meta", {}) or {}
    meta.pop(doc_id, None)
    store.set_setting_json("docs_meta", meta)
    _write_registry()
    return True, ""


def _write_registry() -> None:
    """data/docs.json читает tools/build_docs.py — чтобы новые документы тоже листались в приложении."""
    try:
        reg = [{"id": d["id"], "title": d["title"], "sub": d["sub"]} for d in docs(True)]
        (config.DATA_DIR / "docs.json").write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass

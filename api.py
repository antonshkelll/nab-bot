"""
API Mini App: все обработчики без привязки к веб-фреймворку.

Каждый обработчик получает (ctx, user, body, files) и возвращает (код, словарь).
webapi.py подключает их к aiohttp, а тесты — к своему маленькому серверу.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import config
import content
import reports
import stats
import store
from delivery import KINDS
from security import check_init_data

log = logging.getLogger("sprosinab.api")


@dataclass
class Ctx:
    bot: Any
    identity: dict = field(default_factory=dict)


Handler = Callable[[Ctx, dict, dict, dict], Awaitable[tuple[int, dict]]]
ROUTES: dict[str, tuple[Handler, str]] = {}           # путь → (обработчик, доступ: public|user|admin)
_last_report: dict[int, float] = defaultdict(float)
_track_hits: dict[int, list] = defaultdict(list)


def route(path: str, access: str = "user"):
    def deco(fn: Handler) -> Handler:
        ROUTES[path] = (fn, access)
        return fn
    return deco


def is_admin(uid: int) -> bool:
    return int(uid) in config.ADMIN_IDS


def err(message: str, status: int = 400, **extra) -> tuple[int, dict]:
    return status, {"ok": False, "error": message, **extra}


def ok(**data) -> tuple[int, dict]:
    return 200, {"ok": True, **data}


def who(user: dict) -> str:
    name = (user.get("first_name") or "админ").strip()
    if user.get("username"):
        name += " @" + user["username"]
    return name[:60]


async def dispatch(ctx: Ctx, path: str, init_data: str, body: dict, files: dict) -> tuple[int, dict]:
    """Единая точка входа: проверка подписи Telegram, прав и вызов обработчика."""
    if path not in ROUTES:
        return err("Не найдено", 404)
    fn, access = ROUTES[path]
    user: dict = {}
    if access != "public":
        res = check_init_data(init_data or "", config.TOKEN, config.INIT_DATA_MAX_AGE)
        if res.user is None:
            log.warning("initData отклонён (%s): %s — %s", path, res.reason, res.detail)
            return 401, {"ok": False, "error": "Не удалось подтвердить, что запрос пришёл из Telegram",
                         "code": res.reason, "detail": res.detail, "bot_username": ctx.identity.get("username")}
        user = res.user
        if access == "admin" and not is_admin(user["id"]):
            return err("Только для руководства", 403)
    try:
        return await fn(ctx, user, body or {}, files or {})
    except Exception as e:  # noqa: BLE001
        log.exception("API %s упал", path)
        return err("Ошибка сервера — попробуй ещё раз чуть позже", 500)


def _int(v, default: int | None = None) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _bool(v) -> bool:
    return v in (True, 1, "1", "true", "yes", "on")


# ================================================================ общие

@route("/api/health", "public")
async def health(ctx: Ctx, user, body, files):
    token = config.TOKEN or ""
    return ok(service="sprosinab", webapp=config.WEBAPP_URL or None,
              bot_id=token.split(":")[0] if ":" in token else None, bot_username=ctx.identity.get("username"),
              server_time=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))


@route("/api/diag", "public")
async def diag(ctx: Ctx, user, body, files):
    res = check_init_data(str(body.get("initData") or ""), config.TOKEN, config.INIT_DATA_MAX_AGE)
    token = config.TOKEN or ""
    return 200, {"ok": res.user is not None, "code": res.reason, "detail": res.detail,
                 "bot_id_in_env": token.split(":")[0] if ":" in token else None}


@route("/api/bootstrap")
async def bootstrap(ctx: Ctx, user, body, files):
    uid = int(user["id"])
    store.touch_user(uid, user.get("first_name", ""), user.get("username", ""))
    data = {
        "me": {"id": uid, "firstName": user.get("first_name", ""), "isAdmin": is_admin(uid)},
        "news": content.news_list(),
        "events": content.events_list(uid),
        "polls": [p for p in content.polls_list(uid) if not p["closed"] or p["mine"]][:6],
        "links": content.links(),
        "people": content.people(),
        "docs": content.docs(),
        "myUnread": reports.unread(uid),
        "myCount": len(reports.mine(uid, 50)),
    }
    if is_admin(uid):
        data["adminOpen"] = store.count("SELECT COUNT(*) FROM reports WHERE COALESCE(status,'new')<>'done'")
        data["adminNew"] = store.count("SELECT COUNT(*) FROM reports WHERE COALESCE(status,'new')='new'")
    return ok(data=data)


@route("/api/track")
async def track(ctx: Ctx, user, body, files):
    """Анонимная статистика: какие экраны открывают, что ищут. Кто именно — не сохраняется."""
    uid = int(user["id"])
    now = time.time()
    hits = [t for t in _track_hits[uid] if now - t < 60]
    if len(hits) >= 30:                     # не больше 30 пачек в минуту с одного человека
        _track_hits[uid] = hits
        return ok()
    _track_hits[uid] = hits + [now]
    store.seen(uid)
    evs = body.get("events")
    if not isinstance(evs, list):
        return ok()
    for e in evs[:40]:
        if not isinstance(e, dict):
            continue
        name = str(e.get("n") or "")[:20]
        key = str(e.get("k") or "")[:80]
        if name in ("open", "screen", "tap", "search", "search_empty", "platform", "link"):
            if name in ("search", "search_empty"):
                key = re.sub(r"\s+", " ", key.lower()).strip()[:40]
                if not key:
                    continue
            store.hit(name, key)
    return ok()


# ================================================================ обращения

@route("/api/report")
async def report(ctx: Ctx, user, body, files):
    kind = str(body.get("kind") or "")
    if kind not in KINDS:
        return err("Неизвестный тип обращения")
    text = str(body.get("text") or "").strip()
    photos = [p for p in (files.get("photos") or []) if p][: config.MAX_PHOTOS]
    if any(len(p) > config.MAX_UPLOAD_MB * 1024 * 1024 for p in photos):
        return err(f"Файл больше {config.MAX_UPLOAD_MB} МБ")
    if not text and not photos:
        return err("Добавь описание или фото")
    uid = int(user["id"])
    now = time.time()
    if now - _last_report[uid] < config.RATE_LIMIT_SECONDS:
        return err("Слишком часто. Подожди немного и попробуй снова 🙏", 429)
    _last_report[uid] = now
    store.touch_user(uid, user.get("first_name", ""), user.get("username", ""))
    anon = _bool(body.get("anon"))
    good, rid, msg = await reports.create(ctx.bot, kind, user, text, location=str(body.get("location") or ""),
                                          anon=anon, photos=photos, source="app")
    if not good:
        return err(msg or "Не удалось отправить", 502)
    from keyboards import main_kb
    from tgutil import send
    await send(ctx.bot, uid, reports.ack_text(kind, rid, anon), main_kb())
    return ok(id=rid, ack=KINDS[kind].user_ack)


@route("/api/my-reports")
async def my_reports(ctx: Ctx, user, body, files):
    uid = int(user["id"])
    data = [reports.public(r) for r in reports.mine(uid)]
    reports.mark_seen(uid)
    return ok(data=data)


@route("/api/report-reply")
async def report_reply(ctx: Ctx, user, body, files):
    good, msg = await reports.user_reply(ctx.bot, body.get("id"), int(user["id"]), str(body.get("text") or ""))
    if not good:
        return err(msg)
    return ok(report=reports.public(reports.get(body.get("id"))))


@route("/api/rate")
async def rate(ctx: Ctx, user, body, files):
    value = _int(body.get("rating")) if body.get("rating") is not None else None
    if body.get("rating") is not None and value is None:
        return err("Оценка — от 1 до 5")
    review = body.get("review")
    good, msg = await reports.rate(ctx.bot, body.get("id"), int(user["id"]), value, None if review is None else str(review))
    if not good:
        return err(msg)
    return ok(report=reports.public(reports.get(body.get("id"))))


# ================================================================ анонсы и опросы

@route("/api/events")
async def events(ctx: Ctx, user, body, files):
    return ok(data=content.events_list(int(user["id"])))


@route("/api/event-rsvp")
async def event_rsvp(ctx: Ctx, user, body, files):
    name = ("@" + user["username"]) if user.get("username") else (user.get("first_name") or "")
    if user.get("username") and user.get("first_name"):
        name = f"{user['first_name']} (@{user['username']})"
    good, msg = content.set_rsvp(_int(body.get("id"), 0), int(user["id"]), str(body.get("answer") or ""), name)
    if not good:
        return err(msg)
    store.touch_user(int(user["id"]), user.get("first_name", ""), user.get("username", ""))
    e = store.q("SELECT * FROM events WHERE id=?", (_int(body.get("id"), 0),), one=True)
    return ok(event=content.event_view(e, int(user["id"])))


@route("/api/event-sub")
async def event_sub(ctx: Ctx, user, body, files):
    good, msg = content.set_sub(_int(body.get("id"), 0), int(user["id"]), _bool(body.get("on")))
    return ok() if good else err(msg)


@route("/api/poll-vote")
async def poll_vote(ctx: Ctx, user, body, files):
    opts = body.get("options")
    if not isinstance(opts, list):
        opts = [body.get("option")]
    v, msg = content.poll_vote(_int(body.get("id"), 0), int(user["id"]), [o for o in opts if _int(o) is not None])
    return ok(poll=v) if v else err(msg)


# ================================================================ документы в чат

@route("/api/doc")
async def doc(ctx: Ctx, user, body, files):
    from aiogram.types import FSInputFile
    doc_id = str(body.get("id") or "")
    uid = int(user["id"])
    if doc_id == "map":
        if not config.MAP_FILE.exists():
            return err("Файл карты пока не загружен на сервер", 404)
        await ctx.bot.send_document(uid, FSInputFile(config.MAP_FILE, filename="Карта корпуса — Верхняя Масловка 15.pdf"),
                                    caption="🗺 Карта корпуса | ССт НАБ")
        return ok(message="Карта отправлена в чат с ботом 🗺")
    paths = content.doc_files(doc_id)
    if not paths:
        return err("Файл пока не загружен на сервер", 404)
    for p in paths:
        await ctx.bot.send_document(uid, FSInputFile(p))
    store.hit("doc_sent", doc_id)
    return ok(message="Документ отправлен в чат с ботом 📄")


@route("/api/faq")
async def faq(ctx: Ctx, user, body, files):
    from aiogram.types import FSInputFile
    if not config.FAQ_FILE.exists():
        return err("Файл FAQ пока не загружен на сервер", 404)
    await ctx.bot.send_document(int(user["id"]), FSInputFile(config.FAQ_FILE, filename="FAQ — Часто задаваемые вопросы (НАБ).pdf"),
                                caption="📚 FAQ — часто задаваемые вопросы | ССт НАБ")
    return ok(message="PDF отправлен в чат с ботом 📄")


# ================================================================ Пульт: обзор и настройки

def _settings() -> dict:
    return {"slaHours": reports.sla_hours(), "slaOn": store.setting("sla_on", "1") == "1",
            "digestOn": store.setting("digest_on", "1") == "1", "exportOn": store.setting("export_on", "1") == "1",
            "notifyDm": config.NOTIFY_ADMINS_DM}


@route("/api/admin/summary", "admin")
async def admin_summary(ctx: Ctx, user, body, files):
    return ok(data=stats.dashboard(_int(body.get("days"), 30) or 30), settings=_settings(),
              target={"chat": config.SUPER_CHAT_ID, "topic": config.TOPIC_ID})


@route("/api/admin/settings", "admin")
async def admin_settings(ctx: Ctx, user, body, files):
    if "slaHours" in body:
        h = _int(body.get("slaHours"))
        if not h or not 1 <= h <= 24 * 14:
            return err("Срок — от 1 до 336 часов")
        store.set_setting("sla_hours", h)
    for key, name in (("slaOn", "sla_on"), ("digestOn", "digest_on"), ("exportOn", "export_on")):
        if key in body:
            store.set_setting(name, "1" if _bool(body[key]) else "0")
    return ok(settings=_settings())


# ================================================================ Пульт: обращения

@route("/api/admin/reports", "admin")
async def admin_reports(ctx: Ctx, user, body, files):
    rows = reports.admin_list(str(body.get("filter") or "open"), str(body.get("kind") or ""), str(body.get("q") or "").strip())
    counts = {f: len(reports.admin_list(f, limit=999)) for f in ("open", "new", "work", "done")}
    return ok(data=[reports.admin_view(r) for r in rows], counts=counts)


@route("/api/admin/report-action", "admin")
async def admin_report_action(ctx: Ctx, user, body, files):
    act = body.get("action")
    if act == "reply":
        good, msg = await reports.team_reply(ctx.bot, body.get("id"), str(body.get("text") or ""), who(user), via_app=True)
    elif act in ("take", "done", "reopen"):
        _, msg = await reports.set_status(ctx.bot, body.get("id"), act, who(user))
        good = not msg
    else:
        good, msg = False, "Неизвестное действие"
    if not good:
        return err(msg)
    return ok(report=reports.admin_view(reports.get(body.get("id"))))


@route("/api/admin/digest", "admin")
async def admin_digest(ctx: Ctx, user, body, files):
    if _bool(body.get("preview")):
        return ok(text=reports.digest_text())
    return ok() if await reports.send_digest(ctx.bot) else err("Не получилось отправить в группу", 502)


@route("/api/admin/export", "admin")
async def admin_export(ctx: Ctx, user, body, files):
    y, m = _int(body.get("year")), _int(body.get("month"))
    if not y or not m or not 1 <= m <= 12 or not 2020 <= y <= 2100:
        y, m = stats.prev_month()
    sent = await stats.send_export(ctx.bot, [int(user["id"])], y, m)
    return ok(message="Таблица отправлена тебе в чат с ботом 📤") if sent else err("Не получилось отправить файл — нажми «Старт» у бота", 502)


@route("/api/admin/broadcast", "admin")
async def admin_broadcast(ctx: Ctx, user, body, files):
    from tgutil import broadcast, esc
    text = str(body.get("text") or "").strip()
    if not 2 <= len(text) <= 3500:
        return err("Слишком коротко или длинно")

    async def run():
        good, total = await broadcast(ctx.bot, store.all_user_ids(), "📣 " + esc(text))
        from tgutil import send
        await send(ctx.bot, int(user["id"]), f"✅ Рассылка завершена: доставлено {good} из {total}.")
    asyncio.create_task(run())
    return ok(message=f"Рассылка запущена на {len(store.all_user_ids())} человек")


# ================================================================ Пульт: новости

@route("/api/admin/news", "admin")
async def admin_news(ctx: Ctx, user, body, files):
    return ok(data=content.news_list(include_hidden=True, limit=100))


@route("/api/admin/upload-image", "admin")
async def admin_upload_image(ctx: Ctx, user, body, files):
    raw = (files.get("file") or [b""])[0]
    if not raw:
        return err("Пустой файл")
    if len(raw) > 6 * 1024 * 1024:
        return err("Картинка больше 6 МБ")
    name = content.save_media(raw)
    return ok(name=name, url=f"api/media/{name}") if name else err("Нужна картинка JPG, PNG или WebP")


@route("/api/admin/news-save", "admin")
async def admin_news_save(ctx: Ctx, user, body, files):
    nid, msg = content.news_save(body, who(user))
    if not nid:
        return err(msg)
    if _bool(body.get("send")):
        asyncio.create_task(content.news_broadcast(ctx.bot, nid))
    return ok(id=nid)


@route("/api/admin/news-del", "admin")
async def admin_news_del(ctx: Ctx, user, body, files):
    content.news_delete(_int(body.get("id"), 0))
    return ok()


@route("/api/admin/news-send", "admin")
async def admin_news_send(ctx: Ctx, user, body, files):
    asyncio.create_task(content.news_broadcast(ctx.bot, _int(body.get("id"), 0)))
    return ok(message="Новость рассылается всем пользователям бота")


# ================================================================ Пульт: анонсы

@route("/api/admin/events", "admin")
async def admin_events(ctx: Ctx, user, body, files):
    evs = content.events_list(past_days=60, include_hidden=True)
    for v in evs:
        v["people"] = content.attendees(v["id"])
        v["subs"] = store.count("SELECT COUNT(*) FROM event_subs WHERE event_id=?", (v["id"],))
    return ok(data=evs, kinds=content.EVENT_KINDS)


@route("/api/admin/event-save", "admin")
async def admin_event_save(ctx: Ctx, user, body, files):
    eid, msg = content.event_save(body, int(user["id"]))
    if not eid:
        return err(msg)
    if _bool(body.get("announce")):
        asyncio.create_task(content.announce(ctx.bot, eid))
    return ok(id=eid)


@route("/api/admin/event-del", "admin")
async def admin_event_del(ctx: Ctx, user, body, files):
    content.event_delete(_int(body.get("id"), 0))
    return ok()


# ================================================================ Пульт: опросы

@route("/api/admin/polls", "admin")
async def admin_polls(ctx: Ctx, user, body, files):
    return ok(data=content.polls_list(admin=True))


@route("/api/admin/poll-save", "admin")
async def admin_poll_save(ctx: Ctx, user, body, files):
    pid, msg = content.poll_save(body, int(user["id"]))
    if not pid:
        return err(msg)
    if _bool(body.get("send")):
        asyncio.create_task(content.poll_broadcast(ctx.bot, pid))
    return ok(id=pid)


@route("/api/admin/poll-close", "admin")
async def admin_poll_close(ctx: Ctx, user, body, files):
    store.x("UPDATE polls SET closed=? WHERE id=?", (1 if _bool(body.get("closed", True)) else 0, _int(body.get("id"), 0)))
    return ok()


@route("/api/admin/poll-del", "admin")
async def admin_poll_del(ctx: Ctx, user, body, files):
    pid = _int(body.get("id"), 0)
    store.x("DELETE FROM polls WHERE id=?", (pid,))
    store.x("DELETE FROM poll_votes WHERE poll_id=?", (pid,))
    return ok()


@route("/api/admin/poll-send", "admin")
async def admin_poll_send(ctx: Ctx, user, body, files):
    asyncio.create_task(content.poll_broadcast(ctx.bot, _int(body.get("id"), 0)))
    return ok(message="Опрос рассылается всем пользователям бота")


# ================================================================ Пульт: группы, контакты, документы

@route("/api/admin/links-save", "admin")
async def admin_links_save(ctx: Ctx, user, body, files):
    good, msg = content.links_save(body.get("items") or [])
    return ok(data=content.links()) if good else err(msg)


@route("/api/admin/people-save", "admin")
async def admin_people_save(ctx: Ctx, user, body, files):
    good, msg = content.people_save(body.get("items") or [])
    return ok(data=content.people()) if good else err(msg)


@route("/api/admin/docs", "admin")
async def admin_docs(ctx: Ctx, user, body, files):
    return ok(data=content.docs(include_hidden=True), building=_BUILD.get("running", False))


@route("/api/admin/doc-save", "admin")
async def admin_doc_save(ctx: Ctx, user, body, files):
    doc_id = str(body.get("id") or "")
    if not any(d["id"] == doc_id for d in content.docs(True)):
        return err("Документ не найден", 404)
    content.doc_meta_save(doc_id, title=body.get("title"), sub=body.get("sub"),
                          hidden=_bool(body.get("hidden")) if "hidden" in body else None)
    _schedule_build()
    return ok()


@route("/api/admin/doc-del", "admin")
async def admin_doc_del(ctx: Ctx, user, body, files):
    good, msg = content.doc_delete(str(body.get("id") or ""))
    if good:
        _schedule_build()
    return ok() if good else err(msg)


@route("/api/admin/doc-upload", "admin")
async def admin_doc_upload(ctx: Ctx, user, body, files):
    raw = (files.get("file") or [b""])[0]
    if not raw:
        return err("Пустой файл")
    if len(raw) > 40 * 1024 * 1024:
        return err("Файл больше 40 МБ — сожми PDF")
    doc_id, msg = content.doc_upload(raw, str(body.get("title") or ""), str(body.get("sub") or ""))
    if not doc_id:
        return err(msg)
    _schedule_build(doc_id)
    return ok(id=doc_id, message="Документ добавлен. Бот уже отправляет его, а страницы для листания соберутся за минуту.")


# ---------------------------------------------------------------- пересборка страниц документов

_BUILD: dict = {"running": False, "again": False}


def _schedule_build(only: str | None = None) -> None:
    if not shutil.which("pdftoppm"):
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    if _BUILD["running"]:
        _BUILD["again"] = True
        return
    asyncio.create_task(_build_docs(only))


async def _build_docs(only: str | None) -> None:
    _BUILD["running"] = True
    try:
        while True:
            _BUILD["again"] = False
            args = [sys.executable, str(config.BASE_DIR / "tools" / "build_docs.py")] + (["--only", only] if only else [])
            p = await asyncio.create_subprocess_exec(*args, cwd=str(config.BASE_DIR),
                                                     stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await p.wait()
            pub = config.BASE_DIR / "tools" / "publish_webapp.sh"
            if pub.exists():
                p = await asyncio.create_subprocess_exec("bash", str(pub), stdout=asyncio.subprocess.DEVNULL,
                                                         stderr=asyncio.subprocess.DEVNULL)
                await p.wait()
            if not _BUILD["again"]:
                break
            only = None
    finally:
        _BUILD["running"] = False

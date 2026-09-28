"""
HTTP-API для Telegram Mini App «Спроси ССт НАБ».

Поднимается в том же процессе, что и бот (см. main.py).
Nginx проксирует на него путь /api/ и отдаёт статику из webapp/.

Безопасность: каждый запрос обязан принести Telegram initData —
подпись проверяется HMAC-SHA256 по алгоритму из документации Telegram,
поэтому подделать «я такой-то пользователь» снаружи нельзя.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from aiogram import Bot
from aiogram.types import FSInputFile
from aiohttp import web

import config
from delivery import KINDS, Report, deliver, display_name, notify_admins
from security import check_init_data

log = logging.getLogger("sprosinab.api")


def _check(init_data: str, where: str):
    """Проверяет подпись и, если не сошлось, пишет причину в журнал службы."""
    res = check_init_data(init_data, config.TOKEN, config.INIT_DATA_MAX_AGE)
    if res.user is None:
        log.warning("initData отклонён (%s): %s — %s", where, res.reason, res.detail)
    return res


_IDENTITY: dict = {}


def _auth_error(res) -> web.Response:
    """Ответ с коротким кодом причины — чтобы было видно прямо в приложении."""
    return web.json_response(
        {"ok": False, "error": "Не удалось подтвердить, что запрос пришёл из Telegram",
         "code": res.reason, "detail": res.detail,
         "bot_username": _IDENTITY.get("username")},
        status=401,
    )

_last_action: dict[int, float] = defaultdict(float)


def _err(message: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


def _throttled(user_id: int) -> bool:
    now = time.time()
    if now - _last_action[user_id] < config.RATE_LIMIT_SECONDS:
        return True
    _last_action[user_id] = now
    return False


# --------------------------------------------------------------------------
# Обработчики
# --------------------------------------------------------------------------

async def health(request: web.Request) -> web.Response:
    token = config.TOKEN or ""
    identity = request.app.get("identity") or {}
    return web.json_response({
        "ok": True,
        "service": "sprosinab",
        "webapp": config.WEBAPP_URL or None,
        # первые цифры токена — чтобы сверить с @BotFather, секрет не раскрывается
        "bot_id": token.split(":")[0] if ":" in token else None,
        # кому токен принадлежит на самом деле — спросили у Telegram при старте
        "bot_username": identity.get("username"),
        "admins_dm": config.NOTIFY_ADMINS_DM and bool(config.ADMIN_IDS),
        "server_time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "docs": sorted(p.name for p in config.DOCS_DIR.glob("*.pdf")) if config.DOCS_DIR.exists() else [],
    })


async def diag(request: web.Request) -> web.Response:
    """Диагностика подписи: показывает, почему приложение получает отказ."""
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return _err("Некорректный запрос")
    res = _check(payload.get("initData", ""), "diag")
    token = config.TOKEN or ""
    return web.json_response({
        "ok": res.user is not None,
        "code": res.reason,
        "detail": res.detail,
        "bot_id_in_env": token.split(":")[0] if ":" in token else None,
        "server_time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    })


async def handle_report(request: web.Request) -> web.Response:
    """Вопрос / инициатива / тревожная кнопка / поломка (с фото)."""
    bot: Bot = request.app["bot"]
    add_user = request.app["add_user"]

    init_data = ""
    kind = ""
    text = ""
    location = ""
    is_anon = False
    photos: list[bytes] = []

    if request.content_type.startswith("multipart/"):
        reader = await request.multipart()
        async for part in reader:
            if part.name == "photos":
                chunk = await part.read(decode=False)
                if len(chunk) > config.MAX_UPLOAD_MB * 1024 * 1024:
                    return _err(f"Файл больше {config.MAX_UPLOAD_MB} МБ")
                if chunk:
                    photos.append(chunk)
            else:
                value = (await part.text()).strip()
                if part.name == "initData":
                    init_data = value
                elif part.name == "kind":
                    kind = value
                elif part.name == "text":
                    text = value
                elif part.name == "location":
                    location = value
                elif part.name == "anon":
                    is_anon = value in {"1", "true", "yes"}
    else:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return _err("Некорректный запрос")
        init_data = payload.get("initData", "")
        kind = str(payload.get("kind", ""))
        text = str(payload.get("text", "")).strip()
        location = str(payload.get("location", "")).strip()
        is_anon = bool(payload.get("anon"))

    res = _check(init_data, "report")
    if res.user is None:
        return _auth_error(res)
    user = res.user

    if kind not in KINDS:
        return _err("Неизвестный тип обращения")

    text = text[:3000]
    if not text and not photos:
        return _err("Добавь описание или фото")

    if _throttled(user["id"]):
        return _err("Слишком часто. Подожди немного и попробуй снова 🙏", 429)

    add_user(user["id"])

    report = Report(
        kind=kind,
        text=text or "Без описания",
        user_id=user["id"],
        sender_name=display_name(user.get("username"), user.get("first_name"), is_anon),
        location=location[:200],
        photos=photos[: config.MAX_PHOTOS],
    )

    try:
        file_ids = await deliver(bot, report)
        await notify_admins(bot, report, file_ids)
    except Exception as e:  # noqa: BLE001
        log.exception("deliver from webapp failed")
        return _err(f"Не удалось отправить: {e}", 502)

    # Дублируем автоответ в личку — чтобы у студента осталась история обращения.
    try:
        await bot.send_message(user["id"], KINDS[kind].user_ack)
    except Exception:  # noqa: BLE001
        pass

    return web.json_response({"ok": True, "ack": KINDS[kind].user_ack})


async def handle_doc(request: web.Request) -> web.Response:
    """Отправляет документ НПБ в личку пользователю."""
    bot: Bot = request.app["bot"]
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return _err("Некорректный запрос")

    res = _check(payload.get("initData", ""), "doc")
    if res.user is None:
        return _auth_error(res)
    user = res.user

    doc_id = str(payload.get("id", ""))
    if doc_id == "map":                       # карта корпуса — тоже PDF в чат
        if not config.MAP_FILE.exists():
            return _err("Файл карты пока не загружен на сервер", 404)
        await bot.send_document(user["id"], FSInputFile(config.MAP_FILE, filename="Карта корпуса — Верхняя Масловка 15.pdf"),
                                caption="🗺 Карта корпуса | ССт НАБ")
        return web.json_response({"ok": True, "message": "Карта отправлена в чат с ботом 🗺"})
    files = config.NPB_FILES.get(doc_id)
    if not files:
        return _err("Документ не найден")

    sent = 0
    for rel in files:
        path = config.BASE_DIR / rel
        if path.exists():
            await bot.send_document(user["id"], FSInputFile(path))
            sent += 1

    if not sent:
        return _err("Файл пока не загружен на сервер", 404)

    return web.json_response({"ok": True, "message": "Документ отправлен в чат с ботом 📄"})


async def handle_faq(request: web.Request) -> web.Response:
    """Отправляет PDF с FAQ в личку пользователю."""
    bot: Bot = request.app["bot"]
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return _err("Некорректный запрос")

    res = _check(payload.get("initData", ""), "faq")
    if res.user is None:
        return _auth_error(res)
    user = res.user

    if not config.FAQ_FILE.exists():
        return _err("Файл FAQ пока не загружен на сервер", 404)

    await bot.send_document(
        user["id"],
        FSInputFile(config.FAQ_FILE, filename="FAQ — Часто задаваемые вопросы (НАБ).pdf"),
        caption="📚 FAQ — часто задаваемые вопросы | ССт НАБ",
    )
    return web.json_response({"ok": True, "message": "PDF отправлен в чат с ботом 📄"})


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = config.WEBAPP_URL or "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    return response


def build_app(bot: Bot, add_user, identity: dict | None = None) -> web.Application:
    app = web.Application(
        middlewares=[cors_middleware],
        client_max_size=(config.MAX_UPLOAD_MB + 2) * config.MAX_PHOTOS * 1024 * 1024,
    )
    app["bot"] = bot
    app["add_user"] = add_user
    # держим ссылку на тот же словарь, что заполняет main.py при старте
    global _IDENTITY
    _IDENTITY = identity if identity is not None else {}
    app["identity"] = _IDENTITY

    app.router.add_get("/api/health", health)
    app.router.add_post("/api/report", handle_report)
    app.router.add_post("/api/doc", handle_doc)
    app.router.add_post("/api/faq", handle_faq)
    app.router.add_post("/api/diag", diag)

    # Режим «без nginx»: Python сам отдаёт статику Mini App.
    if os.getenv("SERVE_STATIC", "0") == "1":
        webapp_dir = config.BASE_DIR / "webapp"

        def _file(name: str):
            async def handler(_: web.Request) -> web.FileResponse:
                return web.FileResponse(webapp_dir / name)
            return handler

        app.router.add_get("/", _file("index.html"))
        app.router.add_get("/index.html", _file("index.html"))
        app.router.add_get("/app.js", _file("app.js"))
        app.router.add_get("/styles.css", _file("styles.css"))
        app.router.add_static("/assets", webapp_dir / "assets", show_index=False)

    return app


async def run_api(bot: Bot, add_user, identity: dict | None = None) -> None:
    runner = web.AppRunner(build_app(bot, add_user, identity))
    await runner.setup()
    site = web.TCPSite(runner, config.API_HOST, config.API_PORT)
    await site.start()
    log.info("🌐 API Mini App слушает http://%s:%s", config.API_HOST, config.API_PORT)
    while True:
        await asyncio.sleep(3600)

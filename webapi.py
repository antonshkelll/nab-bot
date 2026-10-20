"""
HTTP-сервер API для Mini App (aiohttp). Поднимается в том же процессе, что и бот.
Nginx проксирует на него /api/ и отдаёт статику из webapp/.

Сами обработчики — в api.py; здесь только разбор запроса и ответ.
Безопасность: каждый запрос несёт Telegram initData, подпись проверяется
HMAC-SHA256 (security.py) — подделать «я такой-то пользователь» нельзя.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re

from aiohttp import web

import api
import config

log = logging.getLogger("sprosinab.api")


async def _parse(request: web.Request) -> tuple[str, dict, dict]:
    body: dict = {}
    files: dict[str, list[bytes]] = {}
    if request.content_type.startswith("multipart/"):
        reader = await request.multipart()
        async for part in reader:
            if part.filename is not None or part.name in ("photos", "file"):
                data = await part.read(decode=False)
                files.setdefault(part.name, []).append(bytes(data))
            else:
                body[part.name] = (await part.text()).strip()
    elif request.method == "POST":
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        if not isinstance(body, dict):
            body = {}
    init_data = request.headers.get("X-Init-Data") or str(body.pop("initData", "") or "")
    return init_data, body, files


async def handle(request: web.Request) -> web.Response:
    ctx: api.Ctx = request.app["ctx"]
    try:
        init_data, body, files = await _parse(request)
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "error": "Некорректный запрос"}, status=400)
    status, data = await api.dispatch(ctx, request.path, init_data, body, files)
    return web.json_response(data, status=status)


async def media(request: web.Request) -> web.StreamResponse:
    name = request.match_info["name"]
    if not re.match(r"^[a-f0-9]{16}\.(jpg|png|webp)$", name):
        raise web.HTTPNotFound()
    path = config.MEDIA_DIR / name
    if not path.exists():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=2592000, immutable"})


@web.middleware
async def cors_middleware(request: web.Request, handler):
    response = web.Response(status=204) if request.method == "OPTIONS" else await handler(request)
    response.headers["Access-Control-Allow-Origin"] = config.WEBAPP_URL or "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Init-Data"
    response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    return response


def build_app(bot, identity: dict | None = None) -> web.Application:
    app = web.Application(middlewares=[cors_middleware],
                          client_max_size=max(45, (config.MAX_UPLOAD_MB + 2) * config.MAX_PHOTOS) * 1024 * 1024)
    app["ctx"] = api.Ctx(bot=bot, identity=identity if identity is not None else {})
    app.router.add_get("/api/media/{name}", media)
    for path, (_, access) in api.ROUTES.items():
        app.router.add_post(path, handle)
        if access == "public":
            app.router.add_get(path, handle)

    # Режим «без nginx»: Python сам отдаёт статику Mini App.
    if os.getenv("SERVE_STATIC", "0") == "1":
        webapp_dir = config.BASE_DIR / "webapp"

        def _file(name: str):
            async def h(_: web.Request) -> web.FileResponse:
                return web.FileResponse(webapp_dir / name)
            return h

        app.router.add_get("/", _file("index.html"))
        for n in ("index.html", "app.js", "admin.js", "styles.css"):
            app.router.add_get("/" + n, _file(n))
        app.router.add_static("/assets", webapp_dir / "assets", show_index=False)
    return app


async def run_api(bot, identity: dict | None = None) -> None:
    runner = web.AppRunner(build_app(bot, identity))
    await runner.setup()
    site = web.TCPSite(runner, config.API_HOST, config.API_PORT)
    await site.start()
    log.info("🌐 API Mini App слушает http://%s:%s", config.API_HOST, config.API_PORT)
    while True:
        await asyncio.sleep(3600)

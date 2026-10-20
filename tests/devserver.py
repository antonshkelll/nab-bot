"""
Локальный сервер для проверки приложения без Telegram и без aiohttp:
отдаёт webapp/ и подключает настоящие обработчики api.py к поддельному боту.

    python3 tests/devserver.py --port 8790 --seed

Токен для подписи initData — из переменной BOT_TOKEN (по умолчанию «123456:TEST-TOKEN»).
Все сообщения «в Telegram» складываются в /__calls (GET) — удобно для тестов.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import email.parser
import email.policy
import json
import mimetypes
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
TMP = Path(os.environ.get("DEV_DATA") or tempfile.mkdtemp(prefix="nabdev_"))
os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("SUPER_CHAT_ID", "-100500")
os.environ.setdefault("TOPIC_ID", "7")
os.environ.setdefault("WEBAPP_URL", "https://app.example")
os.environ["DATA_DIR"] = str(TMP / "data")

import stubs  # noqa: E402

stubs.install()
import config  # noqa: E402

config.RATE_LIMIT_SECONDS = int(os.environ.get("DEV_RATE", "2"))
import store  # noqa: E402

store.reset_for_tests(TMP / "data" / "nab.db")
import api  # noqa: E402
import content  # noqa: E402
import reports  # noqa: E402

BOT = stubs.FakeBot()
CTX = api.Ctx(bot=BOT, identity={"username": "sprosinab_bot"})
LOOP = asyncio.new_event_loop()
threading.Thread(target=LOOP.run_forever, daemon=True).start()
WEB = ROOT / "webapp"


def run(coro):
    return asyncio.run_coroutine_threadsafe(coro, LOOP).result(60)


def seed() -> None:
    today = content.now_msk().date()
    store.touch_user(1, "Алина", "jannalli", started=True)
    for uid in range(100, 140):
        store.touch_user(uid, f"Студент{uid}", "", started=True)
    content.news_save({"tag": "Набор", "title": "Открыт набор в актив ССт НАБ", "pinned": True,
                       "body": "Хочешь делать мероприятия, выезды и проекты факультета? Заполняй анкету до конца недели — "
                               "мы позовём тебя на знакомство.\n\nЖдём всех курсов!",
                       "btnText": "Заполнить анкету", "btnUrl": "https://forms.gle/example"}, "Алина")
    content.news_save({"tag": "Важно", "title": "Пересдачи: график на октябрь",
                       "body": "Учебный офис опубликовал график пересдач. Подробности — в FAQ, раздел «Зачёты и экзамены».",
                       "btnText": "Открыть FAQ", "btnUrl": "#/faq"}, "Мусо")
    e1, _ = content.event_save({"title": "Общее собрание актива", "date": (today + dt.timedelta(days=2)).isoformat(), "time": "18:30",
                                "place": "Ауд. 412, Масловка", "kind": "meeting", "rsvp": True,
                                "descr": "Подводим итоги месяца и планируем выезд. Приходи — будет пицца 🍕"}, 1)
    content.event_save({"title": "Дедлайн анкеты на выезд", "date": (today + dt.timedelta(days=5)).isoformat(), "kind": "deadline",
                        "rsvp": False}, 1)
    content.event_save({"title": "Посвят НАБ", "date": (today + dt.timedelta(days=12)).isoformat(), "time": "19:00",
                        "place": "Актовый зал", "kind": "event", "rsvp": True}, 1)
    for uid in range(100, 118):
        content.set_rsvp(e1, uid, "yes" if uid % 4 else "no", f"Студент{uid}")
    pid, _ = content.poll_save({"question": "Куда поедем на осенний выезд?", "options": ["Подмосковье", "Питер", "Казань"]}, 1)
    for uid in range(100, 125):
        content.poll_vote(pid, uid, [uid % 3])
    u = {"id": 777, "first_name": "Аня", "username": "anya"}
    _, r1, _ = run(reports.create(BOT, "question", u, "Когда будет пересдача по матанализу и где смотреть расписание?", source="app"))
    run(reports.team_reply(BOT, r1, "Пересдача 21 октября, расписание — на ruz.fa.ru. Удачи!", "Алина"))
    run(reports.set_status(BOT, r1, "done", "Алина"))
    _, r2, _ = run(reports.create(BOT, "repair", u, "Не работает розетка у окна", location="ауд. 412", anon=True))
    run(reports.set_status(BOT, r2, "take", "Мусо"))
    _, r3, _ = run(reports.create(BOT, "initiative", {"id": 101, "first_name": "Петя", "username": "petya"},
                                  "Давайте сделаем кофе-поинт на 3 этаже"))
    store.x("UPDATE reports SET created_at=? WHERE id=?", (int(time.time()) - 60 * 3600, r3))
    for i, (scr, n) in enumerate((("home", 120), ("faq", 64), ("map", 41), ("npb", 22), ("events", 18), ("my", 9))):
        store.hit("screen", scr, n)
    for qn, n in (("стипендия", 14), ("пересдача", 9), ("412", 6), ("общежитие", 5)):
        store.hit("search", qn, n)
    store.hit("search_empty", "парковка", 3)
    store.hit("platform", "android", 30)
    store.hit("platform", "ios", 22)
    store.hit("platform", "tdesktop", 9)
    for d in range(30):
        day = (today - dt.timedelta(days=d)).isoformat()
        for k in range(5 + (d * 7) % 23):
            store.x("INSERT OR IGNORE INTO stat_uniq(period, uh) VALUES(?,?)", (f"d:{day}", f"x{d}-{k}"))
        store.hit("open", "", 8 + (d * 5) % 30, day=day)
    store.seen(777)


def parse_multipart(ctype: str, raw: bytes) -> tuple[dict, dict]:
    msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(
        b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw)
    body, files = {}, {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if part.get_filename() is not None or name in ("photos", "file"):
            files.setdefault(name, []).append(part.get_payload(decode=True) or b"")
        else:
            body[name] = (part.get_payload(decode=True) or b"").decode("utf-8", "replace").strip()
    return body, files


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, data: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/__calls":
            calls = [{"m": n, "chat": str(k.get("chat_id", "")), "text": str(k.get("text") or k.get("caption") or "")[:500]}
                     for n, k in BOT.calls]
            return self._send(200, json.dumps(calls, ensure_ascii=False).encode())
        if p.startswith("/api/media/"):
            f = config.MEDIA_DIR / p.rsplit("/", 1)[1]
            if f.exists():
                return self._send(200, f.read_bytes(), mimetypes.guess_type(str(f))[0] or "image/jpeg")
            return self._send(404, b"{}")
        if p.startswith("/api/"):
            status, data = run(api.dispatch(CTX, p, self.headers.get("X-Init-Data", ""), {}, {}))
            return self._send(status, json.dumps(data, ensure_ascii=False).encode())
        f = (WEB / (p.lstrip("/") or "index.html")).resolve()
        if not str(f).startswith(str(WEB)) or not f.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if f.suffix == ".woff":
            ctype = "font/woff"
        return self._send(200, f.read_bytes(), ctype)

    def do_POST(self):
        p = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/"):
            body, files = parse_multipart(ctype, raw)
        else:
            try:
                body, files = json.loads(raw or b"{}"), {}
            except ValueError:
                body, files = {}, {}
        init = self.headers.get("X-Init-Data") or str(body.pop("initData", "") or "")
        status, data = run(api.dispatch(CTX, p, init, body, files))
        return self._send(status, json.dumps(data, ensure_ascii=False).encode())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--seed", action="store_true")
    a = ap.parse_args()
    if a.seed:
        seed()
    print(f"devserver: http://127.0.0.1:{a.port}  (data: {TMP})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()

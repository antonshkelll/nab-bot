"""
Проверка бота без интернета и без aiogram: подменяем aiogram заглушками,
импортируем main.py целиком и прогоняем /start и запуск (on_startup).

    python3 tests/test_bot.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types as pytypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("WEBAPP_URL", "https://app.nablabel.ru")
os.environ.setdefault("BOT_TOKEN", "123:TEST")


# ---------------- заглушки aiogram ----------------

class Magic:
    """Поддерживает F.text == ..., F.data.startswith(...), & | ~ и т. п."""
    def __getattr__(self, _):
        return self

    def __call__(self, *a, **k):
        return self

    __eq__ = __ne__ = __and__ = __or__ = __invert__ = lambda self, *a: self  # noqa: E731
    __hash__ = object.__hash__


class Obj:
    def __init__(self, *a, **k):
        self.args, self.kwargs = a, k
        for key, val in k.items():
            setattr(self, key, val)


class Dispatcher:
    def __init__(self, *a, **k):
        self.middlewares = []
        self.update = Obj(outer_middleware=self.middlewares.append)

    def message(self, *a, **k):
        return lambda f: f

    callback_query = message

    def resolve_used_update_types(self):
        return []


class FakeBot:
    def __init__(self, *a, **k):
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, name):
        async def call(*a, **k):
            self.calls.append((name, k))
            if name == "get_me":
                return Obj(id=123, username="sprosinab_bot", full_name="Спроси ССт НАБ")
            return Obj()
        call.__name__ = name
        return call


def install_stubs() -> None:
    ag = pytypes.ModuleType("aiogram")
    ag.Bot = FakeBot
    ag.Dispatcher = Dispatcher
    ag.F = Magic()
    t = pytypes.ModuleType("aiogram.types")
    for n in ("BotCommand", "FSInputFile", "InlineKeyboardButton", "InlineKeyboardMarkup", "KeyboardButton",
              "MenuButtonWebApp", "ReplyKeyboardMarkup", "WebAppInfo", "BufferedInputFile", "InputMediaPhoto",
              "Message", "CallbackQuery", "User"):
        setattr(t, n, type(n, (Obj,), {}))
    ag.types = t
    mods = {
        "aiogram": ag, "aiogram.types": t,
        "aiogram.client": pytypes.ModuleType("aiogram.client"),
        "aiogram.client.default": pytypes.ModuleType("aiogram.client.default"),
        "aiogram.enums": pytypes.ModuleType("aiogram.enums"),
        "aiogram.filters": pytypes.ModuleType("aiogram.filters"),
        "aiogram.fsm": pytypes.ModuleType("aiogram.fsm"),
        "aiogram.fsm.context": pytypes.ModuleType("aiogram.fsm.context"),
        "aiogram.fsm.state": pytypes.ModuleType("aiogram.fsm.state"),
    }
    mods["aiogram.client.default"].DefaultBotProperties = Obj
    mods["aiogram.enums"].ParseMode = Obj(HTML="HTML")
    mods["aiogram.filters"].Command = Obj
    mods["aiogram.filters"].StateFilter = Obj
    mods["aiogram.fsm.context"].FSMContext = Obj
    mods["aiogram.fsm.state"].State = Obj
    mods["aiogram.fsm.state"].StatesGroup = type("StatesGroup", (), {})
    mods["aiogram.fsm.state"].any_state = object()
    sys.modules.update(mods)


class FakeMessage:
    def __init__(self):
        self.from_user = Obj(id=777, username="student", first_name="Студент")
        self.sent: list[tuple[str, tuple, dict]] = []

    async def answer_photo(self, photo, **k):
        self.sent.append(("photo", (photo,), k))
        return Obj(photo=[Obj(file_id="SMALL"), Obj(file_id="BIG_FILE_ID")])

    async def answer(self, text, **k):
        self.sent.append(("text", (text,), k))
        return Obj()


class FakeState:
    async def clear(self):
        pass


ok = 0


def check(cond: bool, msg: str) -> None:
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1


async def run() -> None:
    install_stubs()
    tmp = tempfile.mkdtemp()
    os.chdir(tmp)                      # база users_nab.db создаётся рядом с main.py — это нормально
    import main  # noqa: WPS433

    # --- /start: фото + кнопка приложения, затем клавиатура ---
    m = FakeMessage()
    await main.cmd_start(m, FakeState())
    kinds = [s[0] for s in m.sent]
    check(kinds[:2] == ["photo", "text"], f"/start должен прислать фото и меню, а прислал {kinds}")
    photo_kw = m.sent[0][2]
    check("Спроси ССт НАБ" in photo_kw["caption"], "подпись приветствия")
    btn = photo_kw["reply_markup"].inline_keyboard[0][0]
    check(btn.web_app.url == "https://app.nablabel.ru", "кнопка открывает Mini App")
    check(m.sent[1][2]["reply_markup"] is main.main_kb, "вторым сообщением — обычное меню")
    check(main.WELCOME_IMAGE.exists(), "картинка приветствия лежит в assets/welcome.jpg")

    # --- второй /start шлёт фото по file_id, без повторной загрузки ---
    m2 = FakeMessage()
    await main.cmd_start(m2, FakeState())
    check(m2.sent[0][1][0] == "BIG_FILE_ID", "повторно — по file_id")

    # --- запуск: профиль бота, команды, кнопка меню ---
    await main.on_startup()
    names = [c[0] for c in main.bot.calls]
    for need in ("get_me", "set_my_short_description", "set_my_description", "set_my_commands", "set_chat_menu_button"):
        check(need in names, f"при старте вызван {need}")
    short = dict(main.bot.calls)["set_my_short_description"]["short_description"]
    check(len(short) <= 120, "«о боте» не длиннее 120 символов")
    check(main.BOT_IDENTITY.get("username") == "sprosinab_bot", "getMe сохранён для API")

    # --- выключатель SET_BOT_PROFILE=0 ---
    main.config.SET_BOT_PROFILE = False
    main.bot.calls.clear()
    await main.on_startup()
    check("set_my_description" not in [c[0] for c in main.bot.calls], "SET_BOT_PROFILE=0 не трогает профиль")

    # --- аналитика подключена и не хранит Telegram ID в открытом виде ---
    check(any(type(m).__name__ == "AnalyticsMiddleware" for m in main.dp.middlewares), "middleware аналитики подключён")
    main.track(777, "report_sent", "question")
    import sqlite3
    rows = sqlite3.connect(main.config.ANALYTICS_DB).execute("SELECT user_hash, event FROM events").fetchall()
    check(rows and rows[-1][1] == "report_sent" and "777" not in rows[-1][0], "событие записано, ID захеширован")

    db = ROOT / "users_nab.db"
    print(f"✅ bot: {ok}/{ok} проверок пройдено")
    return db


if __name__ == "__main__":
    existed = (ROOT / "users_nab.db").exists()
    existed_stats = (ROOT / "analytics.db").exists()
    db = asyncio.run(run())
    if not existed and db.exists():
        db.unlink()                    # не оставляем тестовую базу в проекте
    if not existed_stats and (ROOT / "analytics.db").exists():
        (ROOT / "analytics.db").unlink()

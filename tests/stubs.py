"""
Заглушки aiogram и поддельный бот для тестов без интернета и без установленных пакетов.

FakeBot запоминает все вызовы Telegram и возвращает правдоподобные ответы
(message_id растёт, у фото есть file_id), поэтому логику можно проверять целиком.
"""
from __future__ import annotations

import itertools
import sys
import types as pytypes


class Obj:
    def __init__(self, *a, **k):
        self.args, self.kwargs = a, k
        for key, val in k.items():
            setattr(self, key, val)

    def __repr__(self):
        return f"{type(self).__name__}({self.kwargs})"


class Magic:
    """F.text == ..., F.data.startswith(...), & | ~ — всё возвращает себя."""
    def __getattr__(self, _):
        return self

    def __call__(self, *a, **k):
        return self

    __eq__ = __ne__ = __and__ = __or__ = __invert__ = lambda self, *a: self  # noqa: E731
    __hash__ = object.__hash__


class Dispatcher:
    def __init__(self, *a, **k):
        self.handlers = []

    def message(self, *a, **k):
        def deco(f):
            self.handlers.append(("message", f))
            return f
        return deco

    def callback_query(self, *a, **k):
        def deco(f):
            self.handlers.append(("callback", f))
            return f
        return deco

    def resolve_used_update_types(self):
        return []

    async def start_polling(self, *a, **k):
        return None


_ids = itertools.count(1000)


class FakeBot:
    """Поддельный Telegram-бот: записывает вызовы в self.calls."""
    def __init__(self, *a, fail_chats=(), **k):
        self.calls: list[tuple[str, dict]] = []
        self.fail_chats = set(fail_chats)
        self.id = 42

    def of(self, name: str) -> list[dict]:
        return [k for n, k in self.calls if n == name]

    def __getattr__(self, name):
        async def call(*a, **k):
            if a:
                k = dict(k, _args=a)
            self.calls.append((name, k))
            chat = k.get("chat_id", a[0] if a else 0)
            if chat in self.fail_chats:
                raise RuntimeError("Forbidden: bot was blocked by the user")
            if name == "get_me":
                return Obj(id=42, username="sprosinab_bot", full_name="Спроси ССт НАБ")
            if name == "send_media_group":
                return [Obj(message_id=next(_ids), chat=Obj(id=chat), photo=[Obj(file_id=f"PH{i}")]) for i, _ in enumerate(k["media"])]
            if name in ("send_message", "send_photo", "send_document", "copy_message"):
                return Obj(message_id=next(_ids), chat=Obj(id=chat), photo=[Obj(file_id="PHOTO_ID")])
            return True
        call.__name__ = name
        return call


def install() -> None:
    if "aiogram" in sys.modules and getattr(sys.modules["aiogram"], "_is_stub", False):
        return
    ag = pytypes.ModuleType("aiogram")
    ag._is_stub = True
    ag.Bot = FakeBot
    ag.Dispatcher = Dispatcher
    ag.F = Magic()
    t = pytypes.ModuleType("aiogram.types")
    for n in ("BotCommand", "FSInputFile", "InlineKeyboardButton", "InlineKeyboardMarkup", "KeyboardButton",
              "MenuButtonWebApp", "ReplyKeyboardMarkup", "WebAppInfo", "BufferedInputFile", "InputMediaPhoto",
              "Message", "CallbackQuery", "User", "ForceReply", "ReactionTypeEmoji"):
        setattr(t, n, type(n, (Obj,), {}))
    ag.types = t
    mods = {"aiogram": ag, "aiogram.types": t}
    for name in ("aiogram.client", "aiogram.client.default", "aiogram.enums", "aiogram.filters", "aiogram.fsm",
                 "aiogram.fsm.context", "aiogram.fsm.state"):
        mods[name] = pytypes.ModuleType(name)
    mods["aiogram.client.default"].DefaultBotProperties = Obj
    mods["aiogram.enums"].ParseMode = Obj(HTML="HTML")
    mods["aiogram.filters"].Command = Obj
    mods["aiogram.filters"].StateFilter = Obj
    mods["aiogram.fsm.context"].FSMContext = Obj
    mods["aiogram.fsm.state"].State = Obj
    mods["aiogram.fsm.state"].StatesGroup = type("StatesGroup", (), {})
    mods["aiogram.fsm.state"].any_state = object()
    sys.modules.update(mods)


def kb_buttons(markup) -> list[dict]:
    """Все кнопки клавиатуры: [{'text':..., 'callback_data':...}]."""
    out = []
    for row in getattr(markup, "inline_keyboard", []) or []:
        for b in row:
            out.append(b.kwargs)
    return out

"""Клавиатура главного меню бота (общая для бота и API)."""
from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

import config

B_NPB, B_FAQ = "📚 НПБ", "📖 FAQ"
B_GROUPS, B_CONTACTS = "📌 Важные группы", "📞 Наши Контакты"
B_QUESTION, B_INITIATIVE = "❓ Вопросы", "💡 Инициатива"
B_REPAIR, B_PANIC = "🛠 Поломки", "🚨 Тревожная кнопка"
B_MAP, B_EVENTS = "🗺 Карта корпуса", "📅 Анонсы"
B_MY = "🗂 Мои обращения"
B_APP = "🌐 Приложение"

_KB = None


def main_kb() -> ReplyKeyboardMarkup:
    global _KB
    if _KB is None:
        rows = [
            [KeyboardButton(text=B_NPB), KeyboardButton(text=B_FAQ)],
            [KeyboardButton(text=B_GROUPS), KeyboardButton(text=B_CONTACTS)],
            [KeyboardButton(text=B_QUESTION), KeyboardButton(text=B_INITIATIVE)],
            [KeyboardButton(text=B_REPAIR), KeyboardButton(text=B_PANIC)],
            [KeyboardButton(text=B_MAP), KeyboardButton(text=B_EVENTS)],
            [KeyboardButton(text=B_MY)],
        ]
        if config.WEBAPP_URL:
            rows.append([KeyboardButton(text=B_APP, web_app=WebAppInfo(url=config.WEBAPP_URL))])
        _KB = ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
    return _KB

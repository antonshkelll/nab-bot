"""
Типы обращений и то, как они выглядят в рабочем чате руководства.

Используется и ботом (main.py), и приложением (api.py), поэтому обращение
из чата и из приложения в рабочей группе выглядит одинаково.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tgutil import esc


@dataclass(frozen=True)
class ReportKind:
    code: str
    icon: str
    title: str
    word: str            # «вопрос», «инициатива» — в текстах автору
    tag: str             # хештег в группе
    body_label: str
    user_ack: str


KINDS: dict[str, ReportKind] = {
    "question": ReportKind(
        "question", "❓", "ВОПРОС", "вопрос", "#вопрос", "Вопрос",
        "✅ Твой вопрос успешно передан руководству ССт НАБ. Ответ придёт прямо в этот чат!",
    ),
    "initiative": ReportKind(
        "initiative", "💡", "ИНИЦИАТИВА", "инициатива", "#инициатива", "Идея",
        "✅ Твоя инициатива успешно передана руководству ССт НАБ. Спасибо!",
    ),
    "panic": ReportKind(
        "panic", "🚨", "ТРЕВОЖНАЯ КНОПКА · SOS", "сигнал SOS", "#sos", "Проблема",
        "✅ Сигнал SOS передан руководству ССт НАБ. Постараемся помочь в ближайшее время!",
    ),
    "repair": ReportKind(
        "repair", "🛠", "ПОЛОМКА", "сообщение о поломке", "#поломка", "Описание",
        "✅ <b>Твоё обращение принято!</b>\n\nМы передали информацию о поломке руководству ССт НАБ "
        "и начальнику корпуса. Неисправность постараемся устранить в ближайшее время 🛠",
    ),
}

STATUS_TITLE = {"new": "передано руководству", "work": "в работе", "done": "решено"}


def display_name(username: Optional[str], first_name: Optional[str], is_anon: bool) -> str:
    """Имя отправителя для рабочего чата. Telegram ID никогда не показывается."""
    if is_anon:
        return "Анонимно 🕵️‍♂️"
    if username:
        return f"@{username}"
    return first_name or "Пользователь"


def group_text(r) -> str:
    """Карточка обращения в рабочем чате (r — строка таблицы reports)."""
    k = KINDS.get(r["kind"], KINDS["question"])
    lines = [f"{k.icon} <b>{k.title}</b> · №{r['id']} · {k.tag}"]
    if r["anon"]:
        lines.append("🕵️ <b>От:</b> анонимно" + (" — ответ дойдёт через бота, имя скрыто и от вас" if r["anon_ref"] else ""))
    else:
        lines.append(f"👤 <b>От:</b> {esc(r['user_name'] or 'Пользователь')}")
    if r["location"]:
        lines.append(f"📍 <b>Где:</b> {esc(r['location'])}")
    if r["photos"]:
        lines.append(f"📎 Фото: {r['photos']} (выше)")
    lines.append(f"\n<blockquote>{esc(r['text'] or '—')}</blockquote>")
    lines.append("<i>Ответить автору — Reply на это сообщение или кнопка «Ответить» ниже.</i>")
    return "\n".join(lines)


def what(r, cap: bool = True) -> str:
    """«Твой вопрос №12» — для сообщений автору."""
    k = KINDS.get(r["kind"], KINDS["question"])
    word = {"question": "Твой вопрос", "initiative": "Твоя инициатива", "panic": "Твой сигнал SOS",
            "repair": "Твоё сообщение о поломке"}.get(r["kind"], "Твоё обращение")
    return f"{word} №{r['id']}" if cap else f"{k.word} №{r['id']}"

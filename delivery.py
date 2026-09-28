"""
Общая логика доставки обращений в суперчат.

Используется и обычным ботом (main.py), и веб-приложением (webapi.py),
чтобы формат сообщений в топике был абсолютно одинаковым и админы
могли отвечать через Reply в обоих случаях.
"""
from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from typing import Optional

from aiogram import Bot
from aiogram.types import BufferedInputFile, InputMediaPhoto

import config

log = logging.getLogger("sprosinab.delivery")

# --- Описание каждого типа обращения -------------------------------------

@dataclass(frozen=True)
class ReportKind:
    code: str
    icon: str
    title: str
    body_label: str
    user_ack: str
    replyable: bool = False  # можно ли ответить пользователю через Reply


KINDS: dict[str, ReportKind] = {
    "question": ReportKind(
        code="question",
        icon="❓",
        title="НОВЫЙ ВОПРОС ОТ СТУДЕНТА",
        body_label="Текст",
        user_ack="✅ Твой вопрос успешно передан руководству ССт НАБ. Ожидай ответ прямо в этом диалоге!",
        replyable=True,
    ),
    "initiative": ReportKind(
        code="initiative",
        icon="💡",
        title="НОВАЯ ИНИЦИАТИВА / ЗАПРОС",
        body_label="Текст",
        user_ack="✅ Твоя инициатива успешно передана руководству ССт НАБ. Спасибо!",
    ),
    "panic": ReportKind(
        code="panic",
        icon="🚨",
        title="ТРЕВОЖНАЯ КНОПКА (SOS)",
        body_label="Проблема",
        user_ack="✅ Сигнал SOS передан руководству ССт НАБ. Постараемся помочь в ближайшее время!",
    ),
    "repair": ReportKind(
        code="repair",
        icon="🛠",
        title="СООБЩЕНИЕ О ПОЛОМКЕ",
        body_label="Описание",
        user_ack=(
            "✅ <b>Твоё обращение принято!</b>\n\n"
            "Мы передали информацию о поломке руководству ССт НАБ и начальнику корпуса. "
            "Неисправность постараемся устранить в ближайшее время 🛠\n\n"
            "Спасибо, что помогаешь делать университет лучше!"
        ),
    ),
}


@dataclass
class Report:
    """Одно обращение пользователя."""
    kind: str
    text: str
    user_id: int
    sender_name: str                      # как показывать отправителя в топике
    location: str = ""                    # только для поломок: корпус / аудитория
    photos: list[bytes] = field(default_factory=list)
    photo_file_ids: list[str] = field(default_factory=list)


# --- Вспомогательные функции ---------------------------------------------

def display_name(username: Optional[str], first_name: Optional[str], is_anon: bool) -> str:
    """Имя отправителя для топика. ID пользователя никогда не показывается."""
    if is_anon:
        return "Анонимно 🕵️‍♂️"
    if username:
        return f"@{username}"
    return first_name or "Пользователь"


def _hidden_link(user_id: int) -> str:
    """Скрытая ссылка в смайлике — через неё админ-хендлер достаёт ID для ответа."""
    return f"https://t.me/{config.BOT_USERNAME}?start={user_id}"


def build_caption(report: Report) -> str:
    kind = KINDS[report.kind]
    safe_text = html.escape(report.text or "—")
    safe_sender = html.escape(report.sender_name)

    if kind.replyable:
        head = f"<a href='{_hidden_link(report.user_id)}'>{kind.icon}</a> <b>{kind.title}</b>"
    else:
        head = f"{kind.icon} <b>{kind.title}</b>"

    lines = [
        head,
        f"<b>Отправитель:</b> {safe_sender}",
        "-----------------------------------",
    ]
    if report.location:
        lines.append(f"📍 <b>Где:</b> {html.escape(report.location)}")
    lines.append(f"💬 <b>{kind.body_label}:</b> {safe_text}")

    if kind.replyable:
        lines.append("")
        lines.append("<i>💡 Чтобы ответить пользователю, просто сделайте Reply (Ответить) на это сообщение!</i>")

    return "\n".join(lines)


def _media_sources(report: Report) -> list:
    """Фото обращения: сырые байты из приложения плюс file_id из бота."""
    out: list = []
    for idx, raw in enumerate(report.photos[: config.MAX_PHOTOS]):
        out.append(BufferedInputFile(raw, filename=f"photo_{idx + 1}.jpg"))
    out.extend(report.photo_file_ids[: config.MAX_PHOTOS - len(out)])
    return out


async def notify_admins(bot: Bot, report: Report, file_ids: list[str] | None = None) -> int:
    """
    Дублирует обращение в личку каждому админу из ADMIN_IDS — чтобы обращение
    было видно, даже если человек не сидит в суперчате. Админ может ответить
    студенту прямо отсюда обычным Reply.

    Возвращает, скольким доставлено. Ошибки не роняют основную отправку:
    админ мог не запускать бота или заблокировать его.
    """
    if not config.NOTIFY_ADMINS_DM or not config.ADMIN_IDS:
        return 0

    caption = build_caption(report)
    media_sources: list = list(file_ids or []) or _media_sources(report)

    delivered = 0
    for admin_id in config.ADMIN_IDS:
        try:
            if media_sources:
                media = [
                    InputMediaPhoto(
                        media=src,
                        caption=caption if i == 0 else None,
                        parse_mode="HTML" if i == 0 else None,
                    )
                    for i, src in enumerate(media_sources)
                ]
                await bot.send_media_group(chat_id=admin_id, media=media)
            else:
                await bot.send_message(chat_id=admin_id, text=caption, parse_mode="HTML")
            delivered += 1
        except Exception:  # noqa: BLE001
            log.warning("Копия обращения не доставлена админу %s", admin_id)
    return delivered


async def deliver(bot: Bot, report: Report) -> list[str]:
    """
    Отправляет обращение в суперчат: одним сообщением или альбомом с фото.
    Возвращает file_id отправленных фото — копии админам уходят уже по ним,
    без повторной загрузки тех же байтов.
    """
    caption = build_caption(report)
    media_sources = _media_sources(report)

    if not media_sources:
        await bot.send_message(
            chat_id=config.SUPER_CHAT_ID,
            message_thread_id=config.TOPIC_ID,
            text=caption,
            parse_mode="HTML",
        )
        return []

    # Подпись с ID-ссылкой всегда на первом фото — на него админы и отвечают.
    media = [
        InputMediaPhoto(media=src, caption=caption if i == 0 else None, parse_mode="HTML" if i == 0 else None)
        for i, src in enumerate(media_sources)
    ]
    sent = await bot.send_media_group(
        chat_id=config.SUPER_CHAT_ID,
        message_thread_id=config.TOPIC_ID,
        media=media,
    )
    return [m.photo[-1].file_id for m in sent if getattr(m, "photo", None)]

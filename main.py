"""
Бот «Спроси ССт НАБ» — @sprosinab_bot
Студенческий совет факультета «Налоги, аудит и бизнес-анализ», Финансовый университет.

Разделы: НПБ, FAQ, карта корпуса, важные группы, контакты, вопросы руководству
(можно анонимно), инициативы, поломки с фото, тревожная кнопка, анонсы с явкой,
опросы и «Мои обращения».

Обращения приходят в рабочий чат с кнопками «Взять в работу / Решено / Ответить автору».
Ответ уходит автору от имени бота — даже анонимному. После «Решено» автор ставит
оценку 1–5 и может написать отзыв. Всё остальное руководство меняет в Пульте приложения.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import defaultdict

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup, any_state
from aiogram.types import (BotCommand, FSInputFile, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp,
                           ReactionTypeEmoji, WebAppInfo)

import config
import content
import reports
import stats
import store
from delivery import KINDS, display_name
from keyboards import (B_APP, B_CONTACTS, B_EVENTS, B_FAQ, B_GROUPS, B_INITIATIVE, B_MAP, B_MY, B_NPB, B_PANIC,
                       B_QUESTION, B_REPAIR, main_kb)
from tgutil import btn, esc, ikb, safe, send, short, stars

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("sprosinab")

if not config.TOKEN:
    raise SystemExit("❌ Не задан BOT_TOKEN. Скопируйте .env.example в .env и впишите токен из @BotFather.")

bot = Bot(token=config.TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
ADMINS = set(config.ADMIN_IDS)


def is_admin(uid: int) -> bool:
    return int(uid) in ADMINS


def person(u: types.User) -> str:
    name = (u.first_name or "админ").strip()
    if u.username:
        name += " @" + u.username
    return name[:60]


# === СОСТОЯНИЯ ===
class Form(StatesGroup):
    waiting_for_initiative_text = State()
    waiting_for_panic_text = State()
    waiting_for_broadcast = State()
    waiting_for_question_text = State()
    waiting_for_repair = State()
    waiting_user_reply = State()      # ответ автора руководству по №
    waiting_review = State()          # отзыв словами после оценки


anon_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🕵️‍♂️ Анонимно", callback_data="anon_yes")],
    [InlineKeyboardButton(text="👤 С профилем", callback_data="anon_no")],
])


def webapp_kb(path: str = "", text: str = "🌐 Открыть в приложении") -> InlineKeyboardMarkup | None:
    if not config.WEBAPP_URL:
        return None
    return ikb([[btn(text, app=path)]])


# Приветственная картинка. После первой отправки Telegram отдаёт file_id — дальше шлём по нему.
WELCOME_IMAGE = config.BASE_DIR / "webapp" / "assets" / "brand" / "welcome.jpg"
_welcome_file_id: str | None = None

WELCOME_TEXT = (
    "👋 <b>Привет! Это «Спроси ССт НАБ»</b> — официальный бот-помощник "
    "Студенческого совета факультета НАБ.\n\n"
    "📚 Документы и FAQ, ❓ вопросы руководству, 💡 инициативы, "
    "🛠 поломки, 🚨 тревожная кнопка, 📅 анонсы — всё здесь.\n\n"
    "✨ Открой <b>приложение</b> — там всё то же самое, только красивее. "
    "Или выбирай раздел в меню ниже 👇\n\n"
    "<i>Они были теми, в ком вечно нуждались — Налоги, Аудит и Бизнес-анализ</i> 💚"
)


async def send_welcome(message: types.Message) -> None:
    global _welcome_file_id
    kb = webapp_kb(text="✨ Открыть приложение")
    photo = _welcome_file_id or (FSInputFile(WELCOME_IMAGE) if WELCOME_IMAGE.exists() else None)
    if photo is not None:
        try:
            sent = await message.answer_photo(photo, caption=WELCOME_TEXT, reply_markup=kb)
            if sent.photo:
                _welcome_file_id = sent.photo[-1].file_id
            await message.answer("Меню разделов — внизу 👇", reply_markup=main_kb())
            return
        except Exception:  # noqa: BLE001
            log.exception("Не удалось отправить приветственное фото — шлю текстом")
    await message.answer(WELCOME_TEXT, reply_markup=kb or main_kb())
    if kb is not None:
        await message.answer("Меню разделов — внизу 👇", reply_markup=main_kb())


def track(message_or_cb, key: str) -> None:
    try:
        store.hit("bot", key)
        u = message_or_cb.from_user
        store.seen(u.id)
    except Exception:  # noqa: BLE001
        pass


def extract_hidden_user_id(message: types.Message) -> int | None:
    """Старые обращения (до v3) хранили ID в невидимой ссылке — оставляем совместимость."""
    entities = (message.entities or []) + (message.caption_entities or [])
    for entity in entities:
        if entity.type == "text_link" and entity.url and "start=" in entity.url:
            try:
                return int(entity.url.split("start=")[1])
            except (IndexError, ValueError):
                continue
    return None


# =======================================================
# БЛОК 1: КОМАНДЫ И МЕНЮ
# =======================================================

@dp.message(Command("start"), StateFilter(any_state))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    u = message.from_user
    store.touch_user(u.id, u.first_name or "", u.username or "", started=True)
    track(message, "start")
    arg = (message.text or "").partition(" ")[2].strip()
    if arg.startswith("r") and arg[1:].isdigit():          # ссылка «написать по обращению»
        return await my_reports(message, state)
    await send_welcome(message)


@dp.message(Command("app"), StateFilter(any_state))
async def cmd_app(message: types.Message, state: FSMContext):
    await state.clear()
    kb = webapp_kb()
    if kb is None:
        await message.answer("⚠️ Веб-приложение пока не настроено.")
        return
    await message.answer("🌐 <b>Спроси ССт НАБ — приложение</b>\n\nВсе разделы бота в одном окне 👇", reply_markup=kb)


@dp.message(F.text == "⬅️ Назад", StateFilter(any_state))
async def cmd_back(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Вы вернулись в главное меню.", reply_markup=main_kb())


# --- НПБ ---
def npb_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{d['id']}. {d['title']}"[:64], callback_data=f"npb_{d['id']}")] for d in content.docs()])


@dp.message(F.text == B_NPB, StateFilter(any_state))
async def send_npb_menu(message: types.Message, state: FSMContext):
    await state.clear()
    track(message, "npb")
    await message.answer(
        "🏛 <b>Нормативно-правовая база (НПБ)</b>\n\n"
        "Здесь собраны ключевые документы Финансового университета и Студенческого совета НАБ. "
        "Выберите документ — я моментально отправлю его 📄👇",
        reply_markup=npb_kb(),
    )


@dp.callback_query(F.data.startswith("npb_"), StateFilter(any_state))
async def process_npb_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    paths = content.doc_files(callback.data.split("_", 1)[1])
    for p in paths:
        await callback.message.answer_document(FSInputFile(p))
    if not paths:
        await callback.message.answer("⚠️ Файл пока не загружен на сервер.")
    await callback.answer()


# --- FAQ ---
@dp.message(F.text.in_({B_FAQ, "❓ FAQ", "📖 Часто задаваемые вопросы"}), StateFilter(any_state))
async def send_faq(message: types.Message, state: FSMContext):
    await state.clear()
    track(message, "faq")
    if not config.FAQ_FILE.exists():
        await message.answer("⚠️ Файл FAQ пока не загружен на сервер (docs/FAQ.pdf).")
        return
    await message.answer(
        "📖 <b>FAQ — часто задаваемые вопросы</b>\n\n"
        "Собрали ответы на всё, что чаще всего спрашивают первокурсники: учёба, сессия, "
        "преподаватели, общежитие, личный кабинет, стипендия и документы.\n\nЛистай файл ниже 👇",
        reply_markup=webapp_kb("#/faq"),
    )
    await message.answer_document(FSInputFile(config.FAQ_FILE, filename="FAQ — Часто задаваемые вопросы (НАБ).pdf"),
                                  caption="📚 FAQ — часто задаваемые вопросы | ССт НАБ")


# --- КАРТА ---
@dp.message(F.text.in_({B_MAP, "🗺️ Карта корпуса", "Карта корпуса"}), StateFilter(any_state))
async def send_map(message: types.Message, state: FSMContext):
    await state.clear()
    track(message, "map")
    await message.answer(
        "🗺 <b>Карта корпуса — Верхняя Масловка, 15</b>\n\n"
        "Все этажи главного корпуса и компьютерного корпуса 15-1. "
        "В приложении можно вписать номер аудитории — покажем этаж и точку на плане 📍",
        reply_markup=webapp_kb("#/map"),
    )
    if config.MAP_FILE.exists():
        await message.answer_document(FSInputFile(config.MAP_FILE, filename="Карта корпуса — Верхняя Масловка 15.pdf"),
                                      caption="🗺 Карта корпуса | ССт НАБ")


# --- КОНТАКТЫ (редактируются в Пульте) ---
@dp.message(F.text == B_CONTACTS, StateFilter(any_state))
async def send_contacts(message: types.Message, state: FSMContext):
    await state.clear()
    track(message, "contacts")
    lines = ["📞 <b>Контакты руководства ССт НАБ:</b>"]
    for i, p in enumerate(content.people(), 1):
        lines.append(f"\n <b>{i}. {esc(p['role'])}</b>\n👤 {esc(p['name'])}")
        extra = " | ".join(x for x in (esc(p.get("tel") or ""), ("@" + esc(p["tg"])) if p.get("tg") else "") if x)
        if extra:
            lines.append(f"📞 {extra}")
    await message.answer("\n".join(lines))


# --- ВАЖНЫЕ ГРУППЫ (редактируются в Пульте) ---
@dp.message(F.text == B_GROUPS, StateFilter(any_state))
async def send_groups_menu(message: types.Message, state: FSMContext):
    await state.clear()
    track(message, "groups")
    rows = [[btn(f"{g['t']}", url=g["u"])] for g in content.links() if g.get("u", "").startswith(("http", "tg"))]
    await message.answer("📌 <b>Важные группы</b>\n\nВсе официальные ресурсы факультета и Студсовета — подписывайся 👇",
                         reply_markup=ikb(rows) if rows else None)


# --- ВОПРОСЫ / ИНИЦИАТИВА / SOS / ПОЛОМКИ ---
@dp.message(F.text == B_QUESTION, StateFilter(any_state))
async def question_start(message: types.Message, state: FSMContext):
    await state.clear()
    track(message, "question")
    await message.answer(
        "❓ <b>Задать вопрос руководителям студенческого совета НАБ</b>\n\n"
        "Здесь ты можешь задать любой вопрос руководству ССт НАБ. Ответ придёт прямо сюда — "
        "даже если вопрос анонимный.\n\nКак ты хочешь отправить свой вопрос?",
        reply_markup=anon_kb,
    )
    await state.update_data(action_type="question")


@dp.message(F.text == B_INITIATIVE, StateFilter(any_state))
async def init_start(message: types.Message, state: FSMContext):
    await state.clear()
    track(message, "initiative")
    await message.answer("Как ты хочешь отправить свою инициативу/запрос?", reply_markup=anon_kb)
    await state.update_data(action_type="initiative")


@dp.message(F.text == B_PANIC, StateFilter(any_state))
async def panic_start(message: types.Message, state: FSMContext):
    await state.clear()
    track(message, "panic")
    await message.answer("Как ты хочешь отправить тревожный сигнал?", reply_markup=anon_kb)
    await state.update_data(action_type="panic")


@dp.message(F.text.in_({B_REPAIR, "🔧 Поломки"}), StateFilter(any_state))
async def repair_start(message: types.Message, state: FSMContext):
    await state.clear()
    track(message, "repair")
    await message.answer(
        "🛠 <b>Сообщить о поломке</b>\n\nСломалась розетка, не работает дверь, потекла батарея, разбито стекло? "
        "Пришли фото — мы передадим информацию начальнику корпуса.\n\nКак ты хочешь отправить обращение?",
        reply_markup=anon_kb,
    )
    await state.update_data(action_type="repair")


NEXT_STATE = {
    "initiative": (Form.waiting_for_initiative_text, "📝 Напиши свою инициативу/запрос в одном сообщении:"),
    "panic": (Form.waiting_for_panic_text, "🚨 Опиши свою проблему для тревожной кнопки в одном сообщении:"),
    "question": (Form.waiting_for_question_text, "📝 Напиши свой вопрос в одном сообщении:"),
    "repair": (Form.waiting_for_repair,
               "📸 Пришли <b>фото поломки</b> (можно несколько сразу) и в подписи к нему укажи, "
               "<b>что и где сломалось</b> — корпус и аудиторию.\n\nЕсли фото нет — просто опиши проблему текстом."),
}


@dp.callback_query(F.data.in_({"anon_yes", "anon_no"}), StateFilter(any_state))
async def process_anon_choice(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(is_anon=callback.data == "anon_yes")
    data = await state.get_data()
    await safe(callback.message.delete())
    target = NEXT_STATE.get(data.get("action_type"))
    if target:
        await callback.message.answer(target[1])
        await state.set_state(target[0])
    await callback.answer()


def _user_dict(u: types.User) -> dict:
    return {"id": u.id, "first_name": u.first_name or "", "username": u.username or ""}


async def _finish_report(message: types.Message, state: FSMContext, kind: str, text: str,
                         photo_ids: list[str] | None = None) -> None:
    data = await state.get_data()
    anon = bool(data.get("is_anon"))
    await state.clear()
    store.touch_user(message.from_user.id, message.from_user.first_name or "", message.from_user.username or "", started=True)
    good, rid, msg = await reports.create(bot, kind, _user_dict(message.from_user), text, anon=anon,
                                          photo_ids=photo_ids or [], source="bot")
    if good:
        await message.answer(reports.ack_text(kind, rid, anon), reply_markup=main_kb())
    else:
        await message.answer(f"⚠️ {esc(msg)}", reply_markup=main_kb())


@dp.message(StateFilter(Form.waiting_for_question_text))
async def question_process(message: types.Message, state: FSMContext):
    await _finish_report(message, state, "question", message.text or message.caption or "")


@dp.message(StateFilter(Form.waiting_for_initiative_text))
async def init_process(message: types.Message, state: FSMContext):
    await _finish_report(message, state, "initiative", message.text or message.caption or "")


@dp.message(StateFilter(Form.waiting_for_panic_text))
async def panic_process(message: types.Message, state: FSMContext):
    await _finish_report(message, state, "panic", message.text or message.caption or "")


# --- ПОЛОМКИ: альбом из нескольких фото приходит по частям — собираем ---
_album_buffer: dict[str, list[types.Message]] = defaultdict(list)
_album_tasks: dict[str, asyncio.Task] = {}


@dp.message(StateFilter(Form.waiting_for_repair))
async def repair_process(message: types.Message, state: FSMContext):
    if not message.photo and not (message.text or message.caption):
        await message.answer("⚠️ Пришли, пожалуйста, фото поломки или опиши проблему текстом.")
        return
    if message.media_group_id:
        key = f"{message.from_user.id}:{message.media_group_id}"
        _album_buffer[key].append(message)
        if key not in _album_tasks:
            _album_tasks[key] = asyncio.create_task(_flush_album(key, state))
        return
    await _send_repair(state, [message])


async def _flush_album(key: str, state: FSMContext) -> None:
    await asyncio.sleep(1.5)
    messages = sorted(_album_buffer.pop(key, []), key=lambda m: m.message_id)
    _album_tasks.pop(key, None)
    if messages:
        await _send_repair(state, messages)


async def _send_repair(state: FSMContext, messages: list[types.Message]) -> None:
    text = next((m.caption or m.text for m in messages if (m.caption or m.text)), "")
    file_ids = [m.photo[-1].file_id for m in messages if m.photo][: config.MAX_PHOTOS]
    await _finish_report(messages[0], state, "repair", text or "Без описания", file_ids)


# =======================================================
# БЛОК 2: МОИ ОБРАЩЕНИЯ, ОТВЕТЫ И ОЦЕНКИ (личный чат)
# =======================================================

@dp.message(F.text == B_MY, StateFilter(any_state))
async def my_reports(message: types.Message, state: FSMContext):
    await state.clear()
    track(message, "my")
    rows = reports.mine(message.from_user.id, 10)
    if not rows:
        await message.answer("🗂 Пока нет обращений.\n<i>Анонимные тоже появятся здесь — их видишь только ты.</i>",
                             reply_markup=main_kb())
        return
    t = ["🗂 <b>Мои обращения</b>", ""]
    kb = []
    icon = {"new": "📨", "work": "⏳", "done": "✅"}
    for r in rows:
        k = KINDS.get(r["kind"], KINDS["question"])
        st = r["status"] or "new"
        n = store.count("SELECT COUNT(*) FROM report_msgs WHERE report_id=? AND author IN ('team','user')", (r["id"],))
        t.append(f"{k.icon} <b>№{r['id']}</b> · {icon.get(st, '')} {reports.STATUS_TITLE.get(st, '')}"
                 + (" · 🕵️" if r["anon"] else "") + (f" · 💬 {n}" if n else "")
                 + (f" · {stars(r['rating'])}" if r["rating"] else "")
                 + f"\n<i>{esc(short(r['text'], 90))}</i>")
        if reports.author_of(r) and st != "done" and len(kb) < 5:
            kb.append([btn(f"↩️ Написать по №{r['id']}", f"ur:{r['id']}")])
        elif st == "done" and not r["rating"] and len(kb) < 5:
            kb.append([btn(f"⭐ Оценить №{r['id']}", f"rq:{r['id']}")])
    kb.append([btn("🗂 Вся переписка в приложении", app="#/my")])
    reports.mark_seen(message.from_user.id)
    await message.answer("\n".join(t), reply_markup=ikb(kb))


@dp.callback_query(F.data.startswith("ur:"), StateFilter(any_state))
async def cb_user_reply_start(callback: types.CallbackQuery, state: FSMContext):
    r = reports.get(callback.data[3:])
    if not r or not reports.is_author(r, callback.from_user.id):
        await callback.answer("Обращение не найдено", show_alert=True)
        return
    await state.set_state(Form.waiting_user_reply)
    await state.update_data(rid=r["id"])
    await callback.answer()
    await callback.message.answer(f"✍️ Напиши ответ руководству по №{r['id']} одним сообщением."
                                  + (" Анонимность сохраняется." if r["anon"] else ""))


@dp.message(StateFilter(Form.waiting_user_reply))
async def user_reply_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    good, msg = await reports.user_reply(bot, data.get("rid"), message.from_user.id, message.text or message.caption or "")
    await message.answer("✅ Отправлено руководству ССт НАБ. Ответ придёт сюда." if good else f"⚠️ {esc(msg)}", reply_markup=main_kb())


@dp.callback_query(F.data.startswith("rq:"), StateFilter(any_state))
async def cb_rate_ask(callback: types.CallbackQuery, state: FSMContext):
    r = reports.get(callback.data[3:])
    if not r or not reports.is_author(r, callback.from_user.id):
        await callback.answer("Обращение не найдено", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(f"⭐ Как тебе решение по №{r['id']}? Оцени от 1 до 5:", reply_markup=reports.rate_kb(r["id"]))


@dp.callback_query(F.data.startswith("rate:"), StateFilter(any_state))
async def cb_rate(callback: types.CallbackQuery, state: FSMContext):
    try:
        _, rid, value = callback.data.split(":")
        rid, value = int(rid), int(value)
    except ValueError:
        await callback.answer()
        return
    good, msg = await reports.rate(bot, rid, callback.from_user.id, value)
    if not good:
        await callback.answer(msg, show_alert=True)
        return
    await callback.answer("Спасибо за оценку ✦")
    track(callback, f"rate{value}")
    text = (callback.message.html_text if getattr(callback.message, "html_text", None) else "") or ""
    text = re.sub(r"\n\nПомогло\?.*$", "", text, flags=re.S)
    await safe(callback.message.edit_text(
        f"{text}\n\nТвоя оценка: <b>{stars(value)}</b>\nСпасибо! Хочешь добавить пару слов?",
        reply_markup=ikb([[btn("✍️ Написать отзыв", f"rv:{rid}"), btn("Не нужно", f"rvx:{rid}")]])))


@dp.callback_query(F.data.startswith("rv:"), StateFilter(any_state))
async def cb_review_start(callback: types.CallbackQuery, state: FSMContext):
    r = reports.get(callback.data[3:])
    if not r or not reports.is_author(r, callback.from_user.id):
        await callback.answer("Обращение не найдено", show_alert=True)
        return
    rid = r["id"]
    await state.set_state(Form.waiting_review)
    await state.update_data(rid=rid)
    await callback.answer()
    await safe(callback.message.edit_reply_markup(reply_markup=None))
    await callback.message.answer("✍️ Напиши отзыв одним сообщением — что понравилось, что можно улучшить.")


@dp.callback_query(F.data.startswith("rvx:"), StateFilter(any_state))
async def cb_review_skip(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Спасибо ✦")
    await safe(callback.message.edit_reply_markup(reply_markup=None))


@dp.message(StateFilter(Form.waiting_review))
async def review_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    good, msg = await reports.rate(bot, data.get("rid"), message.from_user.id, None, message.text or "")
    await message.answer("💚 Спасибо за отзыв! Мы его прочитаем." if good else f"⚠️ {esc(msg)}", reply_markup=main_kb())


# =======================================================
# БЛОК 3: АНОНСЫ И ОПРОСЫ
# =======================================================

@dp.message(F.text.in_({B_EVENTS, "📅 Анонсы и события"}), StateFilter(any_state))
async def events_menu(message: types.Message, state: FSMContext):
    await state.clear()
    track(message, "events")
    evs = [v for v in content.events_list(message.from_user.id, past_days=0)]
    if not evs:
        await message.answer("📅 <b>Анонсы</b>\nПока ничего не запланировано. Как только руководство добавит событие — "
                             "оно появится здесь.", reply_markup=webapp_kb("#/events"))
        return
    for v in evs[:5]:
        e = store.q("SELECT * FROM events WHERE id=?", (v["id"],), one=True)
        await message.answer(content.event_text(e, "📅"), reply_markup=content.event_kb(e, message.from_user.id))


@dp.callback_query(F.data.startswith("ev:"), StateFilter(any_state))
async def cb_event(callback: types.CallbackQuery, state: FSMContext):
    try:
        _, act, eid = callback.data.split(":")
        eid = int(eid)
    except ValueError:
        await callback.answer()
        return
    uid = callback.from_user.id
    u = callback.from_user
    if act in ("yes", "no"):
        name = f"{u.first_name or ''}" + (f" (@{u.username})" if u.username else "")
        cur = store.q("SELECT answer FROM event_rsvp WHERE event_id=? AND uid=?", (eid, uid), one=True)
        answer = "" if cur and cur["answer"] == act else act          # повторное нажатие снимает отметку
        good, msg = content.set_rsvp(eid, uid, answer, name.strip())
        if not good:
            await callback.answer(msg, show_alert=True)
            return
        await callback.answer({"yes": "Отметили: придёшь ✦ Напомним накануне", "no": "Отметили: не придёшь", "": "Отметка снята"}[answer])
    elif act == "sub":
        on = not store.q("SELECT 1 FROM event_subs WHERE event_id=? AND uid=?", (eid, uid), one=True)
        good, msg = content.set_sub(eid, uid, on)
        if not good:
            await callback.answer(msg, show_alert=True)
            return
        await callback.answer("Напомню за день и в день события ✦" if on else "Напоминание выключено")
    else:
        await callback.answer()
        return
    e = store.q("SELECT * FROM events WHERE id=?", (eid,), one=True)
    if e:
        await safe(callback.message.edit_text(content.event_text(e, "📅"), reply_markup=content.event_kb(e, uid)))


@dp.callback_query(F.data.startswith("pv:"), StateFilter(any_state))
async def cb_poll_vote(callback: types.CallbackQuery, state: FSMContext):
    try:
        _, pid, opt = callback.data.split(":")
        pid, opt = int(pid), int(opt)
    except ValueError:
        await callback.answer()
        return
    p = store.q("SELECT multi FROM polls WHERE id=?", (pid,), one=True)
    opts = [opt]
    if p and p["multi"]:                      # несколько вариантов: нажатие включает/выключает вариант
        cur = {r["opt"] for r in store.q("SELECT opt FROM poll_votes WHERE poll_id=? AND uid=?", (pid, callback.from_user.id))}
        cur ^= {opt}
        opts = sorted(cur)
    v, msg = content.poll_vote(pid, callback.from_user.id, opts)
    if not v:
        await callback.answer(msg, show_alert=True)
        return
    await callback.answer("Голос учтён ✦")
    await safe(callback.message.edit_text(content.poll_text(v), reply_markup=content.poll_kb(v)))


# =======================================================
# БЛОК 4: АДМИНИСТРАТОРЫ
# =======================================================

def admin_panel_text() -> str:
    d = stats.dashboard(7)
    r = d["reports"]
    return ("🎛 <b>Пульт ССт НАБ</b>\n\n"
            f"👥 Пользователей: <b>{d['users']['total']}</b> (+{d['users']['new7']} за неделю)\n"
            f"📈 Активны: сегодня <b>{d['active']['today']}</b> · за неделю <b>{d['active']['week']}</b> · за месяц <b>{d['active']['month']}</b>\n"
            f"📨 Открыто обращений: <b>{r['open']}</b> · без ответственного: <b>{r['unowned']}</b>"
            + (f" · просрочено: <b>{r['overdue']}</b>" if r["overdue"] else "") + "\n"
            + (f"⭐ Средняя оценка за неделю: <b>{r['avgRating']}</b>\n" if r["avgRating"] else "")
            + "\nНовости, анонсы, опросы, ссылки, контакты, документы и вся статистика — в Пульте приложения.")


def admin_kb():
    return ikb([[btn("🎛 Открыть Пульт", app="#/admin")],
                [btn("📋 Сводка в группу", "adm:digest"), btn("📤 Отчёт за месяц", "adm:export")],
                [btn("📣 Рассылка", "adm:send")]])


@dp.message(Command("admin"), StateFilter(any_state))
async def cmd_admin(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(admin_panel_text(), reply_markup=admin_kb())


@dp.callback_query(F.data.startswith("adm:"), StateFilter(any_state))
async def cb_admin(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    act = callback.data[4:]
    if act == "digest":
        good = await reports.send_digest(bot)
        await callback.answer("Сводка отправлена в группу ✦" if good else "Не получилось отправить в группу", show_alert=not good)
    elif act == "export":
        await callback.answer("Готовлю таблицу…")
        y, m = stats.prev_month()
        await stats.send_export(bot, [callback.from_user.id], y, m)
    elif act == "send":
        await callback.answer()
        await state.set_state(Form.waiting_for_broadcast)
        await callback.message.answer("📝 Введите текст рассылки для всех пользователей бота:")


@dp.message(Command("export"), StateFilter(any_state))
async def cmd_export(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    arg = (message.text or "").partition(" ")[2].strip()
    m = re.match(r"^(\d{1,2})[./-](\d{4})$", arg)
    y, mo = (int(m.group(2)), int(m.group(1))) if m else stats.prev_month()
    if not 1 <= mo <= 12 or not 2020 <= y <= 2100:
        await message.answer("Формат: /export 09.2026 — месяц и год. Без даты — прошлый месяц.")
        return
    await stats.send_export(bot, [message.from_user.id], y, mo)


@dp.message(Command("digest"), StateFilter(any_state))
async def cmd_digest(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer(reports.digest_text() + "\n\n<i>Это предпросмотр. Отправить в группу — /admin → «Сводка в группу».</i>")


@dp.message(Command("send"), StateFilter(any_state))
async def broadcast_start(message: types.Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    await message.answer("📝 Введите текст рассылки для всех пользователей бота:")
    await state.set_state(Form.waiting_for_broadcast)


@dp.message(StateFilter(Form.waiting_for_broadcast))
async def broadcast_process(message: types.Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    from tgutil import broadcast
    good, total = await broadcast(bot, store.all_user_ids(), esc(message.text or ""))
    await message.answer(f"✅ Рассылка завершена. Доставлено: {good} из {total}.")


# --- Кнопки под обращением в рабочем чате ---
@dp.callback_query(F.data.startswith("rp:"), StateFilter(any_state))
async def cb_report_group(callback: types.CallbackQuery):
    try:
        _, act, rid = callback.data.split(":")
        rid = int(rid)
    except ValueError:
        await callback.answer()
        return
    m = callback.message
    private = m.chat.type == "private"
    # кнопки обращений работают только в рабочем чате и в личке у админов
    if (private and not is_admin(callback.from_user.id)) or (not private and m.chat.id != config.SUPER_CHAT_ID):
        await callback.answer()
        return
    if act in ("take", "done", "reopen"):
        _, msg = await reports.set_status(bot, rid, act, person(callback.from_user))
        await callback.answer(msg or {"take": "Взято в работу ✦", "done": "Отмечено решённым ✅", "reopen": "Снова в работе"}[act],
                              show_alert=bool(msg))
        return
    if act == "ask":
        r = reports.get(rid)
        if not r or not reports.author_of(r):
            await callback.answer("Автор отправил обращение без обратной связи — ответить нельзя.", show_alert=True)
            return
        await callback.answer("Ответь на сообщение бота ниже ✦")
        u = callback.from_user
        kw: dict = {"reply_markup": ForceReply(force_reply=True, selective=True, input_field_placeholder=f"Ответ автору №{rid}")}
        if getattr(m, "message_thread_id", None) and getattr(m, "is_topic_message", False):
            kw["message_thread_id"] = m.message_thread_id
        if r["message_id"] and str(r["chat_id"]) == str(m.chat.id):
            kw["reply_parameters"] = {"message_id": int(r["message_id"]), "allow_sending_without_reply": True}
        p = await send(bot, m.chat.id,
                       f'💬 <b>Ответ автору №{rid}</b>\n<a href="tg://user?id={u.id}">{esc(u.first_name or "коллега")}</a>, '
                       "ответь на это сообщение (свайп влево или «Ответить») — текст получит автор"
                       + (" анонимно: кто он, не узнает никто." if r["anon"] else "."), **kw)
        if p is not None:
            reports.map_msg(p.chat.id, p.message_id, rid, "prompt")
        return
    await callback.answer()


@dp.callback_query(F.data == "noop", StateFilter(any_state))
async def cb_noop(callback: types.CallbackQuery):
    await callback.answer()


# --- Reply руководства в рабочем чате или в личке админа ---
async def _team_reply_from(message: types.Message) -> bool:
    rt = message.reply_to_message
    if not rt or rt.from_user is None or rt.from_user.id != bot.id:
        return False
    mp = reports.lookup(message.chat.id, rt.message_id)
    text = (message.text or message.caption or "").strip()
    media = (message.chat.id, message.message_id) if (message.photo or message.document) else None
    if mp and mp["kind"] in ("report", "prompt"):
        who = "Руководство ССт НАБ" if message.from_user.username == "GroupAnonymousBot" else person(message.from_user)
        good, msg = await reports.team_reply(bot, mp["report_id"], text, who, media_from=media)
        if good:
            if not await safe(bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id,
                                                       reaction=[ReactionTypeEmoji(emoji="👍")])):
                await message.reply("✅ <i>Ответ доставлен автору.</i>")
        else:
            await message.reply(f"⚠️ <i>{esc(msg)}</i>")
        return True
    # совместимость: обращения до v3 (ID в невидимой ссылке)
    uid = extract_hidden_user_id(rt)
    if uid:
        body = f"📩 <b>Новое сообщение от руководства ССт НАБ!</b>\n-----------------------------------\n💬 <i>{esc(text)}</i>"
        sent = await send(bot, uid, body)
        if media:
            await safe(bot.copy_message(chat_id=uid, from_chat_id=media[0], message_id=media[1]))
        await message.reply("✅ <i>Ответ доставлен.</i>" if sent else "⚠️ <i>Не удалось отправить — возможно, студент заблокировал бота.</i>")
        return True
    return False


@dp.message(F.chat.id == config.SUPER_CHAT_ID, StateFilter(any_state))
async def group_message(message: types.Message):
    u = message.from_user
    # админы группы, пишущие «от имени группы», приходят как GroupAnonymousBot — их ответы принимаем
    anon_admin = u is not None and u.username == "GroupAnonymousBot"
    if u and u.is_bot and not anon_admin:
        return
    await _team_reply_from(message)


@dp.message(F.chat.type == "private", F.reply_to_message, StateFilter(any_state))
async def private_reply(message: types.Message, state: FSMContext):
    """Reply в личке: админ отвечает на копию обращения, автор — на ответ руководства."""
    rt = message.reply_to_message
    if rt.from_user is None or rt.from_user.id != bot.id:
        return await fallback(message, state)
    mp = reports.lookup(message.chat.id, rt.message_id)
    if mp and mp["kind"] == "private":
        await state.clear()
        good, msg = await reports.user_reply(bot, mp["report_id"], message.from_user.id, message.text or message.caption or "")
        await message.answer("✅ Отправлено руководству ССт НАБ." if good else f"⚠️ {esc(msg)}")
        return
    if is_admin(message.from_user.id) and await _team_reply_from(message):
        return
    await fallback(message, state)


@dp.message(F.chat.type == "private", StateFilter(any_state))
async def fallback(message: types.Message, state: FSMContext):
    if await state.get_state():
        return
    await message.answer("Выбери раздел в меню ниже 👇", reply_markup=main_kb())


# =======================================================
# ЗАПУСК
# =======================================================

BOT_IDENTITY: dict = {}


async def on_startup() -> None:
    store.db()
    try:
        me = await bot.get_me()
        BOT_IDENTITY.update(id=me.id, username=me.username, name=me.full_name)
        log.info("🤖 Токен принадлежит боту @%s (id=%s)", me.username, me.id)
        if me.username and me.username.lower() != config.BOT_USERNAME.lower():
            log.warning("⚠️  BOT_USERNAME в .env = «%s», а токен от @%s.", config.BOT_USERNAME, me.username)
    except Exception:  # noqa: BLE001
        log.exception("Не удалось проверить токен через getMe")

    if config.SET_BOT_PROFILE:
        for call, kwargs in (
            (bot.set_my_short_description, {"short_description": config.BOT_SHORT_DESCRIPTION}),
            (bot.set_my_description, {"description": config.BOT_DESCRIPTION}),
            (bot.set_my_commands, {"commands": [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="app", description="Открыть приложение"),
            ]}),
        ):
            try:
                await call(**kwargs)
            except Exception:  # noqa: BLE001
                log.warning("Не удалось обновить профиль бота (%s)", getattr(call, "__name__", "?"))
        log.info("🪪 Профиль бота обновлён")

    if config.WEBAPP_URL:
        try:
            await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Приложение", web_app=WebAppInfo(url=config.WEBAPP_URL)))
        except Exception:  # noqa: BLE001
            log.exception("Не удалось настроить кнопку меню Mini App")


async def main() -> None:
    log.info("🚀 Бот @%s запускается...", config.BOT_USERNAME)
    await on_startup()
    import scheduler
    tasks = [dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()), scheduler.run(bot)]
    if os.getenv("RUN_API", "1") == "1":
        from webapi import run_api
        tasks.append(run_api(bot, BOT_IDENTITY))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Бот остановлен.")

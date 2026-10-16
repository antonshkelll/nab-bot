"""
Бот «Спроси ССт НАБ» — @SPROSINAB_BOT
Студенческий совет факультета «Налоги, аудит, бизнес-анализ», Финансовый университет.

Разделы: НПБ, FAQ, карта корпуса, важные группы, контакты, вопросы
руководству (можно анонимно), инициативы, поломки с фото, тревожная кнопка.
Все обращения уходят в рабочий чат, админ отвечает через Reply — ответ
приходит пользователю от имени бота.

HTTP-API для Mini App (webapi.py) поднимается в этом же процессе.
"""
import asyncio
import logging
import os
import sqlite3
from collections import defaultdict

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup, any_state
from aiogram.types import (
    BotCommand,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonWebApp,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

import analytics
import config
from analytics import track
from delivery import KINDS, Report, deliver, display_name, notify_admins

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("sprosinab")

if not config.TOKEN:
    raise SystemExit("❌ Не задан BOT_TOKEN. Скопируйте .env.example в .env и впишите токен из @BotFather.")

bot = Bot(token=config.TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

if config.ANALYTICS:
    analytics.init(config.ANALYTICS_DB, config.ANALYTICS_SALT)
    dp.update.outer_middleware(analytics.AnalyticsMiddleware())

# === БАЗА ДАННЫХ ПОЛЬЗОВАТЕЛЕЙ БОТА (SQLite) ===
conn = sqlite3.connect(config.BASE_DIR / "users_nab.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
conn.commit()


def add_user(user_id: int) -> None:
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()


def get_all_users() -> list[int]:
    cursor.execute("SELECT user_id FROM users")
    return [row[0] for row in cursor.fetchall()]


# === СОСТОЯНИЯ ===
class Form(StatesGroup):
    waiting_for_initiative_text = State()
    waiting_for_panic_text = State()
    waiting_for_broadcast = State()
    waiting_for_question_text = State()
    waiting_for_repair = State()


# === КЛАВИАТУРЫ ===

def build_main_kb() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="📚 НПБ"), KeyboardButton(text="📖 FAQ")],
        [KeyboardButton(text="📌 Важные группы"), KeyboardButton(text="📞 Наши Контакты")],
        [KeyboardButton(text="❓ Вопросы"), KeyboardButton(text="💡 Инициатива")],
        [KeyboardButton(text="🛠 Поломки"), KeyboardButton(text="🚨 Тревожная кнопка")],
        [KeyboardButton(text="🗺 Карта корпуса")],
    ]
    if config.WEBAPP_URL:
        rows.append([KeyboardButton(text="🌐 Приложение", web_app=WebAppInfo(url=config.WEBAPP_URL))])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


main_kb = build_main_kb()

npb_inline_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text=f"{key}. {title}", callback_data=f"npb_{key}")]
        for key, title in config.NPB_TITLES.items()
    ]
)

groups_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📢 ТГК NAB LABEL"), KeyboardButton(text="🎓 ТГК Финунивер | НАБ")],
        [KeyboardButton(text="👥 ВК НАБ | Налоги, аудит"), KeyboardButton(text="🏛 ВК Финунивер | НАБ")],
        [KeyboardButton(text="⬅️ Назад")],
    ],
    resize_keyboard=True,
)

anon_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🕵️‍♂️ Анонимно", callback_data="anon_yes")],
        [InlineKeyboardButton(text="👤 С профилем", callback_data="anon_no")],
    ]
)


def webapp_kb(path: str = "") -> InlineKeyboardMarkup | None:
    """Инлайн-кнопка «открыть приложение» на конкретном экране."""
    if not config.WEBAPP_URL:
        return None
    url = f"{config.WEBAPP_URL}/{path.lstrip('/')}" if path else config.WEBAPP_URL
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🌐 Открыть в приложении", web_app=WebAppInfo(url=url))]]
    )


# Приветственная картинка (фото команды + логотип NAB Label). После первой
# отправки Telegram отдаёт file_id — дальше шлём по нему, без повторной загрузки.
WELCOME_IMAGE = config.BASE_DIR / "assets" / "welcome.jpg"
_welcome_file_id: str | None = None

WELCOME_TEXT = (
    "👋 <b>Привет! Это «Спроси ССт НАБ»</b> — официальный бот-помощник "
    "Студенческого совета факультета НАБ.\n\n"
    "📚 Документы и FAQ, ❓ вопросы руководству, 💡 инициативы, "
    "🛠 поломки и 🚨 тревожная кнопка — всё здесь.\n\n"
    "✨ Открой <b>приложение</b> — там всё то же самое, только красивее. "
    "Или выбирай раздел в меню ниже 👇\n\n"
    "<i>Они были теми, в ком вечно нуждались — Налоги, Аудит и Бизнес-анализ</i> 💚"
)


async def send_welcome(message: types.Message) -> None:
    """Приветствие: фото + кнопка Mini App, затем обычное меню-клавиатура."""
    global _welcome_file_id
    kb = webapp_kb()
    if kb is not None:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✨ Открыть приложение", web_app=WebAppInfo(url=config.WEBAPP_URL))
        ]])
    photo = _welcome_file_id or (FSInputFile(WELCOME_IMAGE) if WELCOME_IMAGE.exists() else None)
    if photo is not None:
        try:
            sent = await message.answer_photo(photo, caption=WELCOME_TEXT, reply_markup=kb)
            if sent.photo:
                _welcome_file_id = sent.photo[-1].file_id
            await message.answer("Меню разделов — внизу 👇", reply_markup=main_kb)
            return
        except Exception:  # noqa: BLE001
            log.exception("Не удалось отправить приветственное фото — шлю текстом")
    await message.answer(WELCOME_TEXT, reply_markup=kb or main_kb)
    if kb is not None:
        await message.answer("Меню разделов — внизу 👇", reply_markup=main_kb)


def extract_hidden_user_id(message: types.Message) -> int | None:
    """
    Достаёт ID студента из невидимой ссылки под смайликом.
    Для текстов ссылка лежит в entities, для фото — в caption_entities.
    """
    entities = (message.entities or []) + (message.caption_entities or [])
    for entity in entities:
        if entity.type == "text_link" and entity.url and "start=" in entity.url:
            try:
                return int(entity.url.split("start=")[1])
            except (IndexError, ValueError):
                continue
    return None


async def send_admin_reply(target_id: int, message: types.Message) -> bool:
    """Пересылает ответ администратора студенту. True — доставлено."""
    answer = message.text or message.caption or ""
    reply_text = (
        "📩 <b>Новое сообщение от руководства ССт НАБ!</b>\n"
        "-----------------------------------\n"
        f"💬 <i>{answer}</i>"
    )
    try:
        if message.photo:
            await bot.send_photo(chat_id=target_id, photo=message.photo[-1].file_id, caption=reply_text)
        elif message.document:
            await bot.send_document(chat_id=target_id, document=message.document.file_id, caption=reply_text)
        else:
            await bot.send_message(chat_id=target_id, text=reply_text)
        return True
    except Exception:  # noqa: BLE001
        return False


# =======================================================
# БЛОК 1: НАВИГАЦИЯ И МЕНЮ
# =======================================================

# --- ОТВЕТ АДМИНА ИЗ ЛИЧНОЙ ПЕРЕПИСКИ ---
# Копия обращения приходит админу в личку; Reply на неё уходит студенту.
# Фильтр узкий: только личный чат, только админ, только Reply на сообщение бота.
@dp.message(
    F.chat.type == "private",
    F.from_user.id.in_(set(config.ADMIN_IDS)),
    F.reply_to_message,
    StateFilter(any_state),
)
async def admin_dm_reply_handler(message: types.Message, state: FSMContext):
    reply_to = message.reply_to_message
    if reply_to.from_user.id != bot.id:
        return
    target_id = extract_hidden_user_id(reply_to)
    if not target_id:
        return
    if target_id == message.from_user.id:
        await message.reply("⚠️ Это ваше собственное обращение — отвечать некому.")
        return

    if await send_admin_reply(target_id, message):
        track(target_id, "admin_reply_delivered")
        await message.reply("✅ <i>Ответ доставлен студенту.</i>")
    else:
        await message.reply("⚠️ <i>Не удалось отправить — возможно, студент заблокировал бота.</i>")


@dp.message(Command("start"), StateFilter(any_state))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    add_user(message.from_user.id)
    await send_welcome(message)


@dp.message(Command("app"), StateFilter(any_state))
async def cmd_app(message: types.Message, state: FSMContext):
    await state.clear()
    kb = webapp_kb()
    if kb is None:
        await message.answer("⚠️ Веб-приложение пока не настроено.")
        return
    await message.answer(
        "🌐 <b>Спроси ССт НАБ — веб-версия</b>\n\n"
        "Все разделы бота в одном окне: документы, FAQ, контакты, вопросы, "
        "инициативы и сообщения о поломках 👇",
        reply_markup=kb,
    )


@dp.message(F.text == "⬅️ Назад", StateFilter(any_state))
async def cmd_back(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Вы вернулись в главное меню.", reply_markup=main_kb)


# --- РАЗДЕЛ: НПБ ---
@dp.message(F.text == "📚 НПБ", StateFilter(any_state))
async def send_npb_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🏛 <b>Нормативно-правовая база (НПБ)</b>\n\n"
        "Здесь собраны все ключевые документы Финансового университета и Студенческого совета НАБ. "
        "Выберите нужный документ из списка ниже, и я моментально отправлю его вам 📄👇",
        reply_markup=npb_inline_kb,
    )


@dp.callback_query(F.data.startswith("npb_"), StateFilter(any_state))
async def process_npb_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    file_id = callback.data.split("_")[1]
    files_to_send = config.NPB_FILES.get(file_id)

    if files_to_send:
        sent_count = 0
        for file_name in files_to_send:
            path = config.BASE_DIR / file_name
            if path.exists():
                await callback.message.answer_document(FSInputFile(path))
                sent_count += 1
        if sent_count == 0:
            await callback.message.answer("⚠️ Файл не найден на сервере в папке docs. Проверьте имя файла.")
    await callback.answer()


# --- РАЗДЕЛ: FAQ ---
@dp.message(F.text.in_({"📖 FAQ", "❓ FAQ", "📖 Часто задаваемые вопросы"}), StateFilter(any_state))
async def send_faq(message: types.Message, state: FSMContext):
    await state.clear()
    if not config.FAQ_FILE.exists():
        await message.answer("⚠️ Файл FAQ пока не загружен на сервер (docs/FAQ.pdf).")
        return
    await message.answer(
        "📖 <b>FAQ — часто задаваемые вопросы</b>\n\n"
        "Собрали ответы на всё, что чаще всего спрашивают первокурсники: учёба, сессия, "
        "преподаватели, общежитие, личный кабинет, стипендия и документы.\n\n"
        "Листай файл ниже 👇",
        reply_markup=webapp_kb("#/faq"),
    )
    await message.answer_document(
        FSInputFile(config.FAQ_FILE, filename="FAQ — Часто задаваемые вопросы (НАБ).pdf"),
        caption="📚 FAQ — часто задаваемые вопросы | ССт НАБ",
    )


# --- РАЗДЕЛ: КАРТА КОРПУСА ---
@dp.message(F.text.in_({"🗺 Карта корпуса", "🗺️ Карта корпуса", "Карта корпуса"}), StateFilter(any_state))
async def send_map(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🗺 <b>Карта корпуса — Верхняя Масловка, 15</b>\n\n"
        "Все этажи главного корпуса и компьютерного корпуса 15-1. "
        "В приложении можно вписать номер аудитории — покажем этаж и точку на плане 📍",
        reply_markup=webapp_kb("#/map"),
    )
    if config.MAP_FILE.exists():
        await message.answer_document(
            FSInputFile(config.MAP_FILE, filename="Карта корпуса — Верхняя Масловка 15.pdf"),
            caption="🗺 Карта корпуса | ССт НАБ",
        )


# --- РАЗДЕЛ: НАШИ КОНТАКТЫ ---
@dp.message(F.text == "📞 Наши Контакты", StateFilter(any_state))
async def send_contacts(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📞 <b>Контакты руководства ССт НАБ:</b>\n\n"
        " <b>1. Председатель</b>\n"
        "👤 Алина Зотова\n"
        "📞 +7 (937) 666-11-84 | @jannalli\n\n"
        " <b>2. Заместитель председателя по учебно-социальной деятельности</b>\n"
        "👤 Мусо Мирзоев\n"
        "📞 +7 (912) 029-37-35 | @YotaHiro2"
    )


# --- РАЗДЕЛ: ВОПРОСЫ ---
@dp.message(F.text == "❓ Вопросы", StateFilter(any_state))
async def question_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❓ <b>Задать вопрос руководителям студенческого совета НАБ</b>\n\n"
        "Здесь ты можешь задать любой интересующий тебя вопрос руководству ССт НАБ. "
        "Мы постараемся ответить как можно быстрее!\n\n"
        "Как ты хочешь отправить свой вопрос?",
        reply_markup=anon_kb,
    )
    await state.update_data(action_type="question")


# --- РАЗДЕЛ: ВАЖНЫЕ ГРУППЫ ---
@dp.message(F.text == "📌 Важные группы", StateFilter(any_state))
async def send_groups_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📌 Важные группы\n\nВыбери интересующий ресурс в панели меню ниже 👇",
        reply_markup=groups_kb,
    )


LINKS = {
    "📢 ТГК NAB LABEL": ("https://t.me/NABLABFU", "🔗 Перейти в канал", "Переходи по ссылке на наш официальный канал 👇"),
    "🎓 ТГК Финунивер | НАБ": ("https://t.me/magfnab", "🔗 Перейти в канал", "Переходи по ссылке на канал факультета НАБ 👇"),
    "👥 ВК НАБ | Налоги, аудит": ("https://vk.ru/taxaudit", "🔗 Перейти в группу", "Переходи по ссылке на нашу группу ВКонтакте 👇"),
    "🏛 ВК Финунивер | НАБ": ("https://vk.ru/timetotaxesfa", "🔗 Перейти в группу", "Переходи по ссылке на официальную группу ВКонтакте 👇"),
}


@dp.message(F.text.in_(set(LINKS)), StateFilter(any_state))
async def url_handler(message: types.Message, state: FSMContext):
    await state.clear()
    url, btn, text = LINKS[message.text]
    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=btn, url=url)]]),
    )


# --- РАЗДЕЛ: ИНИЦИАТИВА ---
@dp.message(F.text == "💡 Инициатива", StateFilter(any_state))
async def init_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Как ты хочешь отправить свою инициативу/запрос?", reply_markup=anon_kb)
    await state.update_data(action_type="initiative")


# --- РАЗДЕЛ: ТРЕВОЖНАЯ КНОПКА ---
@dp.message(F.text == "🚨 Тревожная кнопка", StateFilter(any_state))
async def panic_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Как ты хочешь отправить тревожный сигнал?", reply_markup=anon_kb)
    await state.update_data(action_type="panic")


# --- РАЗДЕЛ: ПОЛОМКИ ---
@dp.message(F.text.in_({"🛠 Поломки", "🔧 Поломки"}), StateFilter(any_state))
async def repair_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🛠 <b>Сообщить о поломке</b>\n\n"
        "Сломалась розетка, не работает дверь, потекла батарея, разбито стекло? "
        "Пришли фото — мы передадим информацию начальнику корпуса.\n\n"
        "Как ты хочешь отправить обращение?",
        reply_markup=anon_kb,
    )
    await state.update_data(action_type="repair")


# --- АДМИН-РАССЫЛКА ---
@dp.message(Command("send"), StateFilter(any_state))
async def broadcast_start(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in config.ADMIN_IDS:
        return
    await message.answer("📝 Введите текст рассылки для всех пользователей бота:")
    await state.set_state(Form.waiting_for_broadcast)


# =======================================================
# БЛОК 2: ОБРАБОТКА ДИАЛОГОВ И СКРЫТИЕ ID
# =======================================================

NEXT_STATE = {
    "initiative": (Form.waiting_for_initiative_text, "📝 Напиши свою инициативу/запрос в одном сообщении:"),
    "panic": (Form.waiting_for_panic_text, "🚨 Опиши свою проблему для тревожной кнопки в одном сообщении:"),
    "question": (Form.waiting_for_question_text, "📝 Напиши свой вопрос в одном сообщении:"),
    "repair": (
        Form.waiting_for_repair,
        "📸 Пришли <b>фото поломки</b> (можно несколько сразу) и в подписи к нему укажи, "
        "<b>что и где сломалось</b> — корпус и аудиторию.\n\n"
        "Если фото нет — просто опиши проблему текстом.",
    ),
}


@dp.callback_query(F.data.in_({"anon_yes", "anon_no"}), StateFilter(any_state))
async def process_anon_choice(callback: types.CallbackQuery, state: FSMContext):
    is_anon = callback.data == "anon_yes"
    await state.update_data(is_anon=is_anon)
    data = await state.get_data()
    action = data.get("action_type")
    await callback.message.delete()

    target = NEXT_STATE.get(action)
    if target:
        next_state, prompt = target
        await callback.message.answer(prompt)
        await state.set_state(next_state)

    await callback.answer()


def get_sender_name(message: types.Message, is_anon: bool) -> str:
    """Красивое отображение отправителя (без ID)."""
    return display_name(message.from_user.username, message.from_user.first_name, bool(is_anon))


async def _handle_text_report(message: types.Message, state: FSMContext, kind: str) -> None:
    data = await state.get_data()
    report = Report(
        kind=kind,
        text=message.text or message.caption or "",
        user_id=message.from_user.id,
        sender_name=get_sender_name(message, data.get("is_anon")),
    )
    try:
        file_ids = await deliver(bot, report)
        await notify_admins(bot, report, file_ids)
        track(message.from_user.id, "report_sent", kind, "bot")
        await message.answer(KINDS[kind].user_ack, reply_markup=main_kb)
    except Exception as e:  # noqa: BLE001
        log.exception("delivery failed")
        await message.answer(f"⚠️ Ошибка отправки: {e}")
    await state.clear()


@dp.message(StateFilter(Form.waiting_for_question_text))
async def question_process(message: types.Message, state: FSMContext):
    await _handle_text_report(message, state, "question")


@dp.message(StateFilter(Form.waiting_for_initiative_text))
async def init_process(message: types.Message, state: FSMContext):
    await _handle_text_report(message, state, "initiative")


@dp.message(StateFilter(Form.waiting_for_panic_text))
async def panic_process(message: types.Message, state: FSMContext):
    await _handle_text_report(message, state, "panic")


# --- ПОЛОМКИ: сбор альбома из нескольких фото ---
_album_buffer: dict[str, list[types.Message]] = defaultdict(list)
_album_tasks: dict[str, asyncio.Task] = {}


@dp.message(StateFilter(Form.waiting_for_repair))
async def repair_process(message: types.Message, state: FSMContext):
    if not message.photo and not (message.text or message.caption):
        await message.answer("⚠️ Пришли, пожалуйста, фото поломки или опиши проблему текстом.")
        return

    # Несколько фото одним сообщением приходят по одному — собираем их в буфер.
    if message.media_group_id:
        key = f"{message.from_user.id}:{message.media_group_id}"
        _album_buffer[key].append(message)
        if key in _album_tasks:
            return
        _album_tasks[key] = asyncio.create_task(_flush_album(key, state))
        return

    await _send_repair(state, message.from_user, [message])


async def _flush_album(key: str, state: FSMContext) -> None:
    await asyncio.sleep(1.5)  # ждём, пока Telegram доставит остальные части альбома
    messages = sorted(_album_buffer.pop(key, []), key=lambda m: m.message_id)
    _album_tasks.pop(key, None)
    if messages:
        await _send_repair(state, messages[0].from_user, messages)


async def _send_repair(state: FSMContext, user: types.User, messages: list[types.Message]) -> None:
    data = await state.get_data()
    first = messages[0]

    text = ""
    for m in messages:
        text = m.caption or m.text or text
        if text:
            break

    file_ids = [m.photo[-1].file_id for m in messages if m.photo][: config.MAX_PHOTOS]

    report = Report(
        kind="repair",
        text=text or "Без описания",
        user_id=user.id,
        sender_name=display_name(user.username, user.first_name, bool(data.get("is_anon"))),
        photo_file_ids=file_ids,
    )
    try:
        file_ids = await deliver(bot, report)
        await notify_admins(bot, report, file_ids)
        track(user.id, "report_sent", "repair", "bot")
        await first.answer(KINDS["repair"].user_ack, reply_markup=main_kb)
    except Exception as e:  # noqa: BLE001
        log.exception("repair delivery failed")
        await first.answer(f"⚠️ Ошибка отправки: {e}")
    await state.clear()


@dp.message(StateFilter(Form.waiting_for_broadcast))
async def broadcast_process(message: types.Message, state: FSMContext):
    users = get_all_users()
    count = 0
    for user_id in users:
        try:
            await bot.send_message(user_id, message.text)
            count += 1
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.05)  # бережно к лимитам Telegram
    await message.answer(f"✅ Рассылка завершена. Доставлено: {count} пользователей.")
    await state.clear()


# =======================================================
# БЛОК 3: УМНАЯ СИСТЕМА ОТВЕТОВ ИЗ СУПЕРЧАТА (ЧТЕНИЕ СКРЫТЫХ ID)
# =======================================================

@dp.message(F.chat.id == config.SUPER_CHAT_ID)
async def admin_reply_handler(message: types.Message):
    if not message.reply_to_message:
        return
    if message.reply_to_message.from_user.id != bot.id:
        return

    user_id = extract_hidden_user_id(message.reply_to_message)
    if not user_id:
        return

    if await send_admin_reply(user_id, message):
        track(user_id, "admin_reply_delivered")
        await message.reply("✅ <i>Ответ успешно доставлен пользователю!</i>")
    else:
        await message.reply("⚠️ <i>Не удалось отправить ответ. Возможно, пользователь заблокировал бота.</i>")


# =======================================================
# ЗАПУСК
# =======================================================

BOT_IDENTITY: dict = {}


async def on_startup() -> None:
    # Спрашиваем у Telegram, какому боту принадлежит токен. Если приложение
    # открыть у другого бота, подпись не сойдётся — и в логе будет видно, почему.
    try:
        me = await bot.get_me()
        BOT_IDENTITY.update(id=me.id, username=me.username, name=me.full_name)
        log.info("🤖 Токен принадлежит боту @%s (id=%s)", me.username, me.id)
        if me.username and me.username.lower() != config.BOT_USERNAME.lower():
            log.warning(
                "⚠️  BOT_USERNAME в .env = «%s», а токен от @%s. "
                "Исправьте BOT_USERNAME, иначе ответы через Reply не дойдут до студентов.",
                config.BOT_USERNAME, me.username,
            )
    except Exception:  # noqa: BLE001
        log.exception("Не удалось проверить токен через getMe")

    if config.ADMIN_IDS and config.NOTIFY_ADMINS_DM:
        log.info("📬 Копии обращений будут дублироваться в личку: %s", config.ADMIN_IDS)

    # Профиль бота: описание, «о боте» и список команд — как у F&U.
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
                log.warning("Не удалось обновить профиль бота (%s)", call.__name__)
        log.info("🪪 Профиль бота обновлён: описание, «о боте», команды")

    if config.WEBAPP_URL:
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="Приложение", web_app=WebAppInfo(url=config.WEBAPP_URL))
            )
            log.info("Кнопка меню Mini App настроена: %s", config.WEBAPP_URL)
        except Exception:  # noqa: BLE001
            log.exception("Не удалось настроить кнопку меню Mini App")


async def main() -> None:
    log.info("🚀 Бот @%s запускается...", config.BOT_USERNAME)
    await on_startup()

    tasks = [dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())]

    if os.getenv("RUN_API", "1") == "1":
        from webapi import run_api  # локальный импорт, чтобы бот работал и без aiohttp
        tasks.append(run_api(bot, add_user, BOT_IDENTITY))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Бот остановлен.")

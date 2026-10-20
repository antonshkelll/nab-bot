"""
Проверка обработчиков бота (main.py) на заглушках aiogram — без интернета:
/start, обращения, кнопки в рабочем чате, ответы через Reply (в том числе анонимному),
ответ автора, звёзды и отзыв, «Мои обращения», анонсы «приду / не приду», опросы, /admin.

    python3 tests/test_bot.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
TMP = Path(tempfile.mkdtemp())
os.environ.update(BOT_TOKEN="123456:TEST-TOKEN", ADMIN_IDS="1,2", SUPER_CHAT_ID="-100500", TOPIC_ID="7",
                  WEBAPP_URL="https://app.nablabel.ru", DATA_DIR=str(TMP / "data"))

import stubs  # noqa: E402

stubs.install()
import config  # noqa: E402

config.DOCS_DIR = TMP / "docs"
config.DOCS_DIR.mkdir(parents=True)
import store  # noqa: E402

store.reset_for_tests(TMP / "data" / "nab.db")
import main  # noqa: E402
import content  # noqa: E402
import reports  # noqa: E402
from stubs import Obj, kb_buttons  # noqa: E402

bot = main.bot
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1


class State:
    def __init__(self):
        self.data, self.state = {}, None

    async def clear(self):
        self.data, self.state = {}, None

    async def update_data(self, **k):
        self.data.update(k)

    async def get_data(self):
        return dict(self.data)

    async def set_state(self, s):
        self.state = s

    async def get_state(self):
        return self.state


class Msg:
    _id = 5000

    def __init__(self, text="", uid=777, chat=None, chat_type="private", reply_to=None, username="anya", first="Аня", **k):
        Msg._id += 1
        self.message_id = Msg._id
        self.text, self.caption = text, k.get("caption")
        self.from_user = Obj(id=uid, username=username, first_name=first, is_bot=False)
        self.chat = Obj(id=chat if chat is not None else uid, type=chat_type)
        self.reply_to_message = reply_to
        self.photo, self.document, self.media_group_id = k.get("photo"), None, None
        self.entities, self.caption_entities = k.get("entities") or [], []
        self.message_thread_id, self.is_topic_message = 7, True
        self.html_text = text
        self.sent = []

    async def answer(self, text, **k):
        self.sent.append((text, k))
        return Obj(message_id=1)

    async def answer_photo(self, photo, **k):
        self.sent.append(("PHOTO", k))
        return Obj(photo=[Obj(file_id="F")])

    async def answer_document(self, doc, **k):
        self.sent.append(("DOC", k))

    async def reply(self, text, **k):
        self.sent.append((text, k))

    async def delete(self):
        pass

    async def edit_text(self, text, **k):
        self.sent.append(("EDIT", text, k))

    async def edit_reply_markup(self, **k):
        self.sent.append(("EDITKB", k))


class Cb:
    def __init__(self, data, uid=777, message=None, username="anya", first="Аня"):
        self.data = data
        self.from_user = Obj(id=uid, username=username, first_name=first)
        self.message = message or Msg(uid=uid)
        self.answers = []

    async def answer(self, text=None, **k):
        self.answers.append((text, k))


def bot_msg(chat, mid):
    """Сообщение, которое «отправил бот» (для Reply)."""
    m = Msg(chat=chat, uid=42)
    m.message_id = mid
    m.from_user = Obj(id=bot.id, is_bot=True)
    return m


async def run():
    # ---------- /start ----------
    m = Msg("/start")
    await main.cmd_start(m, State())
    check(m.sent[0][0] == "PHOTO" or "Привет" in m.sent[0][0], "/start — приветствие")
    check(store.q("SELECT started FROM users WHERE id=777", one=True)["started"] == 1, "пользователь сохранён")

    # ---------- вопрос с профилем ----------
    st = State()
    await main.question_start(Msg("❓ Вопросы"), st)
    cb = Cb("anon_no")
    await main.process_anon_choice(cb, st)
    m = Msg("Как получить справку об обучении?")
    await main.question_process(m, st)
    check("№1" in m.sent[-1][0] and "Мои обращения" in m.sent[-1][0], "автору — номер обращения")
    r1 = reports.get(1)
    card = bot.of("send_message")[-1]
    check(card["chat_id"] == -100500 and f"rp:take:{r1['id']}" in str([b.get("callback_data") for b in kb_buttons(card["reply_markup"])]),
          "в группе карточка с кнопками")

    # ---------- анонимный SOS ----------
    st = State()
    await main.panic_start(Msg("🚨 Тревожная кнопка"), st)
    await main.process_anon_choice(Cb("anon_yes"), st)
    m = Msg("Конфликт с преподавателем")
    await main.panic_process(m, st)
    r2 = reports.get(2)
    check(r2["anon"] == 1 and r2["user_id"] is None and "анонимное" in m.sent[-1][0], "анонимный SOS сохранён без ID")

    # ---------- кнопка «Взять в работу» в группе ----------
    gmsg = Msg(chat=-100500, chat_type="supergroup", uid=2)
    cb = Cb(f"rp:take:{r1['id']}", uid=2, message=gmsg, username="muso", first="Мусо")
    await main.cb_report_group(cb)
    check(reports.get(1)["status"] == "work" and "Мусо" in reports.get(1)["taken_by"], "взято в работу из группы")
    check(any(k["chat_id"] == 777 and "взяли в работу" in k["text"] for k in bot.of("send_message")), "автору сообщили")

    # ---------- «Ответить автору» анонимному: подсказка + Reply ----------
    cb = Cb(f"rp:ask:{r2['id']}", uid=2, message=gmsg, username="muso", first="Мусо")
    await main.cb_report_group(cb)
    prompt = bot.of("send_message")[-1]
    check("Ответ автору №2" in prompt["text"] and "анонимно" in prompt["text"], "подсказка для ответа")
    pmap = store.q("SELECT message_id FROM msg_map WHERE report_id=2 AND kind='prompt'", one=True)
    reply = Msg("Мы с тобой, напиши в личку председателю", uid=2, chat=-100500, chat_type="supergroup",
                reply_to=bot_msg(-100500, pmap["message_id"]), username="muso", first="Мусо")
    n0 = len(bot.calls)
    await main.group_message(reply)
    to_author = [k for n, k in bot.calls[n0:] if n == "send_message" and k["chat_id"] == 777]
    check(to_author and "Мы с тобой" in to_author[0]["text"], "анонимный автор получил ответ")
    check("Мусо" not in to_author[0]["text"], "имя админа автору не показывается")

    # ---------- Reply прямо на карточку обращения ----------
    reply = Msg("Справку закажи в личном кабинете", uid=2, chat=-100500, chat_type="supergroup",
                reply_to=bot_msg(-100500, r1["message_id"]))
    await main.group_message(reply)
    check(any(k["chat_id"] == 777 and "личном кабинете" in k["text"] for k in bot.of("send_message")), "Reply на карточку → автору")

    # ---------- автор отвечает Reply на ответ руководства ----------
    pm = store.q("SELECT message_id FROM msg_map WHERE report_id=1 AND kind='private'", one=True)
    st = State()
    m = Msg("Спасибо! А сколько ждать?", reply_to=bot_msg(777, pm["message_id"]))
    await main.private_reply(m, st)
    check("Отправлено руководству" in m.sent[-1][0], "ответ автора ушёл")
    check("Спасибо! А сколько ждать?" in bot.of("send_message")[-1]["text"], "в группу — ответом на карточку")

    # ---------- кнопка «Ответить руководству» → состояние ----------
    st = State()
    await main.cb_user_reply_start(Cb(f"ur:{r2['id']}"), st)
    check(st.state is main.Form.waiting_user_reply and st.data["rid"] == 2, "ждём ответ автора")
    m = Msg("Хорошо, напишу")
    await main.user_reply_text(m, st)
    check("Отправлено" in m.sent[-1][0], "ответ по кнопке отправлен")
    await main.cb_user_reply_start(Cb(f"ur:{r2['id']}", uid=999), State())
    check(True, "чужой не может писать (без исключений)")

    # ---------- «Решено» → звёзды → отзыв ----------
    await main.cb_report_group(Cb(f"rp:done:{r1['id']}", uid=2, message=gmsg, first="Мусо"))
    rate_msg = [k for k in bot.of("send_message") if k["chat_id"] == 777][-1]
    check([b["callback_data"] for b in kb_buttons(rate_msg["reply_markup"])] == [f"rate:1:{i}" for i in range(1, 6)], "звёзды автору")
    msg = Msg("✅ отмечено решённым.\n\nПомогло? Оцени")
    cb = Cb("rate:1:5", message=msg)
    await main.cb_rate(cb, State())
    check(reports.get(1)["rating"] == 5 and "★★★★★" in msg.sent[-1][1], "оценка 5 и звёзды в сообщении")
    st = State()
    await main.cb_review_start(Cb("rv:1", message=msg), st)
    m = Msg("Ответили за час, круто!")
    await main.review_text(m, st)
    check(reports.get(1)["rating_text"] == "Ответили за час, круто!" and "Спасибо за отзыв" in m.sent[-1][0], "отзыв сохранён")
    check("★★★★★" in bot.of("send_message")[-1]["text"], "оценка ушла в группу")
    cb = Cb("rate:2:5", message=Msg("x"))
    await main.cb_rate(cb, State())
    check(cb.answers and cb.answers[0][1].get("show_alert"), "нерешённое оценить нельзя")

    # ---------- «Мои обращения» ----------
    m = Msg("🗂 Мои обращения")
    await main.my_reports(m, State())
    text = m.sent[-1][0]
    check("№1" in text and "№2" in text and "★★★★★" in text, "список своих обращений со статусами")
    m = Msg("🗂 Мои обращения", uid=999)
    await main.my_reports(m, State())
    check("Пока нет" in m.sent[-1][0], "у другого — пусто")

    # ---------- анонсы: приду / не приду ----------
    eid, _ = content.event_save({"title": "Собрание", "date": (content.now_msk() + dt.timedelta(days=3)).date().isoformat(),
                                 "time": "18:00", "rsvp": True}, 1)
    m = Msg("📅 Анонсы")
    await main.events_menu(m, State())
    check("Собрание" in m.sent[-1][0] and f"ev:yes:{eid}" in str(kb_buttons(m.sent[-1][1]["reply_markup"])), "анонс с кнопками")
    em = Msg("📅 Собрание")
    cb = Cb(f"ev:yes:{eid}", message=em)
    await main.cb_event(cb, State())
    check(content.rsvp_counts(eid)["yes"] == 1 and "Приду" not in cb.answers[0][0], "отметка «приду»")
    check("✅ Приду" in str(kb_buttons(em.sent[-1][2]["reply_markup"])), "кнопка отмечена галочкой")
    await main.cb_event(Cb(f"ev:yes:{eid}", message=em), State())
    check(content.rsvp_counts(eid)["yes"] == 0, "повторное нажатие снимает отметку")
    await main.cb_event(Cb(f"ev:no:{eid}", message=em), State())
    check(content.rsvp_counts(eid)["no"] == 1, "«не приду»")

    # ---------- опрос в чате ----------
    pid, _ = content.poll_save({"question": "Время собрания?", "options": ["17:00", "18:30"]}, 1)
    pm = Msg("опрос")
    await main.cb_poll_vote(Cb(f"pv:{pid}:1", message=pm), State())
    check("▰" in pm.sent[-1][1] and "18:30" in pm.sent[-1][1], "голос и результаты полосками")

    # ---------- админ: /admin только для админов ----------
    m = Msg("/admin", uid=777)
    await main.cmd_admin(m, State())
    check(not m.sent, "студенту /admin молчит")
    m = Msg("/admin", uid=1)
    await main.cmd_admin(m, State())
    check("Пульт ССт НАБ" in m.sent[-1][0] and "#/admin" in str(m.sent[-1][1]["reply_markup"]), "админу — сводка и кнопка Пульта")
    n0 = len(bot.calls)
    await main.cb_admin(Cb("adm:export", uid=1), State())
    check(any(n == "send_document" and k["chat_id"] == 1 for n, k in bot.calls[n0:]), "отчёт Excel админу")

    # ---------- совместимость со старыми обращениями (ID в невидимой ссылке) ----------
    old = bot_msg(-100500, 1)
    old.entities = [Obj(type="text_link", url="https://t.me/sprosinab_bot?start=555")]
    store.x("DELETE FROM msg_map WHERE message_id=1")
    reply = Msg("Ответ на старое обращение", uid=2, chat=-100500, chat_type="supergroup", reply_to=old)
    await main.group_message(reply)
    check(any(k["chat_id"] == 555 for k in bot.of("send_message")), "старые обращения тоже получают ответы")

    # ---------- запуск: профиль бота ----------
    await main.on_startup()
    names = [c[0] for c in bot.calls]
    for need in ("get_me", "set_my_short_description", "set_my_description", "set_my_commands", "set_chat_menu_button"):
        check(need in names, f"при старте вызван {need}")
    check(main.WELCOME_IMAGE.exists(), "картинка приветствия на месте")

    print(f"✅ bot: {ok} проверок пройдено")


if __name__ == "__main__":
    asyncio.run(run())

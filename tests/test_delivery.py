"""Тесты формата сообщений в топик (без установленного aiogram — он заглушён)."""
import sys, types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- Заглушки aiogram, чтобы тест работал без установленного пакета ---
if "aiogram" not in sys.modules:
    ag = types.ModuleType("aiogram"); ag.Bot = object
    ag_types = types.ModuleType("aiogram.types")
    ag_types.BufferedInputFile = type("BufferedInputFile", (), {"__init__": lambda s, d, filename=None: None})
    ag_types.InputMediaPhoto = type("InputMediaPhoto", (), {"__init__": lambda s, **k: s.__dict__.update(k)})
    ag.types = ag_types
    sys.modules["aiogram"] = ag
    sys.modules["aiogram.types"] = ag_types

from delivery import Report, build_caption, display_name, notify_admins, KINDS  # noqa: E402
import config  # noqa: E402

# тестовые значения вместо настоящих — настоящие живут только в .env на сервере
config.ADMIN_IDS = [111, 222, 333, 444]
config.SUPER_CHAT_ID = -1001234567890
config.TOPIC_ID = 42


class FakeBot:
    """Заглушка бота: запоминает, кому и что ушло."""
    def __init__(self, fail_for=()):
        self.messages = []
        self.albums = []
        self.fail_for = set(fail_for)

    async def send_message(self, chat_id, text, parse_mode=None):
        if chat_id in self.fail_for:
            raise RuntimeError("bot was blocked by the user")
        self.messages.append((chat_id, text))

    async def send_media_group(self, chat_id, media, message_thread_id=None):
        if chat_id in self.fail_for:
            raise RuntimeError("bot was blocked by the user")
        self.albums.append((chat_id, media))
        return []


def run_async(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


def run():
    ok = 0

    # 1. Имя отправителя
    assert display_name("anya", "Аня", False) == "@anya"; ok += 1
    assert display_name(None, "Аня", False) == "Аня"; ok += 1
    assert display_name("anya", "Аня", True) == "Анонимно 🕵️‍♂️"; ok += 1

    # 2. Вопрос: скрытая ссылка с ID обязана быть — на ней держится ответ через Reply
    cap = build_caption(Report(kind="question", text="Когда сессия?", user_id=42, sender_name="@anya"))
    assert f"?start=42" in cap, "в вопросе должна быть скрытая ссылка с ID"; ok += 1
    assert "НОВЫЙ ВОПРОС ОТ СТУДЕНТА" in cap and "Reply" in cap; ok += 1

    # 3. Поломка: место указано, но ссылки для ответа НЕТ
    cap = build_caption(Report(kind="repair", text="Розетка искрит", user_id=7,
                               sender_name="Анонимно 🕵️‍♂️", location="Кибальчича, ауд. 206"))
    assert "Кибальчича, ауд. 206" in cap and "ПОЛОМКЕ" in cap; ok += 1
    assert "start=7" not in cap and "Reply" not in cap, "у поломок ответа через Reply нет"; ok += 1

    # 4. Отвечать можно ТОЛЬКО на вопросы — у остальных ID не утекает
    for kind in ("initiative", "panic", "repair"):
        cap = build_caption(Report(kind=kind, text="текст", user_id=99, sender_name="@x"))
        assert "start=99" not in cap, f"{kind}: ID не должен попадать в топик"
        assert "Reply" not in cap, f"{kind}: подсказки про Reply быть не должно"
    ok += 1

    # 5. HTML-инъекция из текста пользователя экранируется
    cap = build_caption(Report(kind="question", text="<b>hack</b> & <script>", user_id=1, sender_name="@a"))
    assert "&lt;b&gt;hack&lt;/b&gt; &amp; &lt;script&gt;" in cap, "текст должен экранироваться"; ok += 1
    assert "<script>" not in cap; ok += 1

    # 6. Никакого сырого ID в видимой части подписи
    cap = build_caption(Report(kind="question", text="привет", user_id=555000111, sender_name="Анонимно 🕵️‍♂️"))
    visible = cap.split("</a>", 1)[1]
    assert "555000111" not in visible, "ID не должен быть виден в тексте"; ok += 1

    # 7. Конфиг цел
    assert config.SUPER_CHAT_ID == -1001234567890 and config.TOPIC_ID == 42; ok += 1
    assert len(config.ADMIN_IDS) == 4 and len(config.NPB_FILES) == 9; ok += 1

    # 8. По умолчанию копии в личку НЕ уходят — только суперчат
    bot = FakeBot()
    rep0 = Report(kind="question", text="тест", user_id=1, sender_name="@a")
    assert config.NOTIFY_ADMINS_DM is False, "дублирование админам выключено по умолчанию"; ok += 1
    assert run_async(notify_admins(bot, rep0)) == 0 and not bot.messages, "в личку ничего не ушло"; ok += 1

    # 9. Если включить — копии уходят всем админам
    config.NOTIFY_ADMINS_DM = True
    bot = FakeBot()
    rep = Report(kind="question", text="Когда сессия?", user_id=42, sender_name="@anya")
    n = run_async(notify_admins(bot, rep))
    assert n == len(config.ADMIN_IDS), f"копии ушли {n} из {len(config.ADMIN_IDS)} админов"; ok += 1
    assert {c for c, _ in bot.messages} == set(config.ADMIN_IDS), "получатели — ровно ADMIN_IDS"; ok += 1
    assert "?start=42" in bot.messages[0][1], "в копии есть ссылка для ответа через Reply"; ok += 1

    # 10. Один заблокировавший админ не ломает доставку остальным
    bot = FakeBot(fail_for=[config.ADMIN_IDS[0]])
    n = run_async(notify_admins(bot, rep))
    assert n == len(config.ADMIN_IDS) - 1, "остальные админы получили копию"; ok += 1

    # 11. Поломка с фото уходит альбомом, а не текстом
    bot = FakeBot()
    rep_photo = Report(kind="repair", text="Розетка искрит", user_id=7,
                       sender_name="@anya", location="ауд. 206", photo_file_ids=["AgACfake1"])
    run_async(notify_admins(bot, rep_photo))
    assert len(bot.albums) == len(config.ADMIN_IDS) and not bot.messages, "фото ушли альбомом"; ok += 1
    assert "ауд. 206" in bot.albums[0][1][0].caption, "в подписи есть место поломки"; ok += 1

    config.NOTIFY_ADMINS_DM = False

    # 12. Тексты автоответов пользователю
    assert KINDS["question"].user_ack.startswith("✅ Твой вопрос успешно передан руководству ССт НАБ")
    assert KINDS["initiative"].user_ack.startswith("✅ Твоя инициатива успешно передана")
    assert KINDS["panic"].user_ack.startswith("✅ Сигнал SOS передан руководству ССт НАБ")
    assert "Твоё обращение принято" in KINDS["repair"].user_ack
    ok += 1

    print(f"✅ delivery: {ok}/22 проверок пройдено")


if __name__ == "__main__":
    run()

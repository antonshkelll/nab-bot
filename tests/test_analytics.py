"""Тесты аналитики без aiogram: фейковые апдейты, запись событий и отчёт."""
import asyncio, sqlite3, sys, tempfile
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import analytics

PRIVATE, GROUP = NS(type="private"), NS(type="supergroup")


def msg(uid, text=None, photo=None, chat=PRIVATE):
    return NS(message=NS(from_user=NS(id=uid), chat=chat, text=text, photo=photo, web_app_data=None),
              callback_query=None)


def cb(uid, data, chat=PRIVATE):
    return NS(message=None, callback_query=NS(from_user=NS(id=uid), data=data, message=NS(chat=chat)))


class State:
    async def get_state(self):
        return "Form:waiting_for_repair"


def run():
    ok = 0
    db = Path(tempfile.mkdtemp()) / "a.db"
    analytics.init(db, "test-salt")
    analytics.set_buttons({"🛠 Поломки", "📖 FAQ"})

    assert analytics.classify(msg(1, "/start")) == (1, "command", "/start"); ok += 1
    assert analytics.classify(msg(1, "🛠 Поломки")) == (1, "button", "🛠 Поломки"); ok += 1
    assert analytics.classify(msg(1, "Когда пересдача по матану?")) == (1, "message", None), \
        "короткий вопрос — это обращение, текст не сохраняем"; ok += 1
    assert analytics.classify(msg(1, "Длинный текст обращения про сломанный кран в аудитории 305")) == (1, "message", None); ok += 1
    assert analytics.classify(msg(1, photo=[1])) == (1, "photo", None); ok += 1
    assert analytics.classify(cb(2, "npb_3")) == (2, "callback", "npb_3"); ok += 1
    assert analytics.classify(msg(9, "Ок, принято", chat=GROUP)) is None, "рабочий чат админов не считаем"; ok += 1
    assert analytics.classify(cb(9, "npb_1", chat=GROUP)) is None; ok += 1
    assert analytics.classify(NS(message=None, callback_query=None)) is None; ok += 1

    async def handler(event, data):
        return "handled"
    mw = analytics.AnalyticsMiddleware()
    res = asyncio.run(mw(handler, msg(5, "🛠 Поломки"), {"state": State()}))
    assert res == "handled", "middleware не должен мешать обработке"; ok += 1
    analytics.track(5, "report_sent", "repair", "bot")
    analytics.track(6, "report_sent", "repair", "webapp")       # из Mini App, кнопку в боте не нажимал
    analytics.track(6, "report_sent", "repair", "webapp")
    analytics.track(7, "admin_reply_delivered")                  # пассивное событие

    rows = sqlite3.connect(db).execute("SELECT user_hash, event, label, state FROM events ORDER BY id").fetchall()
    assert [r[1] for r in rows] == ["button", "report_sent", "report_sent", "report_sent", "admin_reply_delivered"]; ok += 1
    assert rows[0][3] == "Form:waiting_for_repair", "FSM-состояние записано"; ok += 1
    assert rows[0][0] == analytics.user_hash(5) and rows[0][0] != "5", "ID захеширован"; ok += 1

    text = analytics.report(db)
    assert "DAU 2" in text, f"пассивный ответ админа не делает пользователя активным: {text}"; ok += 1
    assert "открыли 1 чел. → отправили 1 (100%)" in text, f"воронка только по боту и по людям: {text}"; ok += 1

    analytics._salt = b"other"
    assert analytics.user_hash(5) != rows[0][0], "другая соль — другой хеш"; ok += 1

    analytics._conn.close()                              # база недоступна
    assert asyncio.run(mw(handler, msg(6, "/start"), {})) == "handled", "ошибка базы не роняет бота"; ok += 1
    print(f"✅ analytics: {ok}/{ok} проверок пройдено")


if __name__ == "__main__":
    run()

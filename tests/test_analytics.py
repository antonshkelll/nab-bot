"""Тесты аналитики без aiogram: фейковые апдейты, запись событий и отчёт."""
import asyncio, sqlite3, sys, tempfile, time
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import analytics


def msg(uid, text=None, photo=None):
    return NS(message=NS(from_user=NS(id=uid), text=text, photo=photo, web_app_data=None), callback_query=None)


def cb(uid, data):
    return NS(message=None, callback_query=NS(from_user=NS(id=uid), data=data))


class State:
    async def get_state(self):
        return "Form:waiting_for_repair"


def run():
    ok = 0
    db = Path(tempfile.mkdtemp()) / "a.db"
    analytics.init(db, "test-salt")

    assert analytics.classify(msg(1, "/start")) == (1, "command", "/start"); ok += 1
    assert analytics.classify(msg(1, "🛠 Поломки")) == (1, "button", "🛠 Поломки"); ok += 1
    assert analytics.classify(msg(1, "Длинный текст обращения про сломанный кран в аудитории 305")) == (1, "message", None), \
        "текст обращения не сохраняем"; ok += 1
    assert analytics.classify(msg(1, photo=[1])) == (1, "photo", None); ok += 1
    assert analytics.classify(cb(2, "npb_3")) == (2, "callback", "npb_3"); ok += 1
    assert analytics.classify(NS(message=None, callback_query=None)) is None; ok += 1

    async def handler(event, data):
        return "handled"
    mw = analytics.AnalyticsMiddleware()
    res = asyncio.run(mw(handler, msg(5, "🛠 Поломки"), {"state": State()}))
    assert res == "handled", "middleware не должен мешать обработке"; ok += 1
    analytics.track(5, "report_sent", "repair", "bot")
    analytics.track(5, "admin_reply_delivered")

    rows = sqlite3.connect(db).execute("SELECT user_hash, event, label, state FROM events ORDER BY id").fetchall()
    assert [r[1] for r in rows] == ["button", "report_sent", "admin_reply_delivered"]; ok += 1
    assert rows[0][3] == "Form:waiting_for_repair", "FSM-состояние записано"; ok += 1
    assert all(r[0] == analytics.user_hash(5) and "5" != r[0] for r in rows), "ID захеширован"; ok += 1

    analytics._salt = b"other"
    assert analytics.user_hash(5) != rows[0][0], "другая соль — другой хеш"; ok += 1

    text = analytics.report(db)
    assert "DAU 1" in text and "repair 1" in text and "Поломки" in text, text; ok += 1

    broken = analytics.AnalyticsMiddleware()
    analytics._conn.close()                              # база недоступна
    assert asyncio.run(broken(handler, msg(6, "/start"), {})) == "handled", "ошибка базы не роняет бота"; ok += 1
    print(f"✅ analytics: {ok}/{ok} проверок пройдено")


if __name__ == "__main__":
    run()

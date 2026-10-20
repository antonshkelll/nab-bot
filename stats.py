"""
Метрики для Пульта и ежемесячная выгрузка обращений в Excel.

Статистика анонимная: считаются только события по дням и хеши посетителей
с солью периода (см. store.seen) — по ним нельзя узнать человека.
"""
from __future__ import annotations

import datetime as dt
import json
import time

import config
import store
import xlsx
from content import MSK, MONTHS, attendees, now_msk, poll_view, rsvp_counts
from delivery import KINDS, STATUS_TITLE
from reports import overdue, thread

MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]


def _days(n: int) -> list[str]:
    d0 = now_msk().date()
    return [(d0 - dt.timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def _top(name: str, since: str, limit: int = 10) -> list[dict]:
    return [{"key": r["key"], "n": r["n"]} for r in store.q(
        "SELECT key, SUM(n) n FROM stat WHERE name=? AND day>=? GROUP BY key ORDER BY n DESC LIMIT ?", (name, since, limit))]


def _sum(name: str, since: str, key: str | None = None) -> int:
    if key is None:
        return store.count("SELECT SUM(n) FROM stat WHERE name=? AND day>=?", (name, since))
    return store.count("SELECT SUM(n) FROM stat WHERE name=? AND key=? AND day>=?", (name, key, since))


def _hours(a, b) -> float | None:
    return round((b - a) / 3600, 1) if a and b and b >= a else None


def report_metrics(since_ts: float) -> dict:
    rows = store.q("SELECT * FROM reports WHERE created_at>=?", (since_ts,))
    take = [_hours(r["created_at"], r["taken_at"]) for r in rows if r["taken_at"]]
    solve = [_hours(r["created_at"], r["resolved_at"]) for r in rows if r["resolved_at"]]
    rated = [r["rating"] for r in rows if r["rating"]]
    return {
        "total": len(rows),
        "byKind": {k: sum(1 for r in rows if r["kind"] == k) for k in KINDS},
        "byStatus": {s: sum(1 for r in rows if (r["status"] or "new") == s) for s in STATUS_TITLE},
        "anon": sum(1 for r in rows if r["anon"]),
        "fromApp": sum(1 for r in rows if (r["source"] or "") == "app"),
        "avgTakeH": round(sum(take) / len(take), 1) if take else None,
        "avgSolveH": round(sum(solve) / len(solve), 1) if solve else None,
        "avgRating": round(sum(rated) / len(rated), 2) if rated else None,
        "ratings": {str(i): rated.count(i) for i in range(1, 6)},
        "reviews": [{"id": r["id"], "rating": r["rating"], "text": r["rating_text"]}
                    for r in sorted(rows, key=lambda r: -(r["rated_at"] or 0)) if r["rating_text"]][:8],
    }


def dashboard(days: int = 30) -> dict:
    days = max(7, min(90, int(days)))
    series_days = _days(days)
    since = series_days[0]
    t = time.gmtime(time.time() + config.TZ_OFFSET * 3600)
    now = time.time()
    dau = [{"day": d, "n": store.uniques("d:" + d), "opens": _sum_day("open", d)} for d in series_days]
    open_all = store.q("SELECT status FROM reports WHERE COALESCE(status,'new')<>'done'")
    return {
        "users": {
            "total": store.count("SELECT COUNT(*) FROM users"),
            "reachable": store.count("SELECT COUNT(*) FROM users WHERE blocked=0"),
            "new7": store.count("SELECT COUNT(*) FROM users WHERE created_at>=?", (now - 7 * 86400,)),
            "new30": store.count("SELECT COUNT(*) FROM users WHERE created_at>=?", (now - 30 * 86400,)),
        },
        "active": {"today": store.uniques("d:" + series_days[-1]),
                   "week": store.uniques("w:" + time.strftime("%G-W%V", t)),
                   "month": store.uniques("m:" + time.strftime("%Y-%m", t))},
        "series": dau,
        "screens": _top("screen", since),
        "taps": _top("tap", since),
        "searches": _top("search", since, 12),
        "searchEmpty": _top("search_empty", since, 8),
        "platforms": _top("platform", since, 6),
        "botButtons": _top("bot", since, 10),
        "reports": dict(report_metrics(now - days * 86400),
                        open=len(open_all), unowned=sum(1 for r in open_all if (r["status"] or "new") == "new"),
                        overdue=len(overdue())),
        "rsvp": {"yes": _sum("rsvp", since, "yes"), "no": _sum("rsvp", since, "no")},
        "pollVotes": _sum("poll_vote", since),
        "days": days,
    }


def _sum_day(name: str, day: str) -> int:
    return store.count("SELECT SUM(n) FROM stat WHERE name=? AND day=?", (name, day))


# ================================================================ выгрузка в Excel

def _fmt(ts) -> str:
    if not ts:
        return ""
    return dt.datetime.fromtimestamp(ts, MSK).strftime("%d.%m.%Y %H:%M")


def month_bounds(year: int, month: int) -> tuple[float, float]:
    a = dt.datetime(year, month, 1, tzinfo=MSK)
    b = dt.datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=MSK)
    return a.timestamp(), b.timestamp()


def prev_month(today: dt.date | None = None) -> tuple[int, int]:
    d = (today or now_msk().date()).replace(day=1) - dt.timedelta(days=1)
    return d.year, d.month


def export_month(year: int, month: int) -> tuple[bytes, str, str]:
    """Возвращает (xlsx, имя файла, короткая подпись)."""
    if not (2020 <= int(year) <= 2100 and 1 <= int(month) <= 12):
        year, month = prev_month()
    a, b = month_bounds(year, month)
    rows = store.q("SELECT * FROM reports WHERE created_at>=? AND created_at<? ORDER BY id", (a, b))
    head = ["№", "Дата", "Тип", "Откуда", "Статус", "Анонимно", "Автор", "Где", "Текст обращения",
            "Взял в работу", "Когда взяли", "Часов до взятия", "Решил", "Когда решено", "Часов до решения",
            "Оценка", "Отзыв", "Сообщений в переписке"]
    data = [head]
    for r in rows:
        msgs = [m for m in thread(r["id"]) if m["author"] in ("team", "user")]
        data.append([r["id"], _fmt(r["created_at"]), KINDS.get(r["kind"], KINDS["question"]).word,
                     "приложение" if r["source"] == "app" else "бот", STATUS_TITLE.get(r["status"] or "new", ""),
                     bool(r["anon"]), "" if r["anon"] else (r["user_name"] or ""), r["location"] or "", r["text"] or "",
                     r["taken_by"] or "", _fmt(r["taken_at"]), _hours(r["created_at"], r["taken_at"]),
                     r["resolved_by"] or "", _fmt(r["resolved_at"]), _hours(r["created_at"], r["resolved_at"]),
                     r["rating"] or "", r["rating_text"] or "", len(msgs)])
    m = report_metrics(a)
    m_rows = [r for r in rows]
    take = [_hours(r["created_at"], r["taken_at"]) for r in m_rows if r["taken_at"]]
    solve = [_hours(r["created_at"], r["resolved_at"]) for r in m_rows if r["resolved_at"]]
    rated = [r["rating"] for r in m_rows if r["rating"]]
    title = f"{MONTHS_NOM[month - 1].capitalize()} {year}"
    summary = [["Показатель", "Значение"],
               ["Период", title],
               ["Всего обращений", len(m_rows)]]
    summary += [[f"— {k.word}", sum(1 for r in m_rows if r["kind"] == code)] for code, k in KINDS.items()]
    summary += [["Анонимных", sum(1 for r in m_rows if r["anon"])],
                ["Из приложения", sum(1 for r in m_rows if r["source"] == "app")],
                ["Решено", sum(1 for r in m_rows if r["status"] == "done")],
                ["В работе", sum(1 for r in m_rows if r["status"] == "work")],
                ["Без ответственного", sum(1 for r in m_rows if (r["status"] or "new") == "new")],
                ["Среднее время до взятия, ч", round(sum(take) / len(take), 1) if take else "—"],
                ["Среднее время до решения, ч", round(sum(solve) / len(solve), 1) if solve else "—"],
                ["Средняя оценка", round(sum(rated) / len(rated), 2) if rated else "—"],
                ["Оценок", len(rated)]]
    summary += [[f"— оценок «{i}»", rated.count(i)] for i in range(5, 0, -1)]
    d0, d1 = dt.date(year, month, 1).isoformat(), dt.datetime.fromtimestamp(b, MSK).date().isoformat()
    summary += [["Уникальных посетителей приложения", store.uniques(f"m:{year:04d}-{month:02d}")],
                ["Открытий приложения", store.count("SELECT SUM(n) FROM stat WHERE name='open' AND day>=? AND day<?", (d0, d1))]]
    ev_rows = [["Дата", "Анонс", "Тип", "Место", "Придут", "Не придут", "Кто придёт"]]
    for e in store.q("SELECT * FROM events WHERE date>=? AND date<? ORDER BY date", (d0, d1)):
        c = rsvp_counts(e["id"])
        ev_rows.append([e["date"] + (" " + e["time"] if e["time"] else ""), e["title"], e["kind"], e["place"] or "",
                        c["yes"], c["no"], ", ".join(attendees(e["id"])["yes"])])
    poll_rows = [["Опрос", "Вариант", "Голосов", "Проголосовали всего"]]
    for p in store.q("SELECT * FROM polls WHERE created_at>=? AND created_at<? ORDER BY id", (a, b)):
        v = poll_view(p, admin=True)
        for i, o in enumerate(v["options"]):
            poll_rows.append([v["question"] if i == 0 else "", o, (v["votes"] or [0] * 99)[i], v["voters"] if i == 0 else ""])
    data_bytes = xlsx.build([
        ("Обращения", data, [6, 16, 16, 13, 17, 10, 18, 18, 60, 18, 16, 10, 18, 16, 10, 8, 40, 10]),
        ("Сводка", summary, [38, 18]),
        ("Анонсы и явка", ev_rows, [16, 40, 12, 20, 9, 10, 60]),
        ("Опросы", poll_rows, [44, 36, 10, 12]),
    ])
    fname = f"Обращения ССт НАБ — {title}.xlsx"
    caption = (f"📤 <b>Отчёт по обращениям · {title}</b>\n"
               f"Всего: <b>{len(m_rows)}</b> · решено: <b>{sum(1 for r in m_rows if r['status'] == 'done')}</b>"
               + (f" · средняя оценка: <b>{round(sum(rated) / len(rated), 1)}</b> ⭐" if rated else ""))
    return data_bytes, fname, caption


async def send_export(bot, chat_ids, year: int, month: int) -> int:
    from aiogram.types import BufferedInputFile
    from tgutil import safe
    raw, fname, caption = export_month(year, month)
    ok = 0
    for cid in chat_ids:
        m = await safe(bot.send_document(chat_id=cid, document=BufferedInputFile(raw, filename=fname), caption=caption, parse_mode="HTML"))
        ok += m is not None
    return ok

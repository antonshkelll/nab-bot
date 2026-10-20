"""Фоновые задачи бота: напоминания об анонсах, просроченные обращения, сводка, ежемесячный отчёт."""
from __future__ import annotations

import asyncio
import logging
import time

import config
import content
import reports
import stats
import store

log = logging.getLogger("sprosinab.scheduler")

RETRY_AFTER = 30 * 60          # если отправка не удалась — повтор через 30 минут, а не каждую минуту
_retry_at: dict[str, float] = {}


def _wait(name: str) -> bool:
    return time.time() < _retry_at.get(name, 0)


def _failed(name: str) -> None:
    _retry_at[name] = time.time() + RETRY_AFTER


async def weekly_digest(bot) -> bool:
    """Понедельник, 10:00 МСК — сводка в рабочий чат."""
    if store.setting("digest_on", "1") != "1":
        return False
    n = content.now_msk()
    if n.weekday() == 0 and n.hour < 10:       # в понедельник — с 10:00; если бот лежал, догоняем позже на неделе
        return False
    key = n.strftime("%G-%V")
    if store.setting("digest_sent") is None:     # сразу после установки не шлём — начнём со следующей недели
        store.set_setting("digest_sent", key)
        return False
    if store.setting("digest_sent") == key or _wait("digest"):
        return False
    if await reports.send_digest(bot):
        store.set_setting("digest_sent", key)
        return True
    _failed("digest")
    log.warning("Сводку отправить не удалось — повторю через %s мин", RETRY_AFTER // 60)
    return False


async def monthly_export(bot) -> bool:
    """1-го числа после 10:00 МСК — таблица обращений за прошлый месяц каждому админу в личку."""
    if store.setting("export_on", "1") != "1":
        return False
    n = content.now_msk()
    if n.day == 1 and n.hour < 10:                # 1-го — с 10:00; если бот лежал, догоняем позже в месяце
        return False
    y, m = stats.prev_month(n.date())
    key = f"{y:04d}-{m:02d}"
    if store.setting("export_sent") is None:      # сразу после установки не шлём — начнём со следующего месяца
        store.set_setting("export_sent", key)
        return False
    if store.setting("export_sent") == key or _wait("export"):
        return False
    ok = await stats.send_export(bot, config.ADMIN_IDS, y, m)
    if ok:
        store.set_setting("export_sent", key)
    else:
        _failed("export")
    log.info("Ежемесячный отчёт за %s отправлен: %s админам", key, ok)
    return ok > 0


async def tick(bot) -> None:
    for name, job in (("events", content.event_tick), ("sla", reports.sla_tick),
                      ("digest", weekly_digest), ("export", monthly_export)):
        try:
            await job(bot)
        except Exception:  # noqa: BLE001
            log.exception("Фоновая задача %s упала — продолжаю", name)


async def run(bot) -> None:
    await asyncio.sleep(20)
    while True:
        t0 = time.time()
        await tick(bot)
        await asyncio.sleep(max(10, 60 - (time.time() - t0)))

"""
Проверка подписи Telegram initData.

Вынесено в отдельный модуль без внешних зависимостей, чтобы логику
можно было покрыть тестами (см. tests/test_security.py).

Алгоритм — из официальной документации Telegram:
  secret_key   = HMAC_SHA256(key="WebAppData", msg=<токен бота>)
  check_string = "\\n".join(f"{k}={v}" for k in sorted(полей без hash))
  hash         = HMAC_SHA256(key=secret_key, msg=check_string).hexdigest()
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import NamedTuple
from urllib.parse import parse_qsl


class InitDataResult(NamedTuple):
    """Результат проверки. reason нужен, чтобы в логе было видно причину отказа."""
    user: dict | None
    reason: str          # "ok" | "empty" | "malformed" | "no_hash" | "bad_hash" | "expired" | "no_user"
    detail: str = ""     # человекочитаемая подробность для журнала


def check_init_data(init_data: str, token: str, max_age: int = 86400) -> InitDataResult:
    if not init_data:
        return InitDataResult(None, "empty", "Telegram не передал initData")
    if not token:
        return InitDataResult(None, "empty", "не задан токен бота")

    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return InitDataResult(None, "malformed", "initData не разбирается как query-строка")

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return InitDataResult(None, "no_hash", "в initData нет поля hash")

    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()

    def signed(fields: dict) -> str:
        check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
        return hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    # Telegram считает hash по ВСЕМ полям, кроме самого hash — поле signature
    # (Bot API 7.10+) в их число входит, исключать его нельзя.
    matched = hmac.compare_digest(signed(parsed), received_hash)

    # Запасной вариант для клиентов, которые ведут себя иначе: без signature.
    if not matched and "signature" in parsed:
        without_signature = {k: v for k, v in parsed.items() if k != "signature"}
        matched = hmac.compare_digest(signed(without_signature), received_hash)

    if not matched:
        return InitDataResult(
            None, "bad_hash",
            "подпись не сходится — токен бота в .env не тот, через которого открыто приложение",
        )

    if max_age:
        try:
            auth_date = int(parsed.get("auth_date", 0))
        except (TypeError, ValueError):
            return InitDataResult(None, "expired", "поле auth_date нечитаемо")
        age = time.time() - auth_date
        if age > max_age:
            return InitDataResult(
                None, "expired",
                f"подписи {int(age // 3600)} ч — либо приложение висело открытым, "
                f"либо на сервере сбиты часы (проверьте: timedatectl)",
            )
        if age < -3600:
            return InitDataResult(
                None, "expired",
                f"подпись из будущего на {int(-age // 60)} мин — на сервере сбиты часы (timedatectl)",
            )

    try:
        user = json.loads(parsed.get("user", "{}"))
    except json.JSONDecodeError:
        return InitDataResult(None, "no_user", "поле user не разбирается")

    if not isinstance(user, dict) or not user.get("id"):
        return InitDataResult(None, "no_user", "в initData нет пользователя")

    return InitDataResult(user, "ok")


def validate_init_data(init_data: str, token: str, max_age: int = 86400) -> dict | None:
    """Короткая форма: словарь пользователя или None."""
    return check_init_data(init_data, token, max_age).user

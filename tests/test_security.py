"""Тесты проверки подписи Telegram initData."""
import hashlib, hmac, json, sys, time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from security import validate_init_data

TOKEN = "123456:TEST-TOKEN-abcdef"


def make_init_data(token=TOKEN, user=None, auth_date=None, extra=None, break_hash=False,
                   sign_over_all=True):
    """
    sign_over_all=True  — как считает Telegram: hash по всем полям, включая signature.
    sign_over_all=False — устаревшее поведение: signature исключён из подписи.
    """
    user = user or {"id": 777, "first_name": "Аня", "username": "anya"}
    fields = {
        "auth_date": str(int(auth_date if auth_date is not None else time.time())),
        "query_id": "AAH1",
        "user": json.dumps(user, ensure_ascii=False, separators=(",", ":")),
    }
    if extra:
        fields.update(extra)
    signed_fields = fields if sign_over_all else {k: v for k, v in fields.items() if k != "signature"}
    check = "\n".join(f"{k}={signed_fields[k]}" for k in sorted(signed_fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if break_hash:
        h = ("0" if h[0] != "0" else "1") + h[1:]
    parts = [f"{k}={quote(v, safe='')}" for k, v in fields.items()] + [f"hash={h}"]
    return "&".join(parts)


def run():
    ok = 0

    u = validate_init_data(make_init_data(), TOKEN)
    assert u and u["id"] == 777 and u["username"] == "anya", "валидная подпись должна проходить"; ok += 1

    assert validate_init_data(make_init_data(break_hash=True), TOKEN) is None, "испорченный hash"; ok += 1
    assert validate_init_data(make_init_data(), "999:OTHER") is None, "чужой токен"; ok += 1
    assert validate_init_data("", TOKEN) is None, "пустая строка"; ok += 1
    assert validate_init_data("user=x&hash=abc", TOKEN) is None, "мусор"; ok += 1

    old = make_init_data(auth_date=time.time() - 200000)
    assert validate_init_data(old, TOKEN) is None, "протухшая подпись"; ok += 1
    assert validate_init_data(old, TOKEN, max_age=0) is not None, "max_age=0 отключает проверку срока"; ok += 1

    # signature (Bot API 7.10+): Telegram включает его в подпись — это и был баг,
    # из-за которого приложение получало bad_hash на реальных клиентах
    real = make_init_data(extra={"signature": "aGVsbG8td29ybGQtc2ln"}, sign_over_all=True)
    u3 = validate_init_data(real, TOKEN)
    assert u3 and u3["id"] == 777, "initData с signature (как шлёт Telegram) должен проходить"; ok += 1

    # и запасной путь: клиент, который signature в подпись не включил
    legacy = make_init_data(extra={"signature": "aGVsbG8td29ybGQtc2ln"}, sign_over_all=False)
    assert validate_init_data(legacy, TOKEN) is not None, "вариант без signature тоже принимается"; ok += 1

    # но подделать всё равно нельзя
    assert validate_init_data(real, "999:OTHER") is None, "чужой токен не проходит и с signature"; ok += 1

    # кириллица и эмодзи в имени
    u2 = validate_init_data(make_init_data(user={"id": 5, "first_name": "Мусо 🚀"}), TOKEN)
    assert u2 and u2["first_name"] == "Мусо 🚀", "юникод в имени"; ok += 1

    # пользователь без id не принимается
    assert validate_init_data(make_init_data(user={"first_name": "X"}), TOKEN) is None, "нет id"; ok += 1

    print(f"✅ security: {ok}/12 проверок пройдено")


if __name__ == "__main__":
    run()

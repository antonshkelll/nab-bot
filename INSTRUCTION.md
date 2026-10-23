# Спроси ССт НАБ v3: установка на сервер

Полная инструкция с картинками-подсказками есть на веб-странице «Запуск Спроси ССт НАБ».
Коротко:

1. MobaXterm → панель файлов → путь `/root/` → перетащите `sprosinab.zip`.
2. Вставьте одну команду:

   ```bash
   cd /root && rm -rf /tmp/nab_new && unzip -oq sprosinab.zip -d /tmp/nab_new && bash /tmp/nab_new/sprosinab/install.sh
   ```

3. На вопросы (токен, домен, почта) можно отвечать Enter, тогда подставится текущее значение.
4. @BotFather → /mybots → @sprosinab_bot → Bot Settings → Configure Mini App → Enable Mini App → `https://app.nablabel.ru`.
   Аватарка: /setuserpic → `webapp/assets/brand/avatar.png`.
5. Проверка: /start у бота, тестовый вопрос в суперчат, Reply.

## Команда nab

| Команда | Что делает |
|---|---|
| `nab status` | работает ли бот |
| `nab restart` | перезапуск |
| `nab log` | последние 60 строк журнала |
| `nab doctor` | диагностика: токен, домен, часы, HTTPS |
| `nab update` | обновиться из `/root/sprosinab.zip` |
| `nab publish` | выложить приложение после правок в `webapp/` |
| `nab docs` | пересобрать страницы НПБ после замены PDF |
| `nab clean` | удалить старые версии (спрашивает про каждую) |

## Удалить прошлые версии

Это делает `nab clean`. Вручную:

```bash
systemctl is-active sprosinab                     # должно быть active
grep -l mybot2 /etc/systemd/system/*.service      # службы старого бота
rm -rf /root/mybot2 /root/sprosinab_new /tmp/nab_new /root/sprosinab.zip
ls -d /root/sprosinab_backup_*                    # затем rm -rf нужной
```

В `/root/bot` лежит другой бот, его не трогаем. Установщик и `nab clean` эту папку не удаляют.

## Фирменные шрифты

Положите файлы TD Mars / Caviar Dreams / Carolina в `/root/sprosinab/webapp/assets/fonts/`
в формате WOFF с именами, которые ждёт `webapp/styles.css`: `td-mars.woff`, `caviar-dreams.woff`, `caviar-dreams-bold.woff`, `carolina.woff`. Затем выполните `nab publish`.
Без них используются Unbounded, Comfortaa и Great Vibes.

## Тесты

```bash
python3 tests/test_security.py   # подпись initData
python3 tests/test_core.py       # обращения, анонимность, анонсы, опросы, API
python3 tests/test_bot.py        # /start, профиль бота, команды
NAB_TEST=1 SPROSINAB_LIVE=/tmp/x SPROSINAB_WWW=/tmp/w bash install.sh --yes   # пробный прогон установщика
```

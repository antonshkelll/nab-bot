#!/usr/bin/env bash
# =============================================================================
#   Установщик «Спроси ССт НАБ» — бот + Mini App, версия 2
#
#   Одна команда — и всё встаёт само:
#       cd /root && unzip -oq sprosinab.zip -d /tmp/nab_new && bash /tmp/nab_new/sprosinab/install.sh
#
#   Что делает (по шагам, с понятными сообщениями):
#     1. ставит нужные программы (python, nginx, certbot, poppler, imagemagick);
#     2. спрашивает токен и домен — подставляя то, что уже было настроено;
#     3. проверяет токен у Telegram и сам узнаёт юзернейм бота;
#     4. останавливает старую копию этого же бота (/root/mybot2), если она
#        запущена — два бота с одним токеном мешают друг другу;
#     5. ставит код в /root/sprosinab, сохраняя .env, базу, документы;
#     6. собирает страницы НПБ, выкладывает приложение, настраивает nginx и SSL;
#     7. запускает службу и проверяет, что всё отвечает;
#     8. ставит команду `nab` — статус, перезапуск, журнал одной командой.
#
#   Папку /root/bot (другой бот) установщик не трогает НИКОГДА.
#
#   Ключи:
#     --update   обновление без вопросов (берёт настройки из текущего .env)
#     --yes      не задавать вопросов, если всё уже настроено
#     --no-ssl   не трогать сертификат
#     --no-apt   не ставить системные пакеты
# =============================================================================

set -Eeuo pipefail

VERSION="3.0"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIVE="${SPROSINAB_LIVE:-/root/sprosinab}"
WWW="${SPROSINAB_WWW:-/var/www/sprosinab}"
OLD_BOT_DIR="${SPROSINAB_OLD:-/root/mybot2}"
PROTECTED="/root/bot"                 # другой бот — не трогаем
SERVICE="sprosinab"
TEST="${NAB_TEST:-0}"                 # 1 — пробный прогон без apt/systemd/nginx (для проверки)
STAMP="$(date +%Y%m%d-%H%M%S)"

MODE="install"; YES=0; SKIP_SSL=0; SKIP_APT=0
for a in "$@"; do
  case "$a" in
    --update) MODE="update"; YES=1 ;;
    --yes|-y) YES=1 ;;
    --no-ssl) SKIP_SSL=1 ;;
    --no-apt) SKIP_APT=1 ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "Неизвестный ключ: $a (см. --help)"; exit 1 ;;
  esac
done

# ---------- оформление ----------
B=$'\033[1m'; G=$'\033[1;32m'; Y=$'\033[1;33m'; R=$'\033[1;31m'; C=$'\033[1;36m'; N=$'\033[0m'
STEP=0; TOTAL=9
step() { STEP=$((STEP + 1)); printf '\n%s[%s/%s] %s%s\n' "$C" "$STEP" "$TOTAL" "$*" "$N"; }
ok()   { printf '   %s✔%s %s\n' "$G" "$N" "$*"; }
info() { printf '   · %s\n' "$*"; }
warn() { printf '   %s⚠ %s%s\n' "$Y" "$*" "$N"; }
die()  { printf '\n%s❌ %s%s\n\n' "$R" "$*" "$N" >&2; exit 1; }
trap 'printf "\n%s❌ Установка прервалась на строке %s. Ничего страшного: исправьте причину выше и запустите установщик ещё раз — он продолжит с того же места.%s\n" "$R" "$LINENO" "$N" >&2' ERR

have() { command -v "$1" >/dev/null 2>&1; }
tty_ok() { [ -r /dev/tty ] && [ "$YES" = 0 ]; }
ask() {            # ask "Вопрос" "по умолчанию" → ответ
  local q="$1" def="${2:-}" ans=""
  if tty_ok; then
    if [ -n "$def" ]; then printf '   %s%s%s [%s]: ' "$B" "$q" "$N" "$def" >/dev/tty; else printf '   %s%s%s: ' "$B" "$q" "$N" >/dev/tty; fi
    IFS= read -r ans </dev/tty || true
  fi
  ans="$(printf '%s' "$ans" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  printf '%s' "${ans:-$def}"
}
yesno() {          # yesno "Вопрос" Y|N
  local def="${2:-Y}" a
  if ! tty_ok; then [ "$def" = "Y" ]; return; fi
  a="$(ask "$1 (да/нет)" "$([ "$def" = Y ] && echo да || echo нет)")"
  case "$(printf '%s' "$a" | tr '[:upper:]' '[:lower:]')" in д*|y*) return 0 ;; *) return 1 ;; esac
}
envget() { grep -E "^$1=" "$2" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r' | sed "s/^[\"']//; s/[\"']\$//" || true; }
envset() {         # envset KEY VALUE FILE
  local k="$1" v="$2" f="$3"
  if grep -qE "^$k=" "$f" 2>/dev/null; then
    local tmp; tmp="$(mktemp)"
    awk -v k="$k" -v v="$v" 'BEGIN{FS=OFS="="} $1==k {print k"="v; next} {print}' "$f" >"$tmp" && cat "$tmp" >"$f" && rm -f "$tmp"
  else
    printf '%s=%s\n' "$k" "$v" >>"$f"
  fi
}
mask() { local t="$1"; [ -n "$t" ] && printf '%s:…%s' "${t%%:*}" "${t: -4}" || printf 'нет'; }

# ---------- защита от ошибок ----------
printf '\n%s╔══════════════════════════════════════════════╗\n║   Спроси ССт НАБ · установщик v%s          ║\n╚══════════════════════════════════════════════╝%s\n' "$G" "$VERSION" "$N"

[ "$TEST" = 1 ] || [ "$(id -u)" = 0 ] || die "Запустите от root (в MobaXterm вы и так под root)."
[ -f "$SRC/main.py" ] && [ -f "$SRC/webapp/index.html" ] || die "Рядом с install.sh нет main.py или webapp/ — архив распакован не полностью."
case "$(readlink -m "$LIVE")" in
  "$PROTECTED"|"$PROTECTED"/*) die "Папка $LIVE — это другой бот. Сюда ставить нельзя." ;;
esac
[ "$(readlink -m "$SRC")" != "$(readlink -m "$LIVE")" ] || die "Запускайте установщик из распакованного архива (/tmp/nab_new/sprosinab), а не из $LIVE."

info "Код бота:        $LIVE"
info "Сайт приложения: $WWW"
info "Другой бот $PROTECTED — не трогаю"

# =============================================================================
step "Системные программы"
# =============================================================================
if [ "$TEST" = 1 ] || [ "$SKIP_APT" = 1 ]; then
  info "пропускаю (пробный режим или --no-apt)"
elif have apt-get; then
  export DEBIAN_FRONTEND=noninteractive
  info "обновляю список пакетов…"
  apt-get update -qq >/dev/null 2>&1 || warn "apt update ответил с ошибкой — пробую продолжить"
  PKGS="python3 python3-venv python3-pip nginx certbot python3-certbot-nginx poppler-utils imagemagick curl unzip rsync ca-certificates"
  info "ставлю: $PKGS"
  apt-get install -y -qq $PKGS >/dev/null 2>&1 || die "Не удалось поставить пакеты. Запустите вручную: apt-get install -y $PKGS"
  if ls "$SRC"/docs/*.docx "$LIVE"/docs/*.docx "$OLD_BOT_DIR"/docs/*.docx >/dev/null 2>&1 && ! have libreoffice; then
    info "есть .docx — ставлю libreoffice-writer (пару минут)…"
    apt-get install -y -qq libreoffice-writer >/dev/null 2>&1 || warn "libreoffice не встал — .docx не будут листаться в приложении"
  fi
  ok "всё нужное установлено"
else
  warn "это не Debian/Ubuntu — поставьте python3-venv, nginx, certbot, poppler-utils, imagemagick вручную"
fi

# =============================================================================
step "Настройки: токен и домен"
# =============================================================================
CUR_ENV="$LIVE/.env"
TOKEN="${BOT_TOKEN:-$(envget BOT_TOKEN "$CUR_ENV")}"
[ "$TOKEN" = "ВСТАВЬТЕ_ТОКЕН_СЮДА" ] && TOKEN=""
if [ -z "$TOKEN" ] && [ -d "$OLD_BOT_DIR" ]; then
  TOKEN="$(grep -rhoE '[0-9]{8,12}:[A-Za-z0-9_-]{30,}' "$OLD_BOT_DIR"/*.py "$OLD_BOT_DIR"/.env 2>/dev/null | head -1 || true)"
  [ -n "$TOKEN" ] && info "нашёл токен в старом боте $OLD_BOT_DIR"
fi
[ -z "$TOKEN" ] && TOKEN="$(envget BOT_TOKEN "$SRC/.env" 2>/dev/null || true)"
[ "$TOKEN" = "ВСТАВЬТЕ_ТОКЕН_СЮДА" ] && TOKEN=""

DOMAIN="${NAB_DOMAIN:-$(envget WEBAPP_URL "$CUR_ENV" | sed -E 's#^https?://##; s#/.*$##')}"
DOMAIN="${DOMAIN:-app.nablabel.ru}"
EMAIL="${NAB_EMAIL:-$(envget CERTBOT_EMAIL "$CUR_ENV")}"

check_token() {    # → печатает username или пусто
  local t="$1" me
  [ "$TEST" = 1 ] && { printf '%s' "${NAB_TEST_USERNAME:-sprosinab_bot}"; return; }
  me="$(curl -fsS --max-time 12 "https://api.telegram.org/bot${t}/getMe" 2>/dev/null || true)"
  printf '%s' "$me" | grep -q '"ok":true' || return 0
  printf '%s' "$me" | sed -n 's/.*"username":"\([^"]*\)".*/\1/p'
}

if [ -n "$TOKEN" ]; then info "токен сейчас: $(mask "$TOKEN")"; fi
if tty_ok; then
  echo "   Токен берётся у @BotFather → /mybots → бот → API Token."
  NEWT="$(ask "Вставьте токен (Enter — оставить текущий)" "")"
  [ -n "$NEWT" ] && TOKEN="$NEWT"
fi
[ -n "$TOKEN" ] || die "Нет токена. Запустите установщик ещё раз и вставьте токен из @BotFather."

USERNAME=""
for try in 1 2 3; do
  USERNAME="$(check_token "$TOKEN")"
  [ -n "$USERNAME" ] && break
  warn "Telegram не принял токен $(mask "$TOKEN") (или нет интернета)."
  tty_ok || die "Проверьте токен в $CUR_ENV и интернет на сервере."
  TOKEN="$(ask "Вставьте токен ещё раз" "")"
  [ -n "$TOKEN" ] || die "Токен не введён."
done
[ -n "$USERNAME" ] || die "Токен так и не подошёл."
ok "токен рабочий — это бот @$USERNAME"

if tty_ok; then
  DOMAIN="$(ask "Домен для приложения" "$DOMAIN")"
  EMAIL="$(ask "Почта для писем про SSL (можно пропустить — Enter)" "$EMAIL")"
fi
DOMAIN="$(printf '%s' "$DOMAIN" | sed -E 's#^https?://##; s#/.*$##' | tr '[:upper:]' '[:lower:]')"
printf '%s' "$DOMAIN" | grep -qE '^[a-z0-9.-]+\.[a-z]{2,}$' || die "Домен «$DOMAIN» выглядит неправильно. Пример: app.nablabel.ru"
ok "домен приложения: https://$DOMAIN"

# =============================================================================
step "Старая копия этого же бота"
# =============================================================================
stop_old() {
  local found=0 units pids p cwd
  units="$(grep -lsE "$OLD_BOT_DIR(/|\"|'|\s|$)" /etc/systemd/system/*.service 2>/dev/null | xargs -r -n1 basename | grep -vx "$SERVICE.service" || true)"
  pids=""
  for p in $(pgrep -f python 2>/dev/null || true); do
    [ "$p" = "$$" ] && continue
    cwd="$(readlink "/proc/$p/cwd" 2>/dev/null || true)"
    if [ "$cwd" = "$OLD_BOT_DIR" ] || tr '\0' ' ' <"/proc/$p/cmdline" 2>/dev/null | grep -qE "$OLD_BOT_DIR/"; then
      pids="$pids $p"
    fi
  done
  [ -n "$units$pids" ] || { ok "старая копия ($OLD_BOT_DIR) не запущена — конфликта нет"; return; }
  found=1
  warn "старый бот из $OLD_BOT_DIR сейчас работает с тем же токеном."
  info "Два бота с одним токеном отбирают друг у друга сообщения — старый нужно остановить."
  [ -n "$units" ] && info "службы: $units"
  [ -n "$pids" ]  && info "процессы:$pids"
  if yesno "Остановить старую копию? Папку при этом НЕ удаляю" Y; then
    for u in $units; do systemctl stop "$u" 2>/dev/null || true; systemctl disable "$u" 2>/dev/null || true; ok "служба $u остановлена и выключена"; done
    for p in $pids; do kill "$p" 2>/dev/null || true; done
    sleep 2
    for p in $pids; do kill -0 "$p" 2>/dev/null && kill -9 "$p" 2>/dev/null || true; done
    [ -n "$pids" ] && ok "процессы старого бота остановлены"
    info "если старый бот запускался через screen/tmux — он уже остановлен вместе с процессом"
  else
    warn "оставляю старый бот работать — возможны пропавшие сообщения"
  fi
  : "$found"
}
if [ "$TEST" = 1 ]; then info "пробный режим — пропускаю"; else stop_old; fi

# =============================================================================
step "Код бота в $LIVE (данные сохраняю)"
# =============================================================================
KEEP="$(mktemp -d)"
if [ -d "$LIVE" ]; then
  # бот останавливается на время копирования — база не меняется посреди переноса
  [ "$TEST" = 1 ] || systemctl stop "$SERVICE" 2>/dev/null || true
  BACKUP="${LIVE}_backup_${STAMP}"
  mkdir -p "$BACKUP"
  tar -C "$LIVE" --exclude=./venv --exclude='./__pycache__' -cf - . | tar -C "$BACKUP" -xf -
  ok "резервная копия: $BACKUP"
  [ -f "$LIVE/.env" ]         && cp -a "$LIVE/.env" "$KEEP/.env"
  [ -f "$LIVE/users_nab.db" ] && cp -a "$LIVE/users_nab.db" "$KEEP/users_nab.db"
  [ -d "$LIVE/data" ]         && { mkdir -p "$KEEP/data"; cp -a "$LIVE/data/." "$KEEP/data/" 2>/dev/null || true; }
  [ -d "$LIVE/docs" ]         && { mkdir -p "$KEEP/docs"; cp -a "$LIVE/docs/." "$KEEP/docs/" 2>/dev/null || true; }
  [ -d "$LIVE/webapp/assets/docs" ]  && { mkdir -p "$KEEP/rendered"; cp -a "$LIVE/webapp/assets/docs/." "$KEEP/rendered/" 2>/dev/null || true; }
  [ -d "$LIVE/webapp/assets/fonts" ] && { mkdir -p "$KEEP/fonts"; cp -a "$LIVE/webapp/assets/fonts/." "$KEEP/fonts/" 2>/dev/null || true; }
  [ -d "$LIVE/venv" ]         && mv "$LIVE/venv" "$KEEP/venv"
  rm -rf "${LIVE:?}"
else
  info "первая установка"
fi

mkdir -p "$LIVE"
tar -C "$SRC" --exclude=./venv --exclude='./__pycache__' --exclude=./.env --exclude=./users_nab.db --exclude=./data -cf - . | tar -C "$LIVE" -xf -

[ -f "$KEEP/.env" ]         && cp -a "$KEEP/.env" "$LIVE/.env"
[ -f "$KEEP/users_nab.db" ] && cp -a "$KEEP/users_nab.db" "$LIVE/users_nab.db"
[ -d "$KEEP/data" ]         && { mkdir -p "$LIVE/data"; cp -a "$KEEP/data/." "$LIVE/data/" 2>/dev/null || true; }
[ -d "$KEEP/docs" ]         && cp -an "$KEEP/docs/." "$LIVE/docs/" 2>/dev/null || true
# собранные страницы НПБ — поверх пустого manifest.json из архива
if [ -d "$KEEP/rendered" ] && grep -q '"id"' "$KEEP/rendered/manifest.json" 2>/dev/null; then
  mkdir -p "$LIVE/webapp/assets/docs"; cp -a "$KEEP/rendered/." "$LIVE/webapp/assets/docs/" 2>/dev/null || true
fi
if [ -d "$KEEP/fonts" ];    then mkdir -p "$LIVE/webapp/assets/fonts"; cp -an "$KEEP/fonts/." "$LIVE/webapp/assets/fonts/" 2>/dev/null || true; fi
[ -d "$KEEP/venv" ]         && mv "$KEEP/venv" "$LIVE/venv"

# документы и подписчики из старого бота — если их ещё нет
if [ -d "$OLD_BOT_DIR/docs" ]; then
  mkdir -p "$LIVE/docs"
  n0="$(find "$LIVE/docs" -maxdepth 1 -type f | wc -l)"
  cp -n "$OLD_BOT_DIR"/docs/*.pdf "$OLD_BOT_DIR"/docs/*.docx "$LIVE/docs/" 2>/dev/null || true
  n1="$(find "$LIVE/docs" -maxdepth 1 -type f | wc -l)"
  if [ "$n1" -gt "$n0" ]; then ok "перенёс $((n1 - n0)) документ(ов) из $OLD_BOT_DIR/docs"; fi
fi
if [ ! -f "$LIVE/users_nab.db" ] && [ -f "$OLD_BOT_DIR/users_nab.db" ]; then
  cp -a "$OLD_BOT_DIR/users_nab.db" "$LIVE/users_nab.db" && ok "перенёс базу подписчиков из старого бота"
fi
rm -rf "$KEEP"

# .env
[ -f "$LIVE/.env" ] || cp "$LIVE/.env.example" "$LIVE/.env"
envset BOT_TOKEN "$TOKEN" "$LIVE/.env"
envset BOT_USERNAME "$USERNAME" "$LIVE/.env"
envset WEBAPP_URL "https://$DOMAIN" "$LIVE/.env"
[ -n "$EMAIL" ] && envset CERTBOT_EMAIL "$EMAIL" "$LIVE/.env"
grep -qE '^SET_BOT_PROFILE=' "$LIVE/.env" || envset SET_BOT_PROFILE 1 "$LIVE/.env"
# ключ для шифрования связи с авторами анонимных обращений — создаётся один раз и больше не меняется
grep -qE '^SECRET_KEY=.+' "$LIVE/.env" || envset SECRET_KEY "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" "$LIVE/.env"
grep -qE '^NOTIFY_ADMINS_DM=' "$LIVE/.env" || envset NOTIFY_ADMINS_DM 0 "$LIVE/.env"
chmod 600 "$LIVE/.env"
chmod +x "$LIVE"/install.sh "$LIVE"/tools/*.sh "$LIVE"/tools/*.py 2>/dev/null || true
ok "код обновлён, .env настроен (токен $(mask "$TOKEN"), бот @$USERNAME)"
info "документов в docs/: $(find "$LIVE/docs" -maxdepth 1 -type f | wc -l)"

# =============================================================================
step "Python и библиотеки"
# =============================================================================
if [ "$TEST" = 1 ]; then
  info "пробный режим — пропускаю"
else
  [ -x "$LIVE/venv/bin/python" ] || python3 -m venv "$LIVE/venv"
  "$LIVE/venv/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 || true
  "$LIVE/venv/bin/pip" install --quiet -r "$LIVE/requirements.txt" || die "pip не смог поставить библиотеки — проверьте интернет на сервере"
  ok "aiogram и aiohttp на месте ($("$LIVE/venv/bin/python" -c 'import aiogram; print("aiogram", aiogram.__version__)' 2>/dev/null))"
fi

# =============================================================================
step "Страницы НПБ для листалки"
# =============================================================================
SIG_FILE="$LIVE/webapp/assets/docs/.sources"
BUILD_FORMAT="pages+links-1"   # меняется, когда меняется формат сборки — тогда страницы пересоберутся
SIG="$( (echo "$BUILD_FORMAT"; cd "$LIVE/docs" && ls -l --time-style=+%s -- [0-9]* 2>/dev/null | awk '{print $5, $6, $7, $8, $9, $10}') | md5sum | cut -c1-32 || true)"
HAS_NPB="$( (ls "$LIVE"/docs/[1-9]* 2>/dev/null || true) | wc -l)"
if [ "$HAS_NPB" = 0 ]; then
  warn "в $LIVE/docs нет файлов НПБ («1. Устав….pdf» …) — в приложении их можно будет запросить только в чат"
elif [ -f "$SIG_FILE" ] && [ "$(cat "$SIG_FILE")" = "$SIG" ] && grep -q '"id"' "$LIVE/webapp/assets/docs/manifest.json" 2>/dev/null; then
  ok "страницы уже собраны, документы не менялись"
elif [ "$TEST" = 1 ] || ! have pdftoppm; then
  warn "пропускаю сборку страниц (нет pdftoppm или пробный режим)"
else
  info "собираю страницы из $HAS_NPB документов — это 1–5 минут…"
  if "$LIVE/venv/bin/python" "$LIVE/tools/build_docs.py" >/tmp/nab_build_docs.log 2>&1; then
    printf '%s' "$SIG" >"$SIG_FILE"
    ok "готово: $(find "$LIVE/webapp/assets/docs" -name '*.webp' -not -path '*/thumb/*' | wc -l) страниц"
  else
    warn "сборка страниц не удалась — журнал: /tmp/nab_build_docs.log (бот при этом работает)"
  fi
fi

# =============================================================================
step "Сайт приложения: nginx и SSL"
# =============================================================================
NGINX_CONF="/etc/nginx/sites-available/sprosinab"
if [ "$TEST" = 1 ]; then
  WWW="${WWW:-/tmp/nab_www}"
fi
mkdir -p "$WWW"
if have rsync; then rsync -a --delete "$LIVE/webapp/" "$WWW/"; else cp -a "$LIVE/webapp/." "$WWW/"; fi
chown -R www-data:www-data "$WWW" 2>/dev/null || true
chmod -R a+rX "$WWW"
ok "приложение выложено в $WWW ($(find "$WWW" -type f | wc -l) файлов)"

if [ "$TEST" = 1 ]; then
  info "пробный режим — nginx и certbot не трогаю"
  sed "s/app\.nablabel\.ru/$DOMAIN/g; s#/var/www/sprosinab#$WWW#g" "$LIVE/deploy/nginx.conf" >"$WWW/../nab_nginx_test.conf"
elif ! have nginx; then
  warn "nginx не установлен — пропускаю"
else
  if [ -f "$NGINX_CONF" ] && grep -q "ssl_certificate" "$NGINX_CONF" && grep -q "server_name $DOMAIN" "$NGINX_CONF"; then
    ok "конфиг nginx с HTTPS уже есть — оставляю как есть"
  else
    sed "s/app\.nablabel\.ru/$DOMAIN/g; s#/var/www/sprosinab#$WWW#g" "$LIVE/deploy/nginx.conf" >"$NGINX_CONF"
    ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/sprosinab
    ok "конфиг nginx записан для $DOMAIN"
  fi
  nginx -t >/dev/null 2>&1 || { nginx -t; die "nginx ругается на конфиг (текст выше)"; }
  systemctl enable --quiet nginx 2>/dev/null || true
  systemctl reload nginx 2>/dev/null || systemctl restart nginx
  ok "nginx перезагружен"

  if [ "$SKIP_SSL" = 1 ]; then
    info "SSL пропущен (--no-ssl)"
  elif grep -q "ssl_certificate" "$NGINX_CONF"; then
    ok "HTTPS уже настроен"
  else
    SERVER_IP="$(curl -4 -fsS --max-time 8 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
    DOMAIN_IP="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}')"
    if [ -z "$DOMAIN_IP" ]; then
      warn "домен $DOMAIN пока никуда не указывает. В панели домена добавьте A-запись: ${DOMAIN%%.*} → $SERVER_IP, подождите 10–30 минут и запустите установщик ещё раз."
    elif [ "$DOMAIN_IP" != "$SERVER_IP" ]; then
      warn "домен $DOMAIN указывает на $DOMAIN_IP, а этот сервер — $SERVER_IP. Исправьте A-запись и запустите установщик ещё раз."
    else
      info "домен $DOMAIN → $SERVER_IP, выпускаю бесплатный сертификат Let's Encrypt…"
      MAILARG=(--register-unsafely-without-email); [ -n "$EMAIL" ] && MAILARG=(-m "$EMAIL")
      if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect --keep-until-expiring "${MAILARG[@]}" >/tmp/nab_certbot.log 2>&1; then
        ok "HTTPS включён, сертификат продлевается сам"
      else
        warn "certbot не смог выпустить сертификат — журнал: /tmp/nab_certbot.log"
      fi
    fi
  fi
fi

# =============================================================================
step "Служба бота"
# =============================================================================
if [ "$TEST" = 1 ] || ! have systemctl; then
  info "пробный режим — службу не запускаю"
else
  sed "s#/root/sprosinab#$LIVE#g" "$LIVE/deploy/sprosinab.service" >/etc/systemd/system/$SERVICE.service
  systemctl daemon-reload
  systemctl enable --quiet $SERVICE 2>/dev/null || true
  systemctl restart $SERVICE
  for i in $(seq 1 15); do
    sleep 1
    curl -fsS --max-time 2 http://127.0.0.1:8080/api/health >/dev/null 2>&1 && break
  done
  if systemctl is-active --quiet $SERVICE; then ok "служба $SERVICE работает"; else
    journalctl -u $SERVICE -n 25 --no-pager || true
    die "служба не поднялась — последние строки журнала выше"
  fi
  if curl -fsS --max-time 3 http://127.0.0.1:8080/api/health >/dev/null 2>&1; then ok "API приложения отвечает"; else warn "API пока не отвечает — посмотрите: nab log"; fi
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "https://$DOMAIN/" 2>/dev/null || true)"
  [ "$CODE" = "200" ] && ok "https://$DOMAIN открывается" || warn "https://$DOMAIN пока отвечает кодом «${CODE:-нет ответа}» — см. шаг SSL выше"
fi

# =============================================================================
step "Команда nab — управление в одно слово"
# =============================================================================
if [ "$TEST" = 1 ]; then
  info "пробный режим — пропускаю"
else
  install -m 755 "$LIVE/tools/nab.sh" /usr/local/bin/nab
  ok "готово: nab status · nab restart · nab log · nab doctor · nab update"
fi

# =============================================================================
printf '\n%s══════════════════════════════════════════════%s\n' "$G" "$N"
printf '%s ✅ Готово! Спроси ССт НАБ v%s установлен.%s\n' "$G" "$VERSION" "$N"
printf '%s══════════════════════════════════════════════%s\n' "$G" "$N"
cat <<EOF

 Бот:         https://t.me/$USERNAME
 Приложение:  https://$DOMAIN
 Код:         $LIVE
${BACKUP:+ Старая версия сохранена: $BACKUP
}
 ${B}Осталось один раз в @BotFather${N} (кнопка «Open App» в профиле и в списке чатов):
   /mybots → @$USERNAME → Bot Settings → Configure Mini App → Enable Mini App
   → пришлите ссылку  https://$DOMAIN

 Управление:  nab status   — работает ли бот
              nab restart  — перезапустить
              nab log      — последние сообщения журнала
              nab doctor   — полная диагностика
 Удалить старые версии:  bash $LIVE/tools/cleanup_old.sh

EOF
trap - ERR

#!/usr/bin/env bash
# =============================================================================
#  Диагностика «Спроси ССт НАБ».
#
#      bash /root/sprosinab/tools/doctor.sh
#
#  Показывает всё, что нужно, чтобы понять причину ошибки в приложении.
#  Токен целиком нигде не печатается — только номер бота до двоеточия.
# =============================================================================

set -uo pipefail

LIVE="${SPROSINAB_LIVE:-/root/sprosinab}"
ENV_FILE="$LIVE/.env"

ok()   { printf '  \033[1;32m✔\033[0m %s\n' "$*"; }
bad()  { printf '  \033[1;31m✘\033[0m %s\n' "$*"; }
info() { printf '  · %s\n' "$*"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

echo "═══════════════════════════════════════════════"
echo " Диагностика «Спроси ССт НАБ»"
echo "═══════════════════════════════════════════════"

# ---------- 1. Служба ----------
head_ "1. Служба"
if systemctl is-active --quiet sprosinab 2>/dev/null; then
  ok "sprosinab работает"
else
  bad "sprosinab НЕ работает — смотрите: journalctl -u sprosinab -n 40 --no-pager"
fi

# ---------- 2. Токен ----------
head_ "2. Токен и бот"
TOKEN="$(grep -E '^BOT_TOKEN=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r\n[:space:]')"
if [ -z "$TOKEN" ] || [ "$TOKEN" = "ВСТАВЬТЕ_ТОКЕН_СЮДА" ]; then
  bad "В $ENV_FILE не вписан токен"
else
  info "Номер бота в .env: ${TOKEN%%:*}"
  ME="$(curl -fsS --max-time 10 "https://api.telegram.org/bot${TOKEN}/getMe" 2>/dev/null || true)"
  if [ -z "$ME" ]; then
    bad "Telegram не ответил — нет интернета или токен отозван"
  elif echo "$ME" | grep -q '"ok":true'; then
    USERNAME="$(echo "$ME" | sed -n 's/.*"username":"\([^"]*\)".*/\1/p')"
    ok "Токен рабочий, принадлежит боту @${USERNAME}"
    echo
    printf '  \033[1;33m→ Приложение нужно открывать именно у @%s\033[0m\n' "$USERNAME"
    printf '    Если открываете у другого бота — будет ошибка bad_hash.\n'
  else
    bad "Telegram отверг токен: $(echo "$ME" | sed -n 's/.*"description":"\([^"]*\)".*/\1/p')"
    info "Значит токен отозван. Возьмите новый в @BotFather и впишите в .env"
  fi
fi

BOT_USERNAME_ENV="$(grep -E '^BOT_USERNAME=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r\n[:space:]')"
if [ -n "${USERNAME:-}" ] && [ -n "$BOT_USERNAME_ENV" ]; then
  if [ "${BOT_USERNAME_ENV,,}" = "${USERNAME,,}" ]; then
    ok "BOT_USERNAME в .env совпадает с реальным"
  else
    bad "BOT_USERNAME в .env = «$BOT_USERNAME_ENV», а бот на самом деле @${USERNAME}"
    info "Исправьте BOT_USERNAME, иначе ответы админов через Reply не дойдут"
  fi
fi

# ---------- 3. API ----------
head_ "3. API приложения"
HEALTH="$(curl -fsS --max-time 5 http://127.0.0.1:8080/api/health 2>/dev/null || true)"
if [ -n "$HEALTH" ]; then
  ok "API отвечает"
  echo "$HEALTH" | sed 's/^/    /'
else
  bad "API не ответил на 127.0.0.1:8080"
fi

# ---------- 4. Время ----------
head_ "4. Часы сервера"
NTP="$(timedatectl show -p NTPSynchronized --value 2>/dev/null)"; NTP="${NTP:-unknown}"
info "$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
if [ "$NTP" = "yes" ]; then
  ok "Время синхронизировано"
else
  bad "Время НЕ синхронизировано — включите: timedatectl set-ntp true"
fi

# ---------- 5. Сайт и сертификат ----------
head_ "5. Сайт"
DOMAIN="$(grep -E '^WEBAPP_URL=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | sed 's#https\?://##; s#/$##' | tr -d '\r[:space:]')"
if [ -n "$DOMAIN" ]; then
  info "Домен: $DOMAIN"
  IP="$(getent hosts "$DOMAIN" | awk '{print $1; exit}')"
  MY="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '')"
  [ -n "$IP" ] && info "Домен ведёт на: $IP"
  [ -n "$MY" ] && info "IP этого сервера: $MY"
  if [ -n "$IP" ] && [ -n "$MY" ] && [ "$IP" != "$MY" ]; then
    bad "Домен указывает не на этот сервер — поправьте A-запись"
  fi
  CODE="$(curl -o /dev/null -s -w '%{http_code}' --max-time 8 "https://$DOMAIN" 2>/dev/null)"
  CODE="${CODE:-000}"
  if [ "$CODE" = "200" ]; then
    ok "https://$DOMAIN отвечает 200"
  elif [ "$CODE" = "000" ]; then
    bad "https://$DOMAIN не открывается — нет сертификата или nginx не запущен"
  else
    bad "https://$DOMAIN отвечает $CODE"
  fi
fi

# ---------- 6. Файлы ----------
head_ "6. Файлы"
WWW="${SPROSINAB_WWW:-/var/www/sprosinab}"
[ -f "$WWW/index.html" ] && ok "Приложение выложено в $WWW" || bad "В $WWW нет index.html — запустите tools/publish_webapp.sh"
PAGES=$(find "$LIVE/webapp/assets/docs" -name '*.webp' 2>/dev/null | wc -l)
PDFS=$(find "$LIVE/docs" -maxdepth 1 -name '*.pdf' 2>/dev/null | wc -l)
info "Документов в docs/: $PDFS   ·   страниц НПБ собрано: $PAGES"

# ---------- 7. Последние отказы подписи ----------
head_ "7. Последние отказы подписи (initData)"
LOGS="$(journalctl -u sprosinab -n 200 --no-pager 2>/dev/null | grep -F 'initData отклонён' | tail -5)"
if [ -n "$LOGS" ]; then
  echo "$LOGS" | sed 's/^/    /'
else
  ok "Отказов не было"
fi

echo
echo "═══════════════════════════════════════════════"
echo " Пришлите этот вывод, если что-то помечено ✘"
echo "═══════════════════════════════════════════════"

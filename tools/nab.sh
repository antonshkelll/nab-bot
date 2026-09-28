#!/usr/bin/env bash
# =============================================================================
#  nab — управление ботом «Спроси ССт НАБ» одной командой.
#  Установка: sudo ln -s /opt/sprosinab/tools/nab.sh /usr/local/bin/nab
#
#     nab status    работает ли бот (зелёное active = всё хорошо)
#     nab restart   перезапустить бота
#     nab stop      остановить          nab start   запустить
#     nab log       последние 60 строк журнала
#     nab live      журнал в реальном времени (выход — Ctrl+C)
#     nab doctor    полная диагностика
#     nab publish   заново выложить приложение (после правок в webapp/)
#     nab docs      пересобрать страницы НПБ (после замены PDF в docs/)
#     nab faq       пересобрать FAQ (после замены docs/FAQ.pdf)
#     nab map       пересобрать карту (после замены docs/Карта корпуса.pdf)
# =============================================================================

LIVE="${SPROSINAB_LIVE:-/opt/sprosinab}"
WWW="${SPROSINAB_WWW:-/var/www/sprosinab}"
SVC=sprosinab
G=$'\033[1;32m'; R=$'\033[1;31m'; Y=$'\033[1;33m'; N=$'\033[0m'

case "${1:-help}" in
  status|s)
    if systemctl is-active --quiet $SVC; then
      printf '%s● Бот работает%s  (с %s)\n' "$G" "$N" "$(systemctl show -p ActiveEnterTimestamp --value $SVC)"
    else
      printf '%s● Бот НЕ работает%s — смотрите: nab log\n' "$R" "$N"
    fi
    H="$(curl -fsS --max-time 3 http://127.0.0.1:8080/api/health 2>/dev/null)"
    if [ -n "$H" ]; then printf '%s● API приложения отвечает%s\n' "$G" "$N"; else printf '%s● API приложения молчит%s\n' "$Y" "$N"; fi
    systemctl status $SVC --no-pager -n 5 2>/dev/null | sed -n '1,3p;/^$/,$p'
    ;;
  restart|r) systemctl restart $SVC && sleep 2 && "$0" status ;;
  stop)      systemctl stop $SVC && echo "Бот остановлен. Запустить: nab start" ;;
  start)     systemctl start $SVC && sleep 2 && "$0" status ;;
  log|logs|l) journalctl -u $SVC -n "${2:-60}" --no-pager ;;
  live|f)    journalctl -u $SVC -f ;;
  doctor|d)  bash "$LIVE/tools/doctor.sh" ;;
  publish|p)
    mkdir -p "$WWW"
    if command -v rsync >/dev/null; then rsync -a --delete "$LIVE/webapp/" "$WWW/"; else cp -a "$LIVE/webapp/." "$WWW/"; fi
    chown -R www-data:www-data "$WWW" 2>/dev/null; chmod -R a+rX "$WWW"
    echo "✅ Приложение выложено в $WWW"
    ;;
  docs)
    "$LIVE/venv/bin/python" "$LIVE/tools/build_docs.py" && "$0" publish
    ;;
  faq)
    bash "$LIVE/tools/build_faq.sh" && "$0" publish
    ;;
  map)
    "$LIVE/venv/bin/python" "$LIVE/tools/build_map.py" && "$0" publish
    ;;
  *)
    sed -n '3,15p' "$0" | sed 's/^# \{0,1\}//'
    ;;
esac

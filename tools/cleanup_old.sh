#!/usr/bin/env bash
# =============================================================================
#  Удаление старых версий «Спроси ССт НАБ».
#
#      bash /root/sprosinab/tools/cleanup_old.sh          (или: nab clean)
#
#  Показывает, что нашлось, и про КАЖДЫЙ пункт спрашивает «удалить? да/нет».
#  Никогда не трогает:
#     /root/bot          — это другой бот
#     /root/sprosinab    — текущая рабочая версия
#     .env, базу и документы текущей версии
#  Самую свежую резервную копию предлагает оставить.
# =============================================================================

set -uo pipefail

LIVE="${SPROSINAB_LIVE:-/root/sprosinab}"
OLD_BOT_DIR="${SPROSINAB_OLD:-/root/mybot2}"
PROTECTED="/root/bot"
HOME_DIR="${SPROSINAB_HOME:-/root}"
G=$'\033[1;32m'; Y=$'\033[1;33m'; R=$'\033[1;31m'; B=$'\033[1m'; N=$'\033[0m'

ask() {
  local a
  printf '   %s%s%s (да/нет) [нет]: ' "$B" "$1" "$N"
  IFS= read -r a || a=""
  case "$(printf '%s' "$a" | tr '[:upper:]' '[:lower:]')" in д*|y*) return 0 ;; *) return 1 ;; esac
}
size() { du -sh "$1" 2>/dev/null | cut -f1; }
safe() {             # путь можно удалять?
  local p; p="$(readlink -m "$1")"
  case "$p" in
    "$PROTECTED"|"$PROTECTED"/*) return 1 ;;
    "$(readlink -m "$LIVE")"|"$(readlink -m "$LIVE")"/*) return 1 ;;
    /|/root|/root/|/etc|/usr|/var|/home) return 1 ;;
  esac
  return 0
}
remove() {
  local p="$1"
  safe "$p" || { printf '   %s⛔ %s — защищено, пропускаю%s\n' "$R" "$p" "$N"; return; }
  rm -rf -- "$p" && printf '   %s✔ удалено:%s %s\n' "$G" "$N" "$p"
}

echo "═══════════════════════════════════════════════"
echo " Удаление старых версий «Спроси ССт НАБ»"
echo "═══════════════════════════════════════════════"
printf ' Рабочая версия %s и другой бот %s — не трогаю.\n' "$LIVE" "$PROTECTED"

if ! systemctl is-active --quiet sprosinab 2>/dev/null; then
  printf '\n %s⚠ Сейчас служба sprosinab не работает.%s\n' "$Y" "$N"
  echo "   Сначала убедитесь, что новая версия запущена (nab status), и только потом чистите старое."
  ask "Всё равно продолжить?" || exit 0
fi

# ---------- 1. Старый бот mybot2 ----------
echo
printf '%s1. Старый бот %s%s\n' "$B" "$OLD_BOT_DIR" "$N"
UNITS="$(grep -lsE "$OLD_BOT_DIR(/|\"|'|\s|$)" /etc/systemd/system/*.service 2>/dev/null | grep -v '/sprosinab.service$' || true)"
for u in $UNITS; do
  name="$(basename "$u")"
  printf '   служба %s (запускала старый бот)\n' "$name"
  if ask "Остановить и удалить службу $name?"; then
    systemctl stop "$name" 2>/dev/null; systemctl disable "$name" 2>/dev/null
    rm -f -- "$u" && systemctl daemon-reload && printf '   %s✔ служба %s удалена%s\n' "$G" "$name" "$N"
  fi
done
if [ -d "$OLD_BOT_DIR" ]; then
  RUN=""
  for p in $(pgrep -f python 2>/dev/null); do
    [ "$(readlink "/proc/$p/cwd" 2>/dev/null)" = "$OLD_BOT_DIR" ] && RUN="$RUN $p"
  done
  [ -n "$RUN" ] && printf '   %s⚠ старый бот ещё запущен (процессы:%s) — удаление папки его остановит%s\n' "$Y" "$RUN" "$N"
  printf '   папка %s — %s\n' "$OLD_BOT_DIR" "$(size "$OLD_BOT_DIR")"
  echo "   (документы и база подписчиков оттуда уже перенесены установщиком в $LIVE)"
  if ask "Удалить папку $OLD_BOT_DIR?"; then
    for p in $RUN; do kill "$p" 2>/dev/null; done
    remove "$OLD_BOT_DIR"
  fi
else
  echo "   не найдено — уже удалено"
fi

# ---------- 2. Резервные копии ----------
echo
printf '%s2. Резервные копии прошлых установок%s\n' "$B" "$N"
mapfile -t BACKUPS < <(ls -1dt "$HOME_DIR"/sprosinab_backup_* "$HOME_DIR"/mybot2_backup* "$HOME_DIR"/mybot2.bak* 2>/dev/null)
if [ "${#BACKUPS[@]}" = 0 ]; then
  echo "   нет"
else
  for i in "${!BACKUPS[@]}"; do
    b="${BACKUPS[$i]}"
    if [ "$i" = 0 ]; then
      printf '   %s — %s  %s(самая свежая — советую оставить на неделю)%s\n' "$b" "$(size "$b")" "$Y" "$N"
    else
      printf '   %s — %s\n' "$b" "$(size "$b")"
    fi
    ask "Удалить $b?" && remove "$b"
  done
fi

# ---------- 3. Распакованные архивы и zip ----------
echo
printf '%s3. Архивы и временные папки%s\n' "$B" "$N"
FOUND=0
for p in "$HOME_DIR"/sprosinab_new* "$HOME_DIR"/sprosinab.zip "$HOME_DIR"/sprosinab\ \(*\).zip "$HOME_DIR"/sprosinab-*.zip /tmp/nab_new; do
  [ -e "$p" ] || continue
  FOUND=1
  printf '   %s — %s\n' "$p" "$(size "$p")"
  ask "Удалить $p?" && remove "$p"
done
[ "$FOUND" = 0 ] && echo "   нет"

echo
printf '%s✅ Готово.%s Рабочая версия: %s · проверить: nab status\n' "$G" "$N" "$LIVE"

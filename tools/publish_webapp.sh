#!/usr/bin/env bash
# Копирует Mini App из webapp/ в папку, которую отдаёт nginx.
# Запускайте каждый раз после правок в webapp/ и после build_docs.py:
#     bash /opt/sprosinab/tools/publish_webapp.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/webapp"
DST="${SPROSINAB_WWW:-/var/www/sprosinab}"

[ -d "$SRC" ] || { echo "❌ Нет папки $SRC"; exit 1; }

mkdir -p "$DST"
cp -a "$SRC"/. "$DST"/
chown -R www-data:www-data "$DST" 2>/dev/null || true
chmod -R a+rX "$DST"

echo "✅ Mini App выложен: $DST"
echo "   Файлов: $(find "$DST" -type f | wc -l)"

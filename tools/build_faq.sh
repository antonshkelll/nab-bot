#!/usr/bin/env bash
# Пересобирает картинки FAQ для Mini App из docs/FAQ.pdf.
# Запускать после того, как заменили PDF на новую версию:
#     bash tools/build_faq.sh
#
# Нужны poppler-utils и imagemagick:
#     sudo apt install -y poppler-utils imagemagick

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PDF="$ROOT/docs/FAQ.pdf"
OUT="$ROOT/webapp/assets/faq"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

[ -f "$PDF" ] || { echo "❌ Нет файла $PDF"; exit 1; }

PAGES="$(pdfinfo "$PDF" | awk '/^Pages:/{print $2}')"
echo "📄 Страниц в PDF: $PAGES"

rm -rf "$OUT"
mkdir -p "$OUT/thumb"

pdftoppm -jpeg -r 72 -jpegopt quality=90 "$PDF" "$TMP/p"

for f in "$TMP"/p-*.jpg; do
  n="$(basename "$f" .jpg)"; n="${n#p-}"
  convert "$f" -resize 1000x -quality 78 -strip "$OUT/$n.webp"
  convert "$f" -resize 320x  -quality 70 -strip "$OUT/thumb/$n.webp"
done

echo "✅ Готово: $(ls "$OUT"/*.webp | wc -l) страниц в $OUT"
PY="$ROOT/venv/bin/python"; [ -x "$PY" ] || PY=python3
"$PY" "$ROOT/tools/pdf_links.py" "$PDF" "$OUT" || echo "⚠️  ссылки FAQ не извлечены — страницы будут без кликабельных ссылок"
echo "   Число страниц и разделы приложение берёт из webapp/assets/faq/manifest.json — app.js править не нужно."

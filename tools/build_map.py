#!/usr/bin/env python3
"""
Собирает «Карту корпуса» для приложения из docs/Карта корпуса.pdf.

    ./venv/bin/python tools/build_map.py        (или: nab map)

Что делает:
  • рендерит страницы PDF в картинки webp (webapp/assets/map/);
  • если на сервере есть tesseract (apt install -y tesseract-ocr) и
    pytesseract + scipy (pip), находит на планах номера аудиторий —
    тогда поиск «412» ставит точку прямо на плане. Без них поиск всё равно
    показывает нужный этаж.

Порядок страниц PDF задан в FLOORS ниже — если в новой карте другой порядок,
поправьте список (номер страницы → этаж).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "docs" / "Карта корпуса.pdf"
OUT = ROOT / "webapp" / "assets" / "map"

COVER_PAGE = 1
#   страница, id, файл, подпись, корпус
FLOORS = [
    (2, "-1", "m1", "−1 этаж", "Корпус 15"),
    (3, "1", "1", "1 этаж", "Корпус 15"),
    (4, "2", "2", "2 этаж", "Корпус 15"),
    (5, "3", "3", "3 этаж", "Корпус 15"),
    (6, "4", "4", "4 этаж", "Корпус 15"),
    (7, "5", "5", "5 этаж", "Корпус 15"),
    (8, "1к", "k1", "1 этаж", "Компьютерный корпус 15-1"),
    (9, "2к", "k2", "2 этаж", "Компьютерный корпус 15-1"),
    (10, "3к", "k3", "3 этаж", "Компьютерный корпус 15-1"),
]
LEGEND_X = 0.85          # справа на каждом слайде — легенда, её не распознаём


def ocr_rooms(png: Path) -> dict[str, list]:
    """Номера аудиторий и их место на плане (доли ширины/высоты)."""
    try:
        import numpy as np
        import pytesseract
        from PIL import Image
        from scipy import ndimage
    except ImportError:
        return {}
    im = Image.open(png).convert("L")
    W, H = im.size
    ink = np.asarray(im) < 110

    def long_runs(b, axis, minlen=70):
        m = np.zeros_like(b)
        arr, out = (b, m) if axis == 1 else (b.T, m.T)
        for i, row in enumerate(arr):
            d = np.diff(np.concatenate([[0], row.view(np.int8), [0]]))
            for a, z in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
                if z - a >= minlen:
                    out[i, a:z] = True
        return m

    lines = ndimage.binary_dilation(long_runs(ink, 1) | long_runs(ink, 0), iterations=3)
    txt = ink & ~lines
    lab, n = ndimage.label(txt)
    keep = np.zeros(n + 1, bool)
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if 18 <= h <= 70 and w <= 70 and (lab[sl] == i).sum() > 25:
            keep[i] = True
    glyph = keep[lab]
    wl, _ = ndimage.label(ndimage.binary_dilation(glyph, structure=np.ones((5, 23))))
    found: dict[str, list] = {}
    for sl in ndimage.find_objects(wl):
        y0, y1, x0, x1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
        if x0 > LEGEND_X * W or (x1 - x0) < 25 or (y1 - y0) > 80:
            continue
        crop = np.where(glyph[max(0, y0 - 6):y1 + 6, max(0, x0 - 10):x1 + 10], 0, 255).astype(np.uint8)
        c = Image.fromarray(crop)
        c = c.resize((c.width * 2, c.height * 2))
        pad = Image.new("L", (c.width + 40, c.height + 40), 255)
        pad.paste(c, (20, 20))
        t = pytesseract.image_to_string(pad, config="--psm 7 -c tessedit_char_whitelist=0123456789").strip()
        if re.fullmatch(r"\d{3,4}", t):
            found.setdefault(t, [round((x0 + x1) / 2 / W, 4), round((y0 + y1) / 2 / H, 4)])
    return found


def main() -> None:
    if not PDF.exists():
        sys.exit(f"❌ Нет файла {PDF}")
    if not shutil.which("pdftoppm"):
        sys.exit("❌ Нужен pdftoppm: apt install -y poppler-utils")
    from PIL import Image

    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"title": "Верхняя Масловка, 15", "floors": []}
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", "150", "-png", str(PDF), f"{tmp}/p"], check=True, timeout=900)
        pages = sorted(Path(tmp).glob("p-*.png"))
        def page(n):
            return pages[n - 1] if 0 < n <= len(pages) else None
        cov = page(COVER_PAGE)
        if cov:
            Image.open(cov).convert("RGB").resize((1200, 675)).save(OUT / "cover.webp", quality=80, method=6)
        ocr_on = shutil.which("tesseract") is not None
        for num, fid, slug, title, bld in FLOORS:
            src = page(num)
            if not src:
                continue
            im = Image.open(src).convert("RGB").resize((2400, 1350))
            im.save(OUT / f"{slug}.webp", quality=80, method=6)
            im.resize((360, 203)).save(OUT / f"{slug}-t.webp", quality=70)
            rooms = {}
            if ocr_on:
                pref = fid.rstrip("к").replace("-1", "0")
                for k, xy in ocr_rooms(src).items():
                    if fid.endswith("к"):
                        if k.startswith(pref) and len(k) == 3:
                            rooms[k + "-1"] = xy
                        elif k.startswith(pref) and len(k) == 4 and k[3] in "68":
                            rooms[k[:3] + ("б" if k[3] == "6" else "в") + "-1"] = xy
                    elif len(k) == 3 and k.startswith(pref):
                        rooms[k] = xy
            manifest["floors"].append({"id": fid, "slug": slug, "t": title, "b": bld, "rooms": rooms})
            print(f"   ✅ {title}, {bld}: аудиторий на плане — {len(rooms)}")
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if not shutil.which("tesseract"):
        print("ℹ️  tesseract не установлен — поиск покажет этаж, но без точки на плане.")
    print(f"✅ Карта собрана: {OUT}")


if __name__ == "__main__":
    main()

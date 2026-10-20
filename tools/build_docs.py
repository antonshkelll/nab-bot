#!/usr/bin/env python3
"""
Готовит страницы документов НПБ для просмотра прямо в Mini App.

Что делает:
  1. смотрит в docs/ — берёт файлы 1..9 (PDF или DOCX) и FAQ;
  2. DOCX превращает в PDF (нужен libreoffice);
  3. каждую страницу рендерит в картинку webp + маленькое превью;
  4. пишет webapp/assets/docs/manifest.json — по нему приложение
     понимает, сколько страниц у каждого документа.

Запуск:
    cd /root/sprosinab
    ./venv/bin/python tools/build_docs.py

Нужны пакеты системы:
    apt install -y poppler-utils imagemagick
    apt install -y libreoffice-writer      # только если есть .docx
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
DOCS = ROOT / "docs"
OUT = ROOT / "webapp" / "assets" / "docs"

# Названия разделов НПБ — те же, что в config.py
TITLES = {
    "1": "Устав Финансового университета",
    "2": "Правила внутреннего распорядка обучающихся",
    "3": "Положение о проведении текущего контроля",
    "4": "Правила внутреннего распорядка студенческого общежития",
    "5": "О порядке перехода обучающихся на бесплатное обучение",
    "6": "Положение о курсовой работе",
    "7": "Положение о старосте учебной группы",
    "8": "Положение о порядке предоставления скидок",
    "9": "Положение о Студенческом совете",
}
SUBTITLES = {
    "1": "Главный документ вуза",
    "2": "Права, обязанности, дисциплина",
    "3": "Зачёты, экзамены, пересдачи",
    "4": "Проживание и порядок",
    "5": "Перевод на бюджет",
    "6": "Требования и сроки",
    "7": "Роль и полномочия",
    "8": "Скидки на обучение",
    "9": "Как устроен ССт",
}

# Документы, добавленные через Пульт приложения, — в data/docs.json (его пишет content.py)
_REG = ROOT / "data" / "docs.json"
if _REG.exists():
    try:
        for _d in json.loads(_REG.read_text(encoding="utf-8")):
            TITLES[str(_d["id"])] = _d.get("title") or TITLES.get(str(_d["id"]), "Документ")
            if _d.get("sub") is not None:
                SUBTITLES[str(_d["id"])] = _d.get("sub") or ""
    except (ValueError, KeyError, OSError):
        pass

PAGE_WIDTH = 1100      # ширина страницы-картинки
THUMB_WIDTH = 300
QUALITY = 78


def need(cmd: str, hint: str) -> None:
    if shutil.which(cmd) is None:
        sys.exit(f"❌ Нет программы «{cmd}». Установите: {hint}")


def docx_to_pdf(src: Path, workdir: Path) -> Path | None:
    """Конвертирует DOCX в PDF через libreoffice."""
    if shutil.which("libreoffice") is None:
        print(f"   ⚠️  {src.name}: пропущен — нужен libreoffice "
              f"(apt install -y libreoffice-writer)")
        return None
    subprocess.run(
        ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", str(workdir), str(src)],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300,
    )
    out = workdir / (src.stem + ".pdf")
    return out if out.exists() else None


def page_count(pdf: Path) -> int:
    res = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True)
    m = re.search(r"^Pages:\s+(\d+)", res.stdout, re.M)
    return int(m.group(1)) if m else 0


def render(pdf: Path, dest: Path) -> int:
    """Рендерит все страницы PDF в webp. Возвращает число страниц."""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "thumb").mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["pdftoppm", "-jpeg", "-r", "110", "-jpegopt", "quality=92", str(pdf), f"{tmp}/p"],
            check=True, timeout=900,
        )
        pages = sorted(Path(tmp).glob("p-*.jpg"))
        for i, src in enumerate(pages, 1):
            name = f"{i:03d}.webp"
            subprocess.run(["convert", str(src), "-resize", f"{PAGE_WIDTH}x",
                            "-quality", str(QUALITY), "-strip", str(dest / name)], check=True)
            subprocess.run(["convert", str(src), "-resize", f"{THUMB_WIDTH}x",
                            "-quality", "68", "-strip", str(dest / "thumb" / name)], check=True)
    return len(pages)


def page_ratio(dest: Path) -> float:
    """Ширина / высота первой страницы — приложение заранее резервирует место."""
    res = subprocess.run(["identify", "-format", "%w %h", str(dest / "001.webp")],
                         capture_output=True, text=True)
    try:
        w, h = (int(x) for x in res.stdout.split()[:2])
        return round(w / h, 4)
    except (ValueError, ZeroDivisionError):
        return 0.707


def find_source(number: str) -> Path | None:
    """Ищет файл документа по номеру: «1. …pdf» или «1. …docx»."""
    for ext in ("pdf", "docx", "doc"):
        for p in sorted(DOCS.glob(f"{number}.*.{ext}")) + sorted(DOCS.glob(f"{number} *.{ext}")):
            return p
    return None


def main() -> None:
    need("pdftoppm", "apt install -y poppler-utils")
    need("pdfinfo", "apt install -y poppler-utils")
    need("convert", "apt install -y imagemagick")

    if not DOCS.exists():
        sys.exit(f"❌ Нет папки {DOCS}")

    OUT.mkdir(parents=True, exist_ok=True)
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    manifest: list[dict] = []
    if only:                      # пересобираем один документ, остальные берём из прежнего manifest
        try:
            old = json.loads((OUT / "manifest.json").read_text(encoding="utf-8")).get("docs", [])
        except (OSError, ValueError):
            old = []
        manifest = [d for d in old if d.get("id") != only and find_source(d.get("id", "")) is not None]
        for d in manifest:
            d["title"] = TITLES.get(d["id"], d.get("title", ""))
            d["subtitle"] = SUBTITLES.get(d["id"], d.get("subtitle", ""))

    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir)

        for number, title in TITLES.items():
            if only and number != only:
                continue
            src = find_source(number)
            if src is None:
                print(f"⚪ {number}. {title} — файла нет, пропускаю")
                continue

            pdf = src
            if src.suffix.lower() in (".docx", ".doc"):
                print(f"🔄 {src.name} — конвертирую в PDF…")
                pdf = docx_to_pdf(src, work)
                if pdf is None:
                    continue

            slug = f"npb{number}"
            dest = OUT / slug
            shutil.rmtree(dest, ignore_errors=True)

            total = page_count(pdf)
            print(f"📄 {number}. {title} — {total} стр., рендерю…")
            made = render(pdf, dest)
            links = 0
            try:                                  # ссылки внутри документа → нажимаются в приложении
                sys.path.insert(0, str(ROOT / "tools"))
                from pdf_links import build as build_links
                links = build_links(pdf, dest)
            except Exception as e:  # noqa: BLE001
                print(f"   ⚠️  ссылки не извлечены: {e}")

            manifest.append({
                "id": number,
                "slug": slug,
                "title": title,
                "subtitle": SUBTITLES.get(number, ""),
                "pages": made,
                "ratio": page_ratio(dest) if made else 0.707,
                "file": src.name,
            })
            print(f"   ✅ {made} страниц, ссылок: {links} → assets/docs/{slug}/")

    manifest.sort(key=lambda d: int(d["id"]) if str(d["id"]).isdigit() else 999)
    (OUT / "manifest.json").write_text(
        json.dumps({"docs": manifest}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    total_pages = sum(d["pages"] for d in manifest)
    print()
    if manifest:
        print(f"✅ Готово: {len(manifest)} документов, {total_pages} страниц.")
    else:
        print("⚠️  Ни одного документа не нашлось. Положите файлы в docs/ "
              "с именами, начинающимися с номера: «1. Устав ….pdf»")
    print("\nТеперь выложите приложение:")
    print("   bash /root/sprosinab/tools/publish_webapp.sh")


if __name__ == "__main__":
    main()

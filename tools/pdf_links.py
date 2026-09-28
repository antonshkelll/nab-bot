#!/usr/bin/env python3
"""
Достаёт из PDF всё, что нужно, чтобы страницы-картинки в приложении были
«живыми»:

  • ссылки (ruz.fa.ru, t.me/…, почта) — их прямоугольники на странице;
  • оглавление-«Навигацию»: строки вроде «5. Общежитие» превращаются в
    переходы на нужную страницу внутри листалки (если есть pdfplumber);
  • текст каждой страницы — для поиска по FAQ.

Приложение накладывает на картинку прозрачные кнопки ровно поверх ссылок,
поэтому дизайн страниц не меняется, а ссылки нажимаются.

    python3 tools/pdf_links.py docs/FAQ.pdf webapp/assets/faq
    → webapp/assets/faq/links.json и text.json

Нужен pypdf (есть в requirements.txt). pdfplumber — по желанию.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def normalize(uri: str) -> str:
    """http://t.me/@name → https://t.me/name — так Telegram открывает внутри себя."""
    uri = (uri or "").strip()
    m = re.match(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/@?(.+)$", uri, re.I)
    if m:
        return "https://t.me/" + m.group(1)
    if uri.startswith("http://"):
        return "https://" + uri[len("http://"):]
    return uri


def _norm_text(s: str) -> str:
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[^a-zа-я0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_links(pdf_path: Path) -> dict[str, list]:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    out: dict[str, list] = {}
    for num, page in enumerate(reader.pages, 1):
        box = page.mediabox
        pw, ph = float(box.width), float(box.height)
        x_off, y_off = float(box.left), float(box.bottom)
        items = []
        for ref in page.get("/Annots") or []:
            a = ref.get_object()
            if a.get("/Subtype") != "/Link" or "/Rect" not in a:
                continue
            href = None
            act = a.get("/A")
            if act is not None:
                act = act.get_object()
                if act.get("/URI"):
                    href = normalize(str(act.get("/URI")))
                elif act.get("/S") == "/GoTo":
                    dest = act.get("/D")
                    href = _goto(reader, dest)
            elif a.get("/Dest") is not None:
                href = _goto(reader, a.get("/Dest"))
            if not href:
                continue
            x0, y0, x1, y1 = (float(v) for v in a["/Rect"])
            x0, x1 = sorted((x0 - x_off, x1 - x_off))
            y0, y1 = sorted((y0 - y_off, y1 - y_off))
            items.append([round(x0 / pw, 4), round(1 - y1 / ph, 4),
                          round((x1 - x0) / pw, 4), round((y1 - y0) / ph, 4), href])
        if items:
            out[str(num)] = items
    return out


def _goto(reader, dest) -> str | None:
    try:
        if hasattr(dest, "get_object"):
            dest = dest.get_object()
        if isinstance(dest, str) or (hasattr(dest, "startswith") and not isinstance(dest, list)):
            named = reader.named_destinations.get(str(dest))
            if named is not None:
                return f"#page={reader.get_destination_page_number(named) + 1}"
        if isinstance(dest, list) and dest:
            ref = dest[0]
            if isinstance(ref, int):
                return f"#page={ref + 1}"
            for i, pg in enumerate(reader.pages):
                if getattr(pg, "indirect_reference", None) == ref or pg.get_object() == ref.get_object():
                    return f"#page={i + 1}"
    except Exception:  # noqa: BLE001
        return None
    return None


def extract_text(pdf_path: Path) -> list[str]:
    try:                                   # pdfplumber аккуратнее расставляет пробелы
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            return [re.sub(r"[ \t]+", " ", p.extract_text() or "").strip() for p in pdf.pages]
    except Exception:  # noqa: BLE001
        pass
    from pypdf import PdfReader

    texts = []
    for page in PdfReader(str(pdf_path)).pages:
        try:
            t = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            t = ""
        texts.append(re.sub(r"[ \t]+", " ", t).strip())
    return texts


def navigation_links(pdf_path: Path, texts: list[str], chapters: list | None = None) -> dict[str, list]:
    """Строки оглавления → переходы на страницы (нужен pdfplumber).
    В chapters (если передан список) складываются разделы верхнего уровня:
    [{"t": "Общежитие", "from": 14}, …] — из них приложение делает кнопки разделов."""
    try:
        import pdfplumber
    except ImportError:
        return {}

    out: dict[str, list] = {}
    with pdfplumber.open(str(pdf_path)) as pdf:
        heads = []
        for page in pdf.pages:
            try:
                lines_ = (page.extract_text() or "").splitlines()[:5]
            except Exception:  # noqa: BLE001
                lines_ = []
            heads.append(_norm_text(" ".join(lines_)))
        for pi, page in enumerate(pdf.pages):
            words = page.extract_words(keep_blank_chars=False)
            if not words or not any("навигац" in w["text"].lower() for w in words[:3]):
                continue
            rows: list[list] = []
            for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
                if rows and abs(rows[-1][0]["top"] - w["top"]) < 10:
                    rows[-1].append(w)
                else:
                    rows.append([w])
            items = []
            for ws in rows:
                raw = " ".join(w["text"] for w in ws).strip()
                text = _norm_text(raw)
                text = re.sub(r"^\d+ ", "", text)
                if not text or "навигац" in text:
                    continue
                target = next((j for j in range(pi + 1, len(heads)) if text in heads[j]), None)
                if target is None:
                    continue
                top = min(w["top"] for w in ws) - 8
                bottom = max(w["bottom"] for w in ws) + 8
                items.append([0.03, round(top / page.height, 4), 0.94,
                              round((bottom - top) / page.height, 4), f"#page={target + 1}"])
                if chapters is not None and not raw.startswith(("—", "-", "–")):
                    label = re.sub(r"^\d+[.)]\s*", "", raw).rstrip(" :")
                    if chapters and chapters[-1]["from"] == target + 1 and chapters[-1].get("_row") == len(items) - 2:
                        chapters[-1]["t"] += " " + label
                    else:
                        chapters.append({"t": label, "from": target + 1})
                    chapters[-1]["_row"] = len(items) - 1
            if items:
                out[str(pi + 1)] = items
    return out


_URLISH = re.compile(
    r"^(?:(?P<mail>[\w.+-]+@[\w-]+(?:\.[\w-]+)+)|(?P<url>(?:https?://)?(?:www\.)?(?:[a-z0-9-]+\.)+(?:ru|com|org|net|me|su|рф)(?:/\S*)?)|(?P<tg>@[A-Za-z0-9_]{4,}))$",
    re.I,
)


def autolink(pdf_path: Path, links: dict[str, list]) -> int:
    """Адреса, написанные в PDF обычным текстом (campus.fa.ru, @nick, почта),
    тоже делаем нажимаемыми — если автор PDF забыл поставить на них ссылку."""
    try:
        import pdfplumber
    except ImportError:
        return 0
    added = 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        for num, page in enumerate(pdf.pages, 1):
            W, H = float(page.width), float(page.height)
            have = links.get(str(num), [])
            for w in page.extract_words():
                word = w["text"].strip().rstrip(".,;:!?)»\"'").lstrip("(«\"'")
                m = _URLISH.match(word)
                if not m:
                    continue
                cx, cy = (w["x0"] + w["x1"]) / 2 / W, (w["top"] + w["bottom"]) / 2 / H
                if any(x <= cx <= x + ww and y <= cy <= y + hh for x, y, ww, hh, _ in have):
                    continue
                if m.group("mail"):
                    href = "mailto:" + m.group("mail")
                elif m.group("tg"):
                    href = "https://t.me/" + m.group("tg")[1:]
                else:
                    href = normalize(word if word.lower().startswith("http") else "https://" + word)
                pad = 3
                item = [round((w["x0"] - pad) / W, 4), round((w["top"] - pad) / H, 4),
                        round((w["x1"] - w["x0"] + 2 * pad) / W, 4), round((w["bottom"] - w["top"] + 2 * pad) / H, 4), href]
                links.setdefault(str(num), []).append(item)
                have = links[str(num)]
                added += 1
    return added


def build(pdf_path: Path, out_dir: Path, with_text: bool = True) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    links = extract_links(pdf_path)
    texts = extract_text(pdf_path) if with_text else []
    autolink(pdf_path, links)
    chapters: list = []
    nav = navigation_links(pdf_path, texts, chapters)
    for page, items in nav.items():
        links.setdefault(page, []).extend(items)
    # манифест: сколько страниц, пропорции и разделы — приложение берёт всё отсюда
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        box = reader.pages[0].mediabox
        pages, ratio = len(reader.pages), round(float(box.width) / float(box.height), 4)
    except Exception:  # noqa: BLE001
        pages, ratio = len(texts), 0.707
    chapters = [{"t": c["t"][:1].upper() + c["t"][1:], "from": c["from"]} for c in chapters]
    if chapters and nav:
        chapters.insert(0, {"t": "Навигация", "from": int(next(iter(nav)))})
    if chapters and chapters[-1]["from"] < pages:
        last = (texts[pages - 1].splitlines() if len(texts) >= pages else [""])
        chapters.append({"t": "Остались вопросы?" if "вопрос" in " ".join(last[:3]).lower() else "Конец", "from": pages})
    (out_dir / "manifest.json").write_text(json.dumps({"pages": pages, "ratio": ratio, "chapters": chapters},
                                                      ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "links.json").write_text(json.dumps(links, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if with_text:
        (out_dir / "text.json").write_text(json.dumps(texts, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return sum(len(v) for v in links.values())


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("Использование: python3 tools/pdf_links.py файл.pdf папка_вывода")
    n = build(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"✅ ссылок и переходов: {n} → {sys.argv[2]}/links.json")

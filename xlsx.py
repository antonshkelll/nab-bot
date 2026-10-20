"""
Минимальная запись .xlsx без сторонних библиотек (на сервере ничего доустанавливать не нужно).

Поддерживает несколько листов, жирную шапку с заливкой, ширину колонок,
закреплённую первую строку, автофильтр, числа и перенос строк в ячейках.
"""
from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref: str, v, style: int) -> str:
    if v is None or v == "":
        return f'<c r="{ref}" s="{style}"/>' if style else ""
    if isinstance(v, bool):
        v = "да" if v else "нет"
    if isinstance(v, (int, float)):
        return f'<c r="{ref}" s="{style}"><v>{v}</v></c>'
    t = escape(str(v)).replace("\r", "")
    # управляющие символы недопустимы в XML
    t = "".join(ch for ch in t if ch in "\n\t" or ord(ch) >= 32)
    return f'<c r="{ref}" t="inlineStr" s="{style}"><is><t xml:space="preserve">{t}</t></is></c>'


def _sheet(rows: list[list], widths: list[int] | None, header: bool) -> str:
    cols = max((len(r) for r in rows), default=1)
    out = [_HEAD, '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
    if header and rows:
        out.append('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
    if widths:
        out.append("<cols>" + "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths)) + "</cols>")
    out.append("<sheetData>")
    for i, row in enumerate(rows):
        style = 1 if header and i == 0 else 2
        cells = "".join(_cell(f"{_col(j)}{i + 1}", v, style) for j, v in enumerate(row))
        out.append(f'<row r="{i + 1}">{cells}</row>')
    out.append("</sheetData>")
    if header and len(rows) > 1:
        out.append(f'<autoFilter ref="A1:{_col(cols - 1)}{len(rows)}"/>')
    out.append("</worksheet>")
    return "".join(out)


def build(sheets: list[tuple[str, list[list], list[int] | None]]) -> bytes:
    """sheets: [(название, строки, ширины колонок)]. Первая строка каждого листа — шапка."""
    buf = io.BytesIO()
    z = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    n = len(sheets)
    z.writestr("[Content_Types].xml", _HEAD +
               '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
               '<Default Extension="xml" ContentType="application/xml"/>'
               '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
               '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
               + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(n))
               + "</Types>")
    z.writestr("_rels/.rels", _HEAD +
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
               "</Relationships>")
    names = []
    for title, _, _ in sheets:
        t = "".join(ch for ch in title if ch not in "[]:*?/\\")[:31] or "Лист"
        names.append(t)
    z.writestr("xl/workbook.xml", _HEAD +
               '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
               + "".join(f'<sheet name="{escape(t)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, t in enumerate(names))
               + "</sheets></workbook>")
    z.writestr("xl/_rels/workbook.xml.rels", _HEAD +
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(n))
               + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
               "</Relationships>")
    z.writestr("xl/styles.xml", _HEAD +
               '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
               '<font><b/><sz val="11"/><color rgb="FFEAFBEA"/><name val="Calibri"/></font></fonts>'
               '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
               '<fill><patternFill patternType="solid"><fgColor rgb="FF112622"/><bgColor indexed="64"/></patternFill></fill></fills>'
               '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
               '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
               '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
               '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>'
               '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
               "</cellXfs>"
               '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
               "</styleSheet>")
    for i, (_, rows, widths) in enumerate(sheets):
        z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet(rows, widths, True))
    z.close()
    return buf.getvalue()

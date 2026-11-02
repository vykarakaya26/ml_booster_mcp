"""Readers for EPDK's monthly sector report files: the listing page, Word (.docx) reports and Excel (.xlsx) annexes.

Everything here is pure parsing of bytes already downloaded by the provider; nothing does I/O. Office files are
untrusted input: archive sizes are capped before anything is decompressed, XML with a DTD is refused (no entity
expansion) and only the parts that hold tables are read.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from xml.etree import ElementTree

from mlbooster.application.errors import UnexpectedResponse

MONTHS = ("OCAK", "SUBAT", "MART", "NISAN", "MAYIS", "HAZIRAN", "TEMMUZ", "AGUSTOS", "EYLUL", "EKIM", "KASIM", "ARALIK")
MAX_UNZIPPED_BYTES = 200 * 1024 * 1024  # a 1 MB report unpacks to ~10 MB; anything near this is a zip bomb
MAX_ROWS_PER_SHEET = 20_000
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_TR_FOLD = str.maketrans("İIıiŞşĞğÜüÖöÇç", "IIIISSGGUUOOCC")
_TR_UPPER = str.maketrans({"i": "İ", "ı": "I"})
_DOC_ID = re.compile(r"^[A-Za-z0-9+/=]{4,64}$")
_ICON = re.compile(r"/(pdf|excel|word)\.png$", re.IGNORECASE)
_YEAR = re.compile(r"\b(20[0-9]{2})\b")
_CUMULATIVE = re.compile(r"\bKUMU|\bILK [0-9]+ AY|\bOCAK (?:" + "|".join(MONTHS[1:]) + r")\b")
_TR_NUMBER = re.compile(r"-?[0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]+)?|-?[0-9]+(?:,[0-9]+)?")
_PLAIN_NUMBER = re.compile(r"-?[0-9]+\.[0-9]+(?:[eE]-?[0-9]+)?")
# Legal forms as written in the reports ("A.Ş.", "TİC.A.Ş", "LTD. ŞTİ." in Word, "ANONİM ŞİRKETİ" in Excel).
_LEGAL_FORMS = [
    (re.compile(r"(?<!\S)A\.\s?Ş\.?(?=\s|$)"), "ANONİM ŞİRKETİ"),
    (re.compile(r"(?<!\S)LTD\.?\s?ŞTİ\.?(?=\s|$)"), "LİMİTED ŞİRKETİ"),
]
_LEGAL_SUFFIX = re.compile(r"\s+(?:ANONİM|LİMİTED) ŞİRKETİ$")
# Abbreviated second words of a title, e.g. "TECO PETROL. SANAYİ" (2022), "AYTEMİZ AKAR." and "LİKİTGAZ DAĞ." (LPG).
_WORD_ABBREVIATIONS = {
    "PETROL.": "PETROLCÜLÜK", "PETROLC.": "PETROLCÜLÜK", "AKAR.": "AKARYAKIT", "AKARY.": "AKARYAKIT", "DAĞ.": "DAĞITIM",
    "SAN.": "SANAYİ", "TİC.": "TİCARET", "PET.": "PETROL", "DEP.": "DEPOLAMA",
}  # fmt: skip
_TRAILING_ABBREVIATION = {"AKAR", "AKARY", "DAĞ", "SAN", "TİC", "PET", "DEP"}  # dot lost at the end of a cut title
HOLDER_WORDS = 2


def fold(text: object) -> str:
    """ASCII upper-case words separated by single spaces ("Lisans Türü" -> "LISANS TURU")."""
    ascii_ = str(text or "").replace("\u0307", "").translate(_TR_FOLD).upper()
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", ascii_).split())


def holder(raw: object) -> tuple[str, str]:
    """(entity_id, display name) of a licence holder: the first two words of its title without the legal form.

    The same company is written differently from month to month ("PETROL OFİSİ A.Ş.", "PETROL OFİSİ ANONİM ŞİRKETİ",
    "AYTEMİZ AKARYAKIT DAĞ. A.Ş." next to "AYTEMİZ AKARYAKIT DAĞITIM ANONİM ŞİRKETİ"); the leading words carry the
    brand and stay stable, the rest of the title does not. Holders sharing the first two words are summed."""
    text = " ".join(str(raw).replace("\u0307", "").replace("&", " & ").translate(_TR_UPPER).upper().split())  # "SHELL&TURCAS" (2016-04)
    text = re.sub(r"\([^)]*\)", " ", re.sub(r"\.(?=[^\s.,)])", ". ", text))  # "TİC.A.Ş" -> "TİC. A. Ş"; drop "(S OIL)"
    for pattern, replacement in _LEGAL_FORMS:
        text = pattern.sub(replacement, text)
    words: list[str] = []
    for word in _LEGAL_SUFFIX.sub("", " ".join(text.split())).split():
        if len([w for w in words if fold(w)]) == HOLDER_WORDS:
            break
        if words:  # only the second word: "PET GAZ" is a name, "ŞİRİNGAZ PET." an abbreviation
            word = _WORD_ABBREVIATIONS.get(word if word.endswith(".") or word not in _TRAILING_ABBREVIATION else f"{word}.", word)
        words.append(word)
    name = " ".join(words).strip(" .,;-")
    return fold(name).replace(" ", "_"), name


def is_cumulative(caption: str) -> bool:
    """Year-to-date tables ("Ocak-Ağustos 2026", "Kümülatif") must not be read as monthly figures."""
    return bool(_CUMULATIVE.search(fold(caption)))


def number(cell: object) -> float | None:
    """Cell value as float; None for empty cells. Word reports use Turkish formatting ("1.234.567,89")."""
    if cell is None:
        return None
    if isinstance(cell, bool):
        raise UnexpectedResponse("EPDK tablosunda sayı yerine mantıksal değer var")
    if isinstance(cell, int | float):
        return float(cell)
    text = str(cell).strip().replace("\xa0", "").replace(" ", "").rstrip("%")
    if text in {"", "-", "–", "—"}:
        return None
    if _TR_NUMBER.fullmatch(text):
        return float(text.replace(".", "").replace(",", "."))
    if _PLAIN_NUMBER.fullmatch(text):  # Excel stores some cells as text ("12.5")
        return float(text)
    raise UnexpectedResponse(f"EPDK tablosunda sayısal olmayan değer: {text[:20]!r}")


@dataclass(frozen=True)
class Document:
    kind: str  # pdf | excel | word (the icon next to the link)
    doc_id: str
    title: str


@dataclass
class _Entry:
    title: str
    docs: list[Document] = field(default_factory=list)


class _ListingParser(HTMLParser):
    """Collapsible entries (<a data-toggle="collapse">title</a>) followed by their download links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: list[_Entry] = []
        self._title: list[str] | None = None
        self._link: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "a" and a.get("data-toggle") == "collapse":
            self._title = []
        elif tag == "a" and a.get("href", "").startswith("/Detay/DownloadDocument?id="):
            self._link = {"id": a["href"].split("id=", 1)[1], "title": a.get("title", "")}
        elif tag == "img" and self._link is not None and (m := _ICON.search(a.get("src", ""))):
            self._link["kind"] = m[1].lower()

    def handle_data(self, data: str) -> None:
        if self._title is not None:
            self._title.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a":
            return
        if self._title is not None:
            self.entries.append(_Entry(" ".join("".join(self._title).split())))
            self._title = None
        elif self._link is not None:
            link, self._link = self._link, None
            if self.entries and _DOC_ID.fullmatch(link["id"]) and link.get("kind"):
                self.entries[-1].docs.append(Document(link["kind"], link["id"], " ".join(link["title"].split())))


def parse_listing(html: str) -> dict[tuple[int, int], list[Document]]:
    """{(year, month): documents} of a monthly report list; entries that name no single month are skipped."""
    parser = _ListingParser()
    parser.feed(html)
    out: dict[tuple[int, int], list[Document]] = {}
    for entry in parser.entries:
        words = fold(entry.title).split()
        months = {MONTHS.index(w) + 1 for w in words if w in MONTHS}
        years = set(_YEAR.findall(" ".join(words)))
        if len(months) == 1 and len(years) == 1 and entry.docs:
            out.setdefault((int(years.pop()), months.pop()), []).extend(entry.docs)
    return out


@dataclass(frozen=True)
class Grid:
    """A table of cell values: a Word table (caption = the paragraph before it) or a whole Excel sheet."""

    caption: str
    rows: list[list[object]]


def _archive(content: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise UnexpectedResponse("EPDK dosyası açılamadı (bozuk Office arşivi)") from None
    if sum(i.file_size for i in archive.infolist()) > MAX_UNZIPPED_BYTES:
        raise UnexpectedResponse("EPDK dosyası açıldığında boyut sınırını aşıyor")
    return archive


def office_kind(content: bytes) -> str | None:
    """'docx' or 'xlsx' for Office Open XML files; None for anything else (old binary .doc/.xls, PDF)."""
    if not content.startswith(b"PK\x03\x04"):
        return None
    names = set(_archive(content).namelist())
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    return None


def read_docx(content: bytes) -> list[Grid]:
    xml = _archive(content).read("word/document.xml")
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
        raise UnexpectedResponse("EPDK Word dosyası beklenmeyen bir DTD içeriyor")
    try:
        body = ElementTree.fromstring(xml).find(f"{_W}body")
    except ElementTree.ParseError:
        raise UnexpectedResponse("EPDK Word dosyası çözümlenemedi") from None
    grids: list[Grid] = []
    caption = ""
    for el in _blocks(body):
        if el.tag == f"{_W}p":
            if text := _text(el):
                caption = text
        elif el.tag == f"{_W}tbl":
            grids.append(Grid(caption, [_row(tr) for tr in el.findall(f"{_W}tr")]))
            caption = ""
    return grids


def _blocks(parent):
    """Top-level paragraphs and tables in document order (content controls are unwrapped)."""
    for el in list(parent) if parent is not None else []:
        if el.tag == f"{_W}sdt":
            yield from _blocks(el.find(f"{_W}sdtContent"))
        else:
            yield el


def _text(el) -> str:
    paragraphs = el.iter(f"{_W}p") if el.tag != f"{_W}p" else [el]
    return " ".join(" ".join("".join(t.text or "" for t in p.iter(f"{_W}t")) for p in paragraphs).split())


def _row(tr) -> list[object]:
    cells: list[object] = []
    for tc in tr.findall(f"{_W}tc"):
        span = tc.find(f"{_W}tcPr/{_W}gridSpan")
        width = int(span.get(f"{_W}val", "1")) if span is not None and span.get(f"{_W}val", "1").isdigit() else 1
        cells += [_text(tc)] * max(1, min(width, 50))
    return cells


def read_xlsx(content: bytes) -> list[Grid]:
    import openpyxl  # imported lazily: it is slow to load and only the Excel annexes (2026 onwards) need it

    _archive(content)  # size check before openpyxl decompresses anything
    try:
        book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:  # openpyxl raises many types for malformed files
        raise UnexpectedResponse("EPDK Excel dosyası çözümlenemedi") from None
    try:
        grids = []
        for sheet in book.worksheets:
            rows = []
            for i, row in enumerate(sheet.iter_rows(values_only=True)):
                if i >= MAX_ROWS_PER_SHEET:
                    raise UnexpectedResponse("EPDK Excel sayfası satır sınırını aşıyor")
                rows.append(list(row))
            grids.append(Grid("", rows))
        return grids
    finally:
        book.close()


def read_office(content: bytes) -> list[Grid] | None:
    kind = office_kind(content)
    if kind == "docx":
        return read_docx(content)
    if kind == "xlsx":
        return read_xlsx(content)
    return None


@dataclass
class MonthTable:
    """One month of sales by licence holder: {key: (display name, {column: tonnes})} plus the published totals."""

    entities: dict[str, tuple[str, dict[str, float]]] = field(default_factory=dict)
    totals: dict[str, float] = field(default_factory=dict)
    # published "Pay (%)" next to a column, per row: {column: [(tonnes, share), ...]}
    shares: dict[str, list[tuple[float, float]]] = field(default_factory=dict)

    def add(self, raw_name: object, values: dict[str, float | None]) -> None:
        key, name = holder(raw_name)
        if not key:
            return
        _, current = self.entities.setdefault(key, (name, {}))
        for column, value in values.items():  # two licences (dağıtıcı and ihrakiye) or titles of one brand: summed
            current[column] = current.get(column, 0.0) + (value or 0.0)


def _header(grid: Grid, test) -> int | None:
    for i, row in enumerate(grid.rows[:60]):
        if row and test([fold(c) for c in row]):
            return i
    return None


def _caption(grid: Grid, header: int) -> str:
    above = [str(c) for row in grid.rows[max(0, header - 3) : header] for c in row if c not in (None, "")]
    return " ".join([grid.caption, *above])


def _columns(header: list[str], keys: dict[str, str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, cell in enumerate(header):
        for prefix, key in keys.items():
            if cell.startswith(prefix) and key not in out:
                out[key] = i
                break
    return out


PETROL_COLUMNS = {
    "GENEL TOPLAM": "TOPLAM", "TOPLAM": "TOPLAM", "BENZIN": "BENZIN", "MOTORIN": "MOTORIN", "FUEL OIL": "FUEL_OIL",
    "HAVACILIK": "HAVACILIK", "DENIZCILIK": "DENIZCILIK", "GAZYAGI": "GAZYAGI", "DIGER": "DIGER", "PAY": "PAY",
}  # fmt: skip
LPG_COLUMNS = {"TUPLU": "TUPLU", "DOKME": "DOKME", "OTOGAZ": "OTOGAZ", "TOPLAM": "TOPLAM"}


def petrol_sales(grids: list[Grid]) -> MonthTable | None:
    """'Lisans Sahibi Şirketlere Göre Yurtiçi Satış(lar)' of the month: Word table 3.2 / Excel annex table 12."""

    def is_header(cells: list[str]) -> bool:  # "Lisans Sahibinin Ünvanı | Lisans Türü" or "Şirket Ünvanı | Lisans Tipi"
        return "UNVAN" in cells[0] and bool({"LISANS TURU", "LISANS TIPI"} & set(cells)) and any(c.startswith("BENZIN") for c in cells)

    for grid in grids:
        h = _header(grid, is_header)
        if h is None:
            continue
        caption = fold(_caption(grid, h))
        if "SIRKETLERE GORE YURT" not in caption or is_cumulative(caption):
            continue
        columns = _columns([fold(c) for c in grid.rows[h]], PETROL_COLUMNS)
        if not {"BENZIN", "MOTORIN", "TOPLAM"} <= set(columns):
            raise UnexpectedResponse("EPDK petrol satış tablosunun kolonları değişmiş")
        table, holder, repeated = MonthTable(), None, fold(grid.rows[h][0])
        for row in grid.rows[h + 1 :]:
            label = fold(row[0] if row else "")
            if label == repeated:  # header repeated after a page break
                continue
            values = {k: number(row[i]) if i < len(row) else None for k, i in columns.items()}
            if label in {"TOPLAM", "GENEL TOPLAM"}:
                table.totals = {k: v or 0.0 for k, v in values.items() if k != "PAY"}
                return table
            share = values.pop("PAY", None)
            if all(v is None for v in values.values()):
                continue
            if label:
                holder = row[0]
            elif holder is None:  # a nameless row continues the holder above (second licence, merged name cell)
                raise UnexpectedResponse("EPDK petrol satış tablosunda adı olmayan bir satır var")
            if share is not None:
                table.shares.setdefault("TOPLAM", []).append((values.get("TOPLAM") or 0.0, share))
            table.add(holder, values)
        raise UnexpectedResponse("EPDK petrol satış tablosunda toplam satırı bulunamadı")
    return None


def lpg_sales(grids: list[Grid], year: int, month: int) -> MonthTable | None:
    """LPG sales by distributor and product of the month. Word reports: table 3.5 (wide, Satış/Pay per product);
    Excel annexes: table 12 (one row per distributor and product, previous year's month next to this one)."""
    for grid in grids:
        table = _lpg_wide(grid)
        if table is None:
            table = _lpg_long(grid, f"{MONTHS[month - 1]} {year}")
        if table is not None:
            return table
    return None


def _lpg_wide(grid: Grid) -> MonthTable | None:
    def is_header(cells: list[str]) -> bool:  # first cell "Lisans Sahibinin Unvanı" (2016: "DAĞITICI")
        holder_column = cells[0].startswith("LISANS SAHIBI") or cells[0] == "DAGITICI"
        return holder_column and all(any(c.startswith(p) for c in cells) for p in ("TUPLU", "DOKME", "OTOGAZ"))

    h = _header(grid, is_header)
    if h is None or h + 1 >= len(grid.rows):
        return None
    caption = fold(_caption(grid, h))
    if "DAGITICILARA VE URUN" not in caption or is_cumulative(caption):  # "... ve ürün türüne" / "ve ürünlere göre"
        return None
    head, sub = [fold(c) for c in grid.rows[h]], [fold(c) for c in grid.rows[h + 1]]
    columns = {
        key: i for key in LPG_COLUMNS.values()
        for i in [next((j for j, c in enumerate(head) if c.startswith(key) and j < len(sub) and sub[j].startswith("SATIS")), None)]
        if i is not None
    }  # fmt: skip
    if set(columns) != set(LPG_COLUMNS.values()):
        raise UnexpectedResponse("EPDK LPG satış tablosunun kolonları değişmiş")
    pays = {key: i + 1 for key, i in columns.items() if i + 1 < len(sub) and sub[i + 1].startswith("PAY")}
    table = MonthTable()
    for row in grid.rows[h + 2 :]:
        label = fold(row[0] if row else "")
        if not label or label.startswith("LISANS SAHIBI"):
            continue
        values = {k: number(row[i]) if i < len(row) else None for k, i in columns.items()}
        if label in {"TOPLAM", "GENEL TOPLAM"}:
            table.totals = {k: v or 0.0 for k, v in values.items()}
            return table
        if any(v is not None for v in values.values()):
            for key, i in pays.items():
                if i < len(row) and (share := number(row[i])) is not None:
                    table.shares.setdefault(key, []).append((values[key] or 0.0, share))
            table.add(row[0], values)
    raise UnexpectedResponse("EPDK LPG satış tablosunda toplam satırı bulunamadı")


_LPG_PRODUCTS = {"TUPLU": "TUPLU", "DOKME": "DOKME", "OTOGAZ": "OTOGAZ", "FIRMA TOPLAMI": "TOPLAM"}


def _lpg_long(grid: Grid, period: str) -> MonthTable | None:
    h = _header(grid, lambda cells: cells[0].startswith("LISANS SAHIBI") and period in cells)
    if h is None or h + 1 >= len(grid.rows):
        return None
    caption = fold(_caption(grid, h))
    if "LISANS SAHIPLERINE GORE LPG SATIS" not in caption or is_cumulative(caption):
        return None
    head, sub = [fold(c) for c in grid.rows[h]], [fold(c) for c in grid.rows[h + 1]]
    if "URUN TURU" not in sub:
        raise UnexpectedResponse("EPDK LPG satış tablosunun kolonları değişmiş")
    qty, product = head.index(period), sub.index("URUN TURU")
    table, name, parts = MonthTable(), None, {}
    for row in grid.rows[h + 2 :]:
        if not row:
            continue
        label = fold(row[0])
        if label == "TOPLAM":
            if name is not None:
                raise UnexpectedResponse("EPDK LPG satış tablosunda firma toplamı eksik")
            total = number(row[qty]) or 0.0
            products = {k: sum(v[1].get(k, 0.0) for v in table.entities.values()) for k in ("TUPLU", "DOKME", "OTOGAZ")}
            # The table gives only the grand total; product totals are the sum of the distributors (the report's
            # product tables 8-10 hold the same sums).
            table.totals = {**products, "TOPLAM": total}
            return table
        if label:
            name = row[0]
        kind = _LPG_PRODUCTS.get(fold(row[product]) if product < len(row) else "")
        if kind is None or name is None:
            continue
        parts[kind] = number(row[qty]) if qty < len(row) else None
        if kind == "TOPLAM":
            if abs(sum(v or 0.0 for k, v in parts.items() if k != "TOPLAM") - (parts["TOPLAM"] or 0.0)) > 0.01 + 1e-6 * (parts["TOPLAM"] or 0.0):
                raise UnexpectedResponse(f"EPDK LPG tablosunda ürünlerin toplamı firma toplamını tutmuyor: {holder(name)[1]}")
            table.add(name, parts)
            name, parts = None, {}
    raise UnexpectedResponse("EPDK LPG satış tablosunda toplam satırı bulunamadı")

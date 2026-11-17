"""Dars jadvalini Excel (.xlsx), Word (.docx) yoki CSV fayldan o'qish.

Ikki ko'rinish tanib olinadi:
  1) To'r: bir qatorda (yoki ustunda) hafta kunlari, qarama-qarshi tomonda dars vaqti, kesishmada fan.
  2) Ro'yxat: har qatorda kun, vaqt va fan (kun bir marta yozilib, keyingi qatorlar bo'sh bo'lishi mumkin).
Natija: [(hafta_kuni 0-6, "HH:MM", fan), ...]
"""
import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET

MAX_UNZIPPED = 50 * 1024 * 1024  # zip-bombaga qarshi: ochilgandagi umumiy hajm
MAX_ROWS = 300

# Hafta kunlari aniq so'z bilan (so'z boshi emas: "Yakuniy nazorat" yakshanba emas):
# o'zbek lotin/kirill, rus, ingliz va qisqartmalar
_DAYS = [
    {"dushanba", "душанба", "понедельник", "monday", "du", "dush", "пн", "mo", "mon"},
    {"seshanba", "сешанба", "вторник", "tuesday", "se", "sesh", "вт", "tu", "tue"},
    {"chorshanba", "чоршанба", "среда", "wednesday", "ch", "chor", "ср", "we", "wed"},
    {"payshanba", "пайшанба", "четверг", "thursday", "pa", "pay", "чт", "th", "thu"},
    {"juma", "жума", "пятница", "friday", "ju", "jum", "пт", "fr", "fri"},
    {"shanba", "шанба", "суббота", "saturday", "sh", "shan", "сб", "sa", "sat"},
    {"yakshanba", "якшанба", "воскресенье", "sunday", "ya", "yak", "вс", "su", "sun"},
]
_TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)")


def _clean(v):
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()[:100]


def weekday_of(v):
    s = re.sub(r"[^\w]", "", _clean(v).lower())
    return next((i for i, names in enumerate(_DAYS) if s in names), None)


def time_of(v):
    m = _TIME_RE.search(_clean(v))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def _is_subject(v):
    s = _clean(v)
    return bool(s) and weekday_of(s) is None and not time_of(s) and not re.fullmatch(r"[\d\s.\-–—№#:]+", s)


def _parse_grid(grid):
    """To'r ko'rinishi (kunlar sarlavhada); topilmasa ro'yxat ko'rinishi."""
    width = max((len(r) for r in grid), default=0)
    grid = [list(r) + [""] * (width - len(r)) for r in grid]
    for t in (grid, [list(c) for c in zip(*grid)]):  # oddiy va ag'darilgan (kunlar ustunda)
        for hi, head in enumerate(t):
            days = {ci: d for ci, v in enumerate(head) if (d := weekday_of(v)) is not None}
            if len(days) < 2:
                continue
            rows = []
            for r in t[hi + 1:]:
                tm = next((x for ci, v in enumerate(r) if ci not in days and (x := time_of(v))), None)
                if tm:
                    rows += [(d, tm, _clean(r[ci])) for ci, d in days.items() if _is_subject(r[ci])]
            if rows:
                return rows
    rows, day = [], None
    for r in grid:
        d = next((x for v in r if (x := weekday_of(v)) is not None), None)
        day = d if d is not None else day  # birlashtirilgan katak: kun bir marta yozilgan
        tm = next((x for v in r if (x := time_of(v))), None)
        subj = next((_clean(v) for v in r if _is_subject(v)), None)
        if day is not None and tm and subj:
            rows.append((day, tm, subj))
    return rows


def _check_zip(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("Fayl buzilgan yoki formati noto'g'ri.")
    if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
        raise ValueError("Fayl juda katta.")
    return z


def _xlsx_grids(data):
    import openpyxl

    _check_zip(data)
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            yield [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _docx_grids(data):
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with _check_zip(data) as z:
        try:
            root = ET.fromstring(z.read("word/document.xml"))
        except KeyError:
            raise ValueError("Word fayli ichida hujjat topilmadi.")
    for tbl in root.iter(w + "tbl"):
        grid = []
        for tr in tbl.findall(w + "tr"):
            row = []
            for tc in tr.findall(w + "tc"):
                text = " ".join("".join(t.text or "" for t in p.iter(w + "t")) for p in tc.iter(w + "p"))
                span = tc.find(f"{w}tcPr/{w}gridSpan")
                # birlashtirilgan katak ustunlarni siljitmasin
                row += [text] * (int(span.get(w + "val", 1)) if span is not None else 1)
            grid.append(row)
        yield grid


def _csv_grids(data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1251", errors="replace")  # Excel'ning rus/o'zbek kirill CSV'si
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    yield list(csv.reader(io.StringIO(text), dialect))


def parse_schedule_file(filename, data):
    """Fayldan darslar ro'yxati. Tushunarsiz fayl uchun ValueError (foydalanuvchiga ko'rsatiladigan matn)."""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    readers = {"xlsx": _xlsx_grids, "xlsm": _xlsx_grids, "docx": _docx_grids, "csv": _csv_grids, "txt": _csv_grids}
    if ext in ("xls", "doc"):
        raise ValueError(f"Eski .{ext} formati o'qilmaydi. Faylni Excel/Word'da «Saqlash» → .{ext}x qilib saqlang.")
    if ext not in readers:
        raise ValueError("Faqat Excel (.xlsx), Word (.docx) yoki CSV fayl yuboring.")
    rows, seen = [], set()
    for grid in readers[ext](data):
        for row in _parse_grid([[_clean(v) for v in r] for r in grid]):
            if row not in seen:
                seen.add(row)
                rows.append(row)
    if not rows:
        raise ValueError(
            "Faylda dars jadvali topilmadi. Jadvalda hafta kunlari (Dushanba...), "
            "dars vaqti (masalan 08:30) va fan nomi bo'lishi kerak."
        )
    if len(rows) > MAX_ROWS:
        raise ValueError(f"Juda ko'p dars ({len(rows)}). Ko'pi bilan {MAX_ROWS} ta.")
    return sorted(rows)

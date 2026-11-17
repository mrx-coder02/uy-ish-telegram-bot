"""Jadval fayllarini o'qish testi (bazasiz). Ishga tushirish: py test_schedule_import.py"""
import datetime
import io
import zipfile

import openpyxl

from schedule_import import parse_schedule_file


def xlsx(rows):
    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def docx(table_xml):
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    doc = f'<w:document {w}><w:body><w:p><w:r><w:t>8-A sinf</w:t></w:r></w:p>{table_xml}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def cell(text, span=1):
    pr = f'<w:tcPr><w:gridSpan w:val="{span}"/></w:tcPr>' if span > 1 else ""
    return f"<w:tc>{pr}<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc>"


def row(*cells):
    return "<w:tr>" + "".join(cells) + "</w:tr>"


def expect_error(name, data, words):
    try:
        parse_schedule_file(name, data)
    except ValueError as e:
        assert words in str(e), (name, str(e))
    else:
        raise AssertionError(f"{name}: xato kutilgan edi")


# 1) Excel, to'r: kunlar ustunlarda, № va vaqt oralig'i, Excel vaqt katagi, tanaffus qatori
got = parse_schedule_file("jadval.xlsx", xlsx([
    ["8-A sinf dars jadvali"],
    ["№", "Vaqt", "Dushanba", "Seshanba", "Chorshanba"],
    [1, "08:00-08:45", "Matematika", "Fizika", None],
    [2, datetime.time(8, 50), "Ona tili", "", "Yakuniy nazorat"],
    ["", "Tanaffus", "", "", ""],
]))
assert got == [(0, "08:00", "Matematika"), (0, "08:50", "Ona tili"), (1, "08:00", "Fizika"),
               (2, "08:50", "Yakuniy nazorat")], got

# 2) Excel, ro'yxat: kun bir marta yozilgan (birlashtirilgan katak), "8.50" vaqt, o'qituvchi ustuni
got = parse_schedule_file("royxat.XLSX", xlsx([
    ["Kun", "Vaqt", "Fan", "O'qituvchi"],
    ["Dushanba", "8:00", "Algebra", "Karimov A."],
    [None, "8.50", "Geometriya", "Karimov A."],
    ["Seshanba", "09:00", "Kimyo", None],
]))
assert got == [(0, "08:00", "Algebra"), (0, "08:50", "Geometriya"), (1, "09:00", "Kimyo")], got

# 3) Ag'darilgan to'r, ruscha qisqartmalar: kunlar qatorlarda, vaqt sarlavhada
got = parse_schedule_file("t.xlsx", xlsx([
    ["", "08:00", "09:00"],
    ["Пн", "Математика", "Физика"],
    ["Вт", "Химия", None],
]))
assert got == [(0, "08:00", "Математика"), (0, "09:00", "Физика"), (1, "08:00", "Химия")], got

# 4) Word: birlashtirilgan sarlavha katagi (gridSpan) ustunlarni siljitmaydi
tbl = "<w:tbl>" + row(cell("Dars", 2), cell("Dushanba"), cell("Juma")) \
    + row(cell("1"), cell("8:30"), cell("Ingliz tili"), cell("Musiqa")) \
    + row(cell("Katta tanaffus", 4)) + "</w:tbl>"
got = parse_schedule_file("jadval.docx", docx(tbl))
assert got == [(0, "08:30", "Ingliz tili"), (4, "08:30", "Musiqa")], got

# 5) CSV: nuqta-vergul, Windows kirill kodirovkasi (Excel shunday saqlaydi)
got = parse_schedule_file("j.csv", "День;Время;Предмет\nПонедельник;08:00;Русский язык\nСреда;10:15;Биология\n"
                          .encode("cp1251"))
assert got == [(0, "08:00", "Русский язык"), (2, "10:15", "Биология")], got

# 6) Tushunarsiz fayllar foydalanuvchiga tushunarli xato bilan rad etiladi
expect_error("eski.xls", b"x", "Eski .xls")
expect_error("rasm.pdf", b"x", "Faqat Excel")
expect_error("buzuq.xlsx", b"not a zip", "buzilgan")
expect_error("bosh.xlsx", xlsx([["Salom", "dunyo"]]), "topilmadi")
expect_error("bosh.csv", b"", "topilmadi")

print("OK: jadval fayllari to'g'ri o'qiladi")

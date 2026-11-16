"""Vercel kirish nuqtasini (api/index.py) haqiqiy Postgres bilan, soxta Telegram API bilan sinash.
Har bir webhook alohida chaqiruv: suhbat holati faqat baza orqali saqlanishi kerak.

Ishga tushirish (Docker, bo'sh test bazasi bilan — haqiqiy bazada EMAS):
  docker network create uytest
  docker run -d --name uytest-pg --network uytest -e POSTGRES_PASSWORD=pw postgres:15-alpine
  docker run --rm --network uytest -v "%cd%:/app:ro" python:3.12-slim sh -c     "pip install -q -r /app/requirements.txt && cd /tmp && DATABASE_URL=postgresql://postgres:pw@uytest-pg/postgres python /app/test_flow.py"
"""
import datetime
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse

assert "supabase" not in os.environ.get("DATABASE_URL", ""), "Test haqiqiy bazaga yozadi — faqat bo'sh test bazasida ishlating"
os.environ["BOT_TOKEN"] = "123:TEST"
os.environ["CRON_SECRET"] = "cronsecret"
os.environ["SUPABASE_URL"] = ""  # loyihadagi .env haqiqiy Supabase'ga yuklamasin
os.environ["VERCEL"] = "1"
os.environ["VERCEL_PROJECT_PRODUCTION_URL"] = "uy-ish.vercel.app"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from telegram.request import HTTPXRequest  # noqa: E402

CALLS = []
_mid = [0]


async def fake_do_request(self, url, method, request_data=None, **kw):
    ep = url.rsplit("/", 1)[-1]
    p = dict(request_data.parameters) if request_data else {}
    CALLS.append((ep, p))
    _mid[0] += 1
    if ep == "getChat":
        if int(p["chat_id"]) == 444:  # botga /start bosmagan odam
            return 400, json.dumps({"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}).encode()
        chat = {"id": int(p["chat_id"]), "type": "private", "first_name": "Yangi", "last_name": "Ustoz",
                "accent_color_id": 0, "max_reaction_count": 0,
                "accepted_gift_types": {"unlimited_gifts": False, "limited_gifts": False, "unique_gifts": False,
                                        "premium_subscription": False, "gifts_from_channels": False}}
        return 200, json.dumps({"ok": True, "result": chat}).encode()
    msg = {"message_id": _mid[0], "date": 0, "chat": {"id": int(p.get("chat_id", 1) or 1), "type": "private"},
           "text": p.get("text", "")}
    result = {
        "getMe": {"id": 999, "is_bot": True, "first_name": "Bot", "username": "test_bot"},
        "getWebhookInfo": {"url": "https://testserver/api/webhook", "has_custom_certificate": False,
                           "pending_update_count": 0},
    }.get(ep, msg if ep.startswith(("send", "edit")) else True)
    return 200, json.dumps({"ok": True, "result": result}).encode()


HTTPXRequest.do_request = fake_do_request

from starlette.testclient import TestClient  # noqa: E402

import api.index as entry  # noqa: E402
import bot as core  # noqa: E402

DEV, STU = 111, 222
_uid = [0]


def upd(user_id, text=None, contact=None, cb=None):
    _uid[0] += 1
    frm = {"id": user_id, "is_bot": False, "first_name": f"U{user_id}"}
    chat = {"id": user_id, "type": "private"}
    if cb:
        return {"update_id": _uid[0], "callback_query": {"id": str(_uid[0]), "from": frm, "chat_instance": "x",
                "data": cb, "message": {"message_id": 1, "date": 0, "chat": chat, "text": "hw"}}}
    m = {"message_id": _uid[0], "date": 0, "chat": chat, "from": frm}
    if contact:
        m["contact"] = contact
    else:
        m["text"] = text
        if text.startswith("/"):
            m["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    return {"update_id": _uid[0], "message": m}


def init_data(user_id):
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": user_id, "first_name": "X"})}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", b"123:TEST", hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


def q(sql, params=()):
    with core.db() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def last_text_to(chat_id):
    return [p.get("text") or p.get("caption") for ep, p in CALLS if str(p.get("chat_id")) == str(chat_id)][-1]


with TestClient(entry.app) as client:
    H = {"X-Telegram-Bot-Api-Secret-Token": core.WEBHOOK_SECRET}

    def send(u):
        r = client.post("/api/webhook", json=u, headers=H)
        assert r.status_code == 200, r.text

    # webhook himoyasi
    assert client.post("/api/webhook", json=upd(DEV, "/start")).status_code == 403

    # 1) developer bo'lish
    send(upd(DEV, "/dev"))
    assert q("SELECT role FROM users WHERE tg_id=?", (DEV,)) == [{"role": "developer"}]
    assert "DEVELOPER" in last_text_to(DEV)

    # 2) ko'p bosqichli suhbat: o'quvchi qo'shish (har xabar — yangi Application)
    send(upd(DEV, "/oquvchi"))
    assert q("SELECT count(*) n FROM ptb_state WHERE kind='conv:stu'")[0]["n"] == 1, "suhbat holati bazada saqlanmadi"
    send(upd(DEV, "+998901234567"))
    send(upd(DEV, "Ali"))
    send(upd(DEV, "Valiyev"))
    stu = q("SELECT * FROM users WHERE phone=?", ("+998901234567",))
    assert stu and stu[0]["first_name"] == "Ali" and stu[0]["last_name"] == "Valiyev", (stu, last_text_to(DEV))
    assert q("SELECT count(*) n FROM ptb_state")[0]["n"] == 0, q("SELECT * FROM ptb_state")

    # vaqt zonasi Toshkent (UTC+5)
    assert datetime.datetime.now().astimezone().utcoffset() == datetime.timedelta(hours=5)

    # 3) boshqa odamning kontakti bilan kirib bo'lmaydi
    send(upd(STU, contact={"phone_number": "998901234567", "first_name": "Ali", "user_id": 987654}))
    assert q("SELECT tg_id FROM users WHERE phone=?", ("+998901234567",))[0]["tg_id"] is None, "begona kontakt qabul qilindi"
    # o'quvchi o'z raqami bilan kiradi
    send(upd(STU, contact={"phone_number": "998901234567", "first_name": "Ali", "user_id": STU}))
    assert q("SELECT tg_id FROM users WHERE phone=?", ("+998901234567",))[0]["tg_id"] == STU, last_text_to(STU)

    # 4a) "❌ Bekor" fan nomi bo'lib saqlanmaydi; eslatmada ham
    for t in ("/uyvazifa", "❌ Bekor", "/eslatma", "❌ Bekor"):
        send(upd(DEV, t))
    assert not q("SELECT 1 FROM homework") and q("SELECT count(*) n FROM ptb_state")[0]["n"] == 0
    assert not [p for ep, p in CALLS if str(p.get("chat_id")) == str(STU) and "ESLATMA" in (p.get("text") or "")]

    # 4) uy vazifa qo'shish (suhbat) -> o'quvchiga yuboriladi; sana nolsiz yozilsa ham dd.mm.yyyy saqlanadi
    d = datetime.date.today() + datetime.timedelta(days=3)
    for t in ("/uyvazifa", "Matematika", "1-10 misollar"):
        send(upd(DEV, t))
    last = upd(DEV, f"{d.day}.{d.month}.{d.year}")
    send(last)
    sent_before = len(CALLS)
    send(last)  # Telegram bir xil update'ni qayta yubordi -> e'tiborsiz
    assert len(CALLS) == sent_before, "takroriy update qayta bajarildi"
    assert "kimga" in last_text_to(DEV) and not q("SELECT 1 FROM homework")
    send(upd(DEV, "👥 Hammaga"))
    hw = q("SELECT * FROM homework")
    assert len(hw) == 1 and hw[0]["subject"] == "Matematika", (hw, last_text_to(DEV))
    assert hw[0]["due_date"] == d.strftime("%d.%m.%Y"), hw[0]["due_date"]
    hw_id = hw[0]["id"]
    assert "Matematika" in last_text_to(STU), "vazifa o'quvchiga yuborilmadi"
    assert hw[0]["for_all"] == 1

    # 4c) bot: faqat tanlangan o'quvchilarga
    stu_uid = q("SELECT id FROM users WHERE tg_id=?", (STU,))[0]["id"]
    for t in ("/uyvazifa", "Fizika", "Masala 5", f"{d.day}.{d.month}.{d.year}", "✅ O'quvchilarni tanlash"):
        send(upd(DEV, t))
    send(upd(DEV, cb="hwsel:ok"))  # hech kim tanlanmagan -> rad
    assert not q("SELECT 1 FROM homework WHERE subject='Fizika'")
    assert [p for ep, p in CALLS if ep == "answerCallbackQuery"][-1].get("show_alert")
    send(upd(DEV, cb=f"hwsel:{stu_uid}"))
    mk = [p for ep, p in CALLS if ep == "editMessageReplyMarkup"][-1]["reply_markup"]
    assert "✅" in json.dumps(mk, ensure_ascii=False), mk
    send(upd(DEV, cb="hwsel:ok"))
    fiz = q("SELECT * FROM homework WHERE subject='Fizika'")
    assert len(fiz) == 1 and fiz[0]["for_all"] == 0, fiz
    assert q("SELECT user_id FROM homework_status WHERE homework_id=?", (fiz[0]["id"],)) == [{"user_id": stu_uid}]
    assert "Fizika" in last_text_to(STU) and "1 ta o'quvchiga" in last_text_to(DEV)
    assert q("SELECT count(*) n FROM ptb_state")[0]["n"] == 0
    send(upd(DEV, cb="hwsel:ok"))  # suhbat tugagach eski tugma
    assert "eskirgan" in [p for ep, p in CALLS if ep == "answerCallbackQuery"][-1]["text"]
    # bot: o'chirish (developer), qayta bosish
    send(upd(STU, cb=f"delhw:{fiz[0]['id']}"))  # o'quvchi o'chira olmaydi
    assert q("SELECT 1 FROM homework WHERE id=?", (fiz[0]["id"],))
    send(upd(DEV, cb=f"delhw:{fiz[0]['id']}"))
    assert not q("SELECT 1 FROM homework WHERE id=?", (fiz[0]["id"],))
    assert not q("SELECT 1 FROM homework_status WHERE homework_id=?", (fiz[0]["id"],))
    send(upd(DEV, cb=f"delhw:{fiz[0]['id']}"))
    assert "allaqachon" in [p for ep, p in CALLS if ep == "answerCallbackQuery"][-1]["text"]

    # 4b) 6 soatdan eski suhbat bosqichi unutiladi
    send(upd(DEV, "/oquvchi"))
    with core.db() as c:
        c.execute("UPDATE ptb_state SET value=? WHERE kind='conv:stu'", (json.dumps({"v": "stu1", "t": 0}),))
    send(upd(DEV, "+998901111111"))
    assert not q("SELECT 1 FROM users WHERE phone='+998901111111'") and not q("SELECT 1 FROM ptb_state WHERE kind='conv:stu'")
    send(upd(DEV, "Ism"))  # suhbat davom etmaydi
    assert not q("SELECT 1 FROM users WHERE first_name='Ism'")

    # 5) tugma: bajarildi
    send(upd(STU, cb=f"done:{hw_id}"))
    assert q("SELECT done FROM homework_status WHERE homework_id=?", (hw_id,)) == [{"done": 1}]

    # 5b) Rol berish: ro'yxatdan tugma bilan
    stu_id = q("SELECT id FROM users WHERE tg_id=?", (STU,))[0]["id"]
    send(upd(DEV, "/rol"))
    kb = [p for ep, p in CALLS if ep == "sendMessage" and str(p.get("chat_id")) == str(DEV)][-1]["reply_markup"]
    assert f"rolepick:{stu_id}" in json.dumps(kb), kb
    send(upd(DEV, cb=f"rolepick:{stu_id}"))
    send(upd(DEV, "Starosta"))
    assert q("SELECT role FROM users WHERE id=?", (stu_id,)) == [{"role": "starosta"}], last_text_to(DEV)
    assert "Starosta" in last_text_to(STU)
    # Telegram ID yozib: ro'yxatda yo'q, lekin /start bosgan -> yangi hisob
    for t in ("/rol", "333", "O'qituvchi"):
        send(upd(DEV, t))
    new = q("SELECT first_name, role FROM users WHERE tg_id=?", (333,))
    assert new == [{"first_name": "Yangi", "role": "teacher"}], (new, [(ep, p.get("text")) for ep, p in CALLS[-8:]])
    # /start bosmagan ID -> rad, suhbat davom etadi
    send(upd(DEV, "/rol"))
    send(upd(DEV, "444"))
    assert "/start" in last_text_to(DEV) and not q("SELECT 1 FROM users WHERE tg_id=444")
    send(upd(DEV, "/cancel"))
    assert q("SELECT count(*) n FROM ptb_state")[0]["n"] == 0, q("SELECT * FROM ptb_state")
    with core.db() as c:
        c.execute("UPDATE users SET role='student' WHERE id=?", (stu_id,))

    # 6) Mini App API
    sh = {"X-Init-Data": init_data(STU)}
    dh = {"X-Init-Data": init_data(DEV)}
    assert client.get("/api/me").status_code == 401
    me = client.get("/api/me", headers=sh).json()
    assert me["user"]["role"] == "student", me
    assert client.get("/api/homework", headers=sh).json()["homework"][0]["done"] == 1
    assert client.get("/api/stats", headers=sh).status_code == 403
    assert client.get("/api/stats", headers=dh).json()["stats"]["users"] == 3
    # Rol API: Telegram ID bo'yicha
    assert client.post("/api/role", headers=sh, json={"tg_id": 555, "role": "teacher"}).status_code == 403
    r = client.post("/api/role", headers=dh, json={"tg_id": 555, "role": "teacher"})
    assert r.json() == {"ok": True, "name": "Yangi Ustoz"}, r.text
    assert client.post("/api/role", headers=dh, json={"tg_id": 444, "role": "student"}).json()["error"] == "not_started"
    r = client.post("/api/role", headers=dh, json={"tg_id": STU, "role": "starosta"})
    assert r.json()["ok"] and q("SELECT role FROM users WHERE tg_id=?", (STU,)) == [{"role": "starosta"}]
    th = {"X-Init-Data": init_data(555)}  # o'qituvchi developer qila olmaydi
    assert client.post("/api/role", headers=th, json={"tg_id": STU, "role": "developer"}).status_code == 403
    assert client.post("/api/role", headers=th, json={"tg_id": STU, "role": "student"}).json()["ok"]
    studs = client.get("/api/students", headers=dh).json()["students"]
    assert any(s_["tg_id"] == STU for s_ in studs), studs
    assert client.post("/api/schedule", headers=dh, json={"weekday": 0, "time": "8:30", "subject": "Fizika"}).json()["ok"]
    sch = client.get("/api/schedule", headers=sh).json()["schedule"]
    assert sch[0]["time"] == "08:30", sch
    assert client.delete(f"/api/schedule/{sch[0]['id']}", headers=dh).json()["ok"]
    r = client.post("/api/homework", headers=dh, json={"subject": "Ona tili", "task": "Insho",
                    "due_date": (datetime.date.today() + datetime.timedelta(days=1)).isoformat()})
    assert r.json()["ok"], r.text
    assert len(client.get("/api/homework", headers=dh).json()["homework"]) == 2
    # Mini App: tanlanganlarga; noto'g'ri ro'yxat rad etiladi
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    base = {"subject": "Kimyo", "task": "Tajriba", "due_date": tomorrow}
    for bad_ids in ([], [999999], "x", [True]):
        assert client.post("/api/homework", headers=dh, json={**base, "user_ids": bad_ids}).status_code == 400, bad_ids
    assert client.get("/api/students/brief", headers=sh).status_code == 403
    brief = client.get("/api/students/brief", headers=dh).json()["students"]
    assert brief == [{"id": stu_uid, "name": "Ali Valiyev"}], brief
    r = client.post("/api/homework", headers=dh, json={**base, "user_ids": [stu_uid, stu_uid]})
    kimyo = r.json()["id"]
    assert q("SELECT user_id FROM homework_status WHERE homework_id=?", (kimyo,)) == [{"user_id": stu_uid}]
    r = client.post("/api/students", headers=dh, json={"phone": "+998907777777", "first_name": "Vali",
                    "last_name": "Aliyev"})
    assert r.json()["ok"], r.text
    new_id = r.json()["id"]
    # yangi o'quvchi faqat "hammaga" vazifalarni oladi (Matematika, Ona tili), Kimyo'ni emas
    assert q("SELECT count(*) n FROM homework_status WHERE user_id=?", (new_id,))[0]["n"] == 2
    vali_hw = client.post("/api/homework", headers=dh, json={**base, "subject": "Biologiya", "user_ids": [new_id]}).json()["id"]
    # Mini App: o'chirish — muallif (starosta) o'zinikini, boshqasinikini emas
    with core.db() as c:
        c.execute("UPDATE users SET role='starosta' WHERE id=?", (stu_uid,))
    own = client.post("/api/homework", headers=sh, json={**base, "subject": "Starosta vazifasi"}).json()["id"]
    assert client.delete(f"/api/homework/{kimyo}", headers=sh).status_code == 403
    assert client.delete(f"/api/homework/{own}", headers=sh).json()["ok"]
    assert client.delete(f"/api/homework/{own}", headers=sh).status_code == 404
    with core.db() as c:
        c.execute("UPDATE users SET role='student' WHERE id=?", (stu_uid,))
    assert client.post("/api/submit", headers=sh, json={"homework_id": hw_id, "text": "Bajardim"}).json()["ok"]
    # XSS: zararli photo_url qabul qilinmaydi
    bad = 'x" onerror="alert(1)'
    assert client.post("/api/submit", headers=sh, json={"homework_id": hw_id, "photo_url": bad}).status_code == 400
    assert client.post("/api/homework", headers=dh, json={"subject": "A", "photo_url": bad,
                       "due_date": d.isoformat()}).status_code == 400
    # o'qituvchi developer/o'qituvchi hisobini yarata olmaydi, o'qituvchini o'chira olmaydi
    assert client.post("/api/students", headers=th, json={"phone": "+998905555555", "first_name": "Hack",
                       "role": "developer"}).status_code == 403
    teacher333 = q("SELECT id FROM users WHERE tg_id=333")[0]["id"]
    assert client.delete(f"/api/students/{teacher333}", headers=th).status_code == 403
    up = client.post("/api/upload", headers=sh, files={"file": ("a.png", b"\x89PNG fake", "image/png")}).json()
    assert up["url"].startswith("/uploads/"), up
    assert client.get(up["url"]).content == b"\x89PNG fake"

    # 7) cron
    assert client.get("/api/cron/morning").status_code == 401
    r = client.get("/api/cron/morning", headers={"Authorization": "Bearer cronsecret"})
    assert r.json()["ran"] == ["job_morning", "job_homework"], r.text
    n0 = len(CALLS)
    r = client.get("/api/cron/evening", headers={"Authorization": "Bearer cronsecret"})
    assert r.json()["ran"] == ["job_lessons_reminder", "job_evening"], r.text
    eve = {str(p.get("chat_id")): p.get("text", "") for ep, p in CALLS[n0:] if "Ertaga topshiriladigan" in p.get("text", "")}
    # Biologiya faqat Vali'ga (Telegram'siz) berilgan: Ali ko'rmaydi, developer ko'radi
    assert "Kimyo" in eve[str(STU)] and "Biologiya" not in eve[str(STU)], eve
    assert "Biologiya" in eve[str(DEV)], eve
    r = client.get("/api/cron/evening", headers={"Authorization": "Bearer cronsecret"})
    assert "skipped" in r.json(), "cron bir kunda ikki marta ishladi"
    assert client.get("/api/cron/xyz", headers={"Authorization": "Bearer cronsecret"}).status_code == 404

    # 8) setup va Mini App sahifasi
    assert client.get("/api/setup").status_code == 400  # preview/boshqa domendan emas
    s = client.get("https://uy-ish.vercel.app/api/setup").json()
    assert s["ok"] and s["mini_app"] == "https://uy-ish.vercel.app/", s
    wh = [p for ep, p in CALLS if ep == "setWebhook"][-1]
    assert wh["url"] == "https://uy-ish.vercel.app/api/webhook" and wh["secret_token"] == core.WEBHOOK_SECRET, wh
    mb = [p for ep, p in CALLS if ep == "setChatMenuButton"][-1]
    assert "chat_id" not in mb and "https://uy-ish.vercel.app/" in json.dumps(mb), mb  # hamma uchun
    assert "<html" in client.get("/").text.lower()

print(f"OK: hammasi o'tdi ({len(CALLS)} ta Telegram chaqiruvi)")

"""Mini App web-server: Telegram WebApp init data bilan autentifikatsiya,
REST API va statik fayllar. bot.py'dan post_init ichida ishga tushadi."""
import datetime
import hashlib
import hmac
import json
import os
import re
import urllib.parse

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

import bot as core

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


# ---------------- Telegram initData tekshiruvi ----------------
def validate_init_data(init_data: str):
    # Telegram hash'ni URL-dekodlangan qiymatlar ustidan hisoblaydi
    pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    hash_val = pairs.pop("hash", None)
    if not hash_val:
        return None
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", core.BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, hash_val):
        return None
    try:
        return json.loads(pairs.get("user", "{}"))
    except Exception:
        return None


def require_auth(perm=None):
    def deco(handler):
        async def wrapper(request):
            init = request.headers.get("X-Init-Data", "")
            tg_user = validate_init_data(init)
            if not tg_user or not tg_user.get("id"):
                return jr({"error": "auth"}, status=401)
            user = core.get_user_by_tg(tg_user["id"])
            if not user:
                return jr({"error": "not_registered"}, status=403)
            if perm and not core.has_perm(user, perm):
                return jr({"error": "forbidden"}, status=403)
            request.scope["user"] = user  # handler'lar request["user"] orqali o'qiydi
            return await handler(request)

        return wrapper

    return deco


def jr(data, status=200):
    return JSONResponse(data, status_code=status)


def json_ok(data=None):
    return jr(data or {"ok": True})


def parse_due(due):
    due = (due or "").strip()
    try:
        d = datetime.datetime.strptime(due, "%Y-%m-%d").date()
    except ValueError:
        return None
    return d.strftime("%d.%m.%Y")


# ---------------- API ----------------
@require_auth()
async def api_me(request):
    user = request["user"]
    perms = core.get_perms()
    return jr(
        {
            "user": {
                "id": user["id"],
                "name": core.display_name(user),
                "phone": user["phone"],
                "role": user["role"],
                "role_name": core.ROLE_NAMES[user["role"]],
                "tg_linked": bool(user["tg_id"]),
            },
            "perms": perms.get(user["role"], []) if user["role"] != "developer" else core.PERM_KEYS,
            "perm_labels": core.PERM_LABELS,
        }
    )


@require_auth()
async def api_homework(request):
    user = request["user"]
    with core.db() as c:
        if user["role"] in ("student", "starosta"):
            rows = c.execute(
                "SELECT h.*, s.accepted, s.done, sub.photo_url AS sub_photo, sub.text AS sub_text "
                "FROM homework h "
                "JOIN homework_status s ON s.homework_id=h.id AND s.user_id=? "
                "LEFT JOIN submission sub ON sub.homework_id=h.id AND sub.user_id=?",
                (user["id"], user["id"]),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT h.*, "
                "(SELECT COUNT(*) FROM homework_status s WHERE s.homework_id=h.id) total, "
                "(SELECT COUNT(*) FROM homework_status s WHERE s.homework_id=h.id AND s.done=1) done_n "
                "FROM homework h ORDER BY h.id DESC LIMIT 100"
            ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["left_text"] = core.days_left_text(d["due_date"])
        out.append(d)
    out.sort(key=lambda h: core.due_sort_key(h["due_date"]))
    return jr({"homework": out})


@require_auth(perm="add_homework")
async def api_homework_create(request):
    user = request["user"]
    try:
        body = await request.json()
    except Exception:
        return jr({"error": "bad_json"}, status=400)
    subject = (body.get("subject") or "").strip()
    task = (body.get("task") or "").strip() or None
    photo_url = (body.get("photo_url") or "").strip() or None
    due = parse_due(body.get("due_date"))
    if not subject or not due or (not task and not photo_url):
        return jr({"error": "missing_fields"}, status=400)
    with core.db() as c:
        cur = c.execute(
            "INSERT INTO homework(author_id, subject, task, photo_url, due_date, created_at) VALUES(?,?,?,?,?,?) RETURNING id",
            (user["id"], subject, task, photo_url, due, datetime.datetime.now().isoformat()),
        )
        hw_id = cur.fetchone()["id"]
        hw = dict(c.execute("SELECT * FROM homework WHERE id=?", (hw_id,)).fetchone())
    await core.broadcast_homework(hw_id)
    return jr({"ok": True, "id": hw_id})


@require_auth(perm="delete_content")
async def api_homework_delete(request):
    hw_id = int(request.path_params["hw_id"])
    with core.db() as c:
        c.execute("DELETE FROM homework WHERE id=?", (hw_id,))
        c.execute("DELETE FROM homework_status WHERE homework_id=?", (hw_id,))
        c.execute("DELETE FROM submission WHERE homework_id=?", (hw_id,))
    return json_ok()


@require_auth(perm="view_students")
async def api_hw_status(request):
    hw_id = int(request.path_params["hw_id"])
    with core.db() as c:
        hw = c.execute("SELECT * FROM homework WHERE id=?", (hw_id,)).fetchone()
        if not hw:
            return jr({"error": "not_found"}, status=404)
        statuses = c.execute(
            "SELECT s.user_id, s.accepted, s.done, u.first_name, u.last_name, "
            "sub.photo_url AS sub_photo, sub.text AS sub_text, sub.submitted_at "
            "FROM homework_status s JOIN users u ON u.id=s.user_id "
            "LEFT JOIN submission sub ON sub.homework_id=s.homework_id AND sub.user_id=s.user_id "
            "WHERE s.homework_id=? ORDER BY u.first_name",
            (hw_id,),
        ).fetchall()
    return jr(
        {"homework": dict(hw), "statuses": [dict(s) for s in statuses]}
    )


@require_auth()
async def api_submit(request):
    user = request["user"]
    try:
        body = await request.json()
    except Exception:
        return jr({"error": "bad_json"}, status=400)
    hw_id = int(body.get("homework_id") or 0)
    text = (body.get("text") or "").strip() or None
    photo_url = (body.get("photo_url") or "").strip() or None
    if not text and not photo_url:
        return jr({"error": "empty"}, status=400)
    with core.db() as c:
        assigned = c.execute(
            "SELECT 1 FROM homework_status WHERE homework_id=? AND user_id=?",
            (hw_id, user["id"]),
        ).fetchone()
        if not assigned:
            return jr({"error": "not_assigned"}, status=403)
        c.execute(
            "INSERT INTO submission(homework_id, user_id, text, photo_url, submitted_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(homework_id, user_id) DO UPDATE SET text=excluded.text, photo_url=excluded.photo_url, "
            "submitted_at=excluded.submitted_at",
            (hw_id, user["id"], text, photo_url, datetime.datetime.now().isoformat()),
        )
        c.execute(
            "UPDATE homework_status SET done=1, accepted=1 WHERE homework_id=? AND user_id=?",
            (hw_id, user["id"]),
        )
        hw = c.execute("SELECT author_id, subject FROM homework WHERE id=?", (hw_id,)).fetchone()
    if hw:
        author = core.get_user_by_id(hw["author_id"])
        if author and author.get("tg_id"):
            msg = (
                f"📤 {core.display_name(user)} vazifani topshirdi (Mini App).\n"
                f"📘 {hw['subject']}\n" + (f"📝 {text}" if text else "🖼 Rasm ilova qilingan")
            )
            try:
                if photo_url:
                    if photo_url.startswith("http"):
                        await core.bot_app.bot.send_photo(author["tg_id"], photo_url, caption=msg)
                    else:
                        local = os.path.join(core.UPLOADS_DIR, os.path.basename(photo_url))
                        if os.path.exists(local):
                            await core.bot_app.bot.send_photo(author["tg_id"], open(local, "rb"), caption=msg)
                        else:
                            await core.safe_send(core.bot_app.bot, author["tg_id"], msg)
                else:
                    await core.safe_send(core.bot_app.bot, author["tg_id"], msg)
            except Exception:
                await core.safe_send(core.bot_app.bot, author["tg_id"], msg)
    return json_ok()


@require_auth()
async def api_mark_done(request):
    user = request["user"]
    try:
        body = await request.json()
    except Exception:
        return jr({"error": "bad_json"}, status=400)
    hw_id = int(body.get("homework_id") or 0)
    with core.db() as c:
        cur = c.execute(
            "UPDATE homework_status SET accepted=1, done=1 WHERE homework_id=? AND user_id=?",
            (hw_id, user["id"]),
        )
        if cur.rowcount == 0:
            return jr({"error": "not_assigned"}, status=403)
        hw = c.execute("SELECT author_id, subject FROM homework WHERE id=?", (hw_id,)).fetchone()
    if hw:
        author = core.get_user_by_id(hw["author_id"])
        if author and author.get("tg_id"):
            await core.safe_send(
                core.bot_app.bot,
                author["tg_id"],
                f"✔️ {core.display_name(user)} vazifani bajarildi deb belgiladi (Mini App).\n📘 {hw['subject']}",
            )
    return json_ok()


@require_auth()
async def api_schedule(request):
    with core.db() as c:
        rows = c.execute("SELECT * FROM schedule ORDER BY weekday, time").fetchall()
    return jr({"schedule": [dict(r) for r in rows]})


@require_auth(perm="add_lesson")
async def api_schedule_add(request):
    try:
        body = await request.json()
    except Exception:
        return jr({"error": "bad_json"}, status=400)
    weekday = int(body.get("weekday", -1))
    time = (body.get("time") or "").strip()
    subject = (body.get("subject") or "").strip()
    if not (0 <= weekday <= 6) or not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", time) or not subject:
        return jr({"error": "bad_fields"}, status=400)
    time = f"{int(time.split(':')[0]):02d}:{time.split(':')[1]}"
    with core.db() as c:
        c.execute("INSERT INTO schedule(weekday, time, subject) VALUES(?,?,?)", (weekday, time, subject))
    return json_ok()


@require_auth(perm="delete_content")
async def api_schedule_delete(request):
    sch_id = int(request.path_params["sch_id"])
    with core.db() as c:
        c.execute("DELETE FROM schedule WHERE id=?", (sch_id,))
    return json_ok()


@require_auth(perm="view_students")
async def api_students(request):
    users = core.all_users()
    return jr(
        {
            "students": [
                {
                    "id": u["id"],
                    "name": core.display_name(u),
                    "phone": u["phone"],
                    "role": u["role"],
                    "role_name": core.ROLE_NAMES[u["role"]],
                    "tg_linked": bool(u["tg_id"]),
                }
                for u in users
            ]
        }
    )


@require_auth(perm="add_student")
async def api_students_add(request):
    try:
        body = await request.json()
    except Exception:
        return jr({"error": "bad_json"}, status=400)
    phone = core.normalize_phone(body.get("phone"))
    first = (body.get("first_name") or "").strip()
    last = (body.get("last_name") or "").strip()
    role = body.get("role") or "student"
    if not phone or len(phone) < 10 or not (2 <= len(first) <= 30) or role not in core.ROLE_NAMES:
        return jr({"error": "bad_fields"}, status=400)
    if core.get_user_by_phone(phone):
        return jr({"error": "phone_exists"}, status=409)
    with core.db() as c:
        cur = c.execute(
            "INSERT INTO users(tg_id, phone, first_name, last_name, role, created_at) VALUES(?,?,?,?,?,?) RETURNING id",
            (None, phone, first, last, role, datetime.datetime.now().isoformat()),
        )
        user_id = cur.fetchone()["id"]
        today_iso = datetime.date.today().strftime("%Y%m%d")
        c.execute(
            "INSERT INTO homework_status(homework_id, user_id) "
            "SELECT id, ? FROM homework "
            "WHERE substr(due_date,7,4)||substr(due_date,4,2)||substr(due_date,1,2) >= ? ON CONFLICT DO NOTHING",
            (user_id, today_iso),
        )
    return jr({"ok": True, "id": user_id})


@require_auth(perm="add_student")
async def api_students_delete(request):
    user_id = int(request.path_params["uid"])
    target = core.get_user_by_id(user_id)
    if not target:
        return jr({"error": "not_found"}, status=404)
    if target["role"] == "developer":
        return jr({"error": "forbidden"}, status=403)
    with core.db() as c:
        c.execute("DELETE FROM users WHERE id=?", (user_id,))
        c.execute("DELETE FROM homework_status WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM submission WHERE user_id=?", (user_id,))
    return json_ok()


@require_auth(perm="set_roles")
async def api_role_set(request):
    giver = request["user"]
    try:
        body = await request.json()
    except Exception:
        return jr({"error": "bad_json"}, status=400)
    user_id = int(body.get("user_id") or 0)
    role = body.get("role")
    if role not in core.ROLE_NAMES:
        return jr({"error": "bad_role"}, status=400)
    target = core.get_user_by_id(user_id)
    if not target:
        return jr({"error": "not_found"}, status=404)
    if target["role"] == "developer" and giver["role"] != "developer":
        return jr({"error": "forbidden"}, status=403)
    if giver["role"] != "developer" and role not in ("student", "starosta"):
        return jr({"error": "forbidden"}, status=403)
    with core.db() as c:
        c.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
    if target.get("tg_id"):
        await core.safe_send(
            core.bot_app.bot,
            target["tg_id"],
            f"🔔 Sizning rolingiz o'zgartirildi: {core.ROLE_NAMES[role]}",
        )
    return json_ok()


@require_auth(perm="manage_permissions")
async def api_permissions(request):
    if request.method == "GET":
        return jr(
            {"perms": core.get_perms(), "labels": core.PERM_LABELS, "roles": list(core.ROLE_NAMES.keys())}
        )
    try:
        body = await request.json()
    except Exception:
        return jr({"error": "bad_json"}, status=400)
    core.save_perms(body.get("perms") or {})
    return json_ok()


@require_auth(perm="view_stats")
async def api_stats(request):
    with core.db() as c:
        stats = {
            "users": c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"],
            "students": c.execute("SELECT COUNT(*) n FROM users WHERE role IN ('student','starosta')").fetchone()["n"],
            "teachers": c.execute("SELECT COUNT(*) n FROM users WHERE role='teacher'").fetchone()["n"],
            "homework": c.execute("SELECT COUNT(*) n FROM homework").fetchone()["n"],
            "submissions": c.execute("SELECT COUNT(*) n FROM submission").fetchone()["n"],
            "lessons": c.execute("SELECT COUNT(*) n FROM schedule").fetchone()["n"],
        }
    return jr({"stats": stats})


@require_auth(perm="view_students")
async def api_pending(request):
    if request.method == "GET":
        with core.db() as c:
            rows = c.execute("SELECT * FROM pending_requests ORDER BY id").fetchall()
        return jr({"pending": [dict(r) for r in rows]})
    body = await request.json()
    req_id = int(body.get("id") or 0)
    action = body.get("action")
    with core.db() as c:
        req = c.execute("SELECT * FROM pending_requests WHERE id=?", (req_id,)).fetchone()
        if not req:
            return jr({"error": "not_found"}, status=404)
        if action == "approve":
            exists = c.execute("SELECT 1 FROM users WHERE phone=?", (req["phone"],)).fetchone()
            if not exists:
                parts = req["full_name"].split(None, 1)
                user_id = c.execute(
                    "INSERT INTO users(tg_id, phone, first_name, last_name, role, created_at) VALUES(?,?,?,?,?,?) RETURNING id",
                    (
                        req["tg_id"],
                        req["phone"],
                        parts[0],
                        parts[1] if len(parts) > 1 else "",
                        "student",
                        datetime.datetime.now().isoformat(),
                    ),
                ).fetchone()["id"]
                today_iso = datetime.date.today().strftime("%Y%m%d")
                c.execute(
                    "INSERT INTO homework_status(homework_id, user_id) "
                    "SELECT id, ? FROM homework "
                    "WHERE substr(due_date,7,4)||substr(due_date,4,2)||substr(due_date,1,2) >= ? ON CONFLICT DO NOTHING",
                    (user_id, today_iso),
                )
            c.execute("DELETE FROM pending_requests WHERE id=?", (req_id,))
            await core.safe_send(
                core.bot_app.bot, req["tg_id"],
                "🎉 O'qituvchi so'rovingizni tasdiqladi! Endi botning barcha imkoniyatlari sizda.",
            )
        elif action == "reject":
            c.execute("DELETE FROM pending_requests WHERE id=?", (req_id,))
            await core.safe_send(core.bot_app.bot, req["tg_id"], "❌ So'rovingiz o'qituvchi tomonidan bekor qilindi.")
    return json_ok()


# ---------------- Rasm yuklash ----------------
@require_auth()
async def api_upload(request):
    form = await request.form()
    file = form.get("file")
    if not file or not hasattr(file, "read"):
        return jr({"error": "no_file"}, status=400)
    data = await file.read()
    if not data:
        return jr({"error": "no_file"}, status=400)
    if len(data) > 10 * 1024 * 1024:
        return jr({"error": "too_big"}, status=400)
    url, local = await core.save_image(data, file.filename or "rasm.jpg")
    return jr({"url": url})


# ---------------- Statik fayllar ----------------
async def index(request):
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


ROUTES = [
    Route("/", index),
    Route("/api/me", api_me),
    Route("/api/homework", api_homework),
    Route("/api/homework", api_homework_create, methods=["POST"]),
    Route("/api/homework/{hw_id:int}", api_homework_delete, methods=["DELETE"]),
    Route("/api/homework/{hw_id:int}/status", api_hw_status),
    Route("/api/submit", api_submit, methods=["POST"]),
    Route("/api/done", api_mark_done, methods=["POST"]),
    Route("/api/schedule", api_schedule),
    Route("/api/schedule", api_schedule_add, methods=["POST"]),
    Route("/api/schedule/{sch_id:int}", api_schedule_delete, methods=["DELETE"]),
    Route("/api/students", api_students),
    Route("/api/students", api_students_add, methods=["POST"]),
    Route("/api/students/{uid:int}", api_students_delete, methods=["DELETE"]),
    Route("/api/role", api_role_set, methods=["POST"]),
    Route("/api/permissions", api_permissions, methods=["GET", "POST"]),
    Route("/api/stats", api_stats),
    Route("/api/pending", api_pending, methods=["GET", "POST"]),
    Route("/api/upload", api_upload, methods=["POST"]),
]


def create_web_app(extra_routes=()) -> Starlette:
    os.makedirs(core.UPLOADS_DIR, exist_ok=True)
    return Starlette(
        routes=[*ROUTES, *extra_routes, Mount("/uploads", StaticFiles(directory=core.UPLOADS_DIR))]
    )

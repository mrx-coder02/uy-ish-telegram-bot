import datetime
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import time
import uuid

import httpx
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.error import Forbidden
from telegram.ext import (
    ApplicationBuilder,
    Application,
    BasePersistence,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    PersistenceInput,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("uy_ishlari_bot")

# Maxfiy sozlamalar .env faylidan o'qiladi (u git'ga kirmaydi)
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH, encoding="utf-8") as _f:
        for _line in _f:
            _k, _sep, _v = _line.strip().partition("=")
            if _sep and not _k.startswith("#"):
                os.environ.setdefault(_k.strip(), _v.strip())

# Vercel serverlari UTC'da ishlaydi; sana/vaqt hisoblari Toshkent bo'yicha bo'lsin
os.environ.setdefault("TZ", "Asia/Tashkent")
if hasattr(time, "tzset"):
    time.tzset()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DB_PATH = "uy_ishlari.db"
# Berilsa Postgres ishlatiladi (Vercel uchun shart), aks holda lokal SQLite
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
# Telegram webhook so'rovlarini tekshirish uchun (tokendan hosil qilinadi, alohida sozlash shart emas)
WEBHOOK_SECRET = hashlib.sha256(f"webhook:{BOT_TOKEN}".encode()).hexdigest()[:32]

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "uy-ishlari")

WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip()
if not WEBAPP_URL and os.environ.get("VERCEL_PROJECT_PRODUCTION_URL"):
    # Vercel'da Mini App manzili avtomatik: loyiha domeni
    WEBAPP_URL = f"https://{os.environ['VERCEL_PROJECT_PRODUCTION_URL']}/"
WEB_PORT = int(os.environ.get("WEB_PORT", "8000"))
# Vercel'da faqat /tmp ga yozish mumkin (vaqtinchalik); asosiy saqlash joyi Supabase
UPLOADS_DIR = "/tmp/uploads" if os.environ.get("VERCEL") else os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

bot_app: Application | None = None

WEEKDAYS = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]
ROLE_NAMES = {
    "student": "O'quvchi",
    "starosta": "Starosta",
    "teacher": "O'qituvchi",
    "developer": "Developer",
}

BTN_MY_HW = "🏠 Uy ishlarim"
BTN_SCHEDULE = "📅 Dars jadvali"
BTN_ADD_HW = "➕ Uy vazifa qo'shish"
BTN_ADD_NOTE = "📣 Eslatma yuborish"
BTN_ADD_STUDENT = "🧑‍🎓 O'quvchi qo'shish"
BTN_ADD_LESSON = "🗓 Dars qo'shish"
BTN_STUDENTS = "👥 O'quvchilar"
BTN_TASKS = "📋 Vazifalar"
BTN_SET_ROLE = "👤 Rol berish"
BTN_DEL_HW = "🗑 Uy vazifasi o'chirish"
BTN_DEL_SCHEDULE = "🗑 Jadval o'chirish"
BTN_STATS = "📊 Statistika"
BTN_LOGIN_PHONE = "📱 Telefon raqam bilan kirish"

# ---- Conversation holatlari ----
S_HW_SUBJECT, S_HW_TASK, S_HW_DATE = "hw1", "hw2", "hw3"
S_NOTE = "note1"
S_STU_PHONE, S_STU_NAME, S_STU_SURNAME = "stu1", "stu2", "stu3"
S_SCH_DAY, S_SCH_TIME, S_SCH_SUBJ = "sch1", "sch2", "sch3"
S_ROLE_ID, S_ROLE_ROLE = "role1", "role2"
S_PEND_NAME = "pend1"
S_SUBMIT = "sub1"

LOGIN_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
DATE_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")

# ---------------- Ruxsatlar (developer panel boshqaradi) ----------------
PERM_KEYS = [
    "submit_homework",
    "add_homework",
    "send_note",
    "add_student",
    "view_students",
    "add_lesson",
    "set_roles",
    "delete_content",
    "view_stats",
    "manage_permissions",
]
PERM_LABELS = {
    "submit_homework": "Vazifa topshirish",
    "add_homework": "Uy vazifa qo'shish",
    "send_note": "Eslatma yuborish",
    "add_student": "O'quvchi qo'shish",
    "view_students": "O'quvchilarni ko'rish",
    "add_lesson": "Dars jadvalini boshqarish",
    "set_roles": "Rol berish",
    "delete_content": "Vazifa/dars o'chirish",
    "view_stats": "Statistika",
    "manage_permissions": "Ruxsatlarni boshqarish",
}
DEFAULT_PERMS = {
    "student": ["submit_homework"],
    "starosta": ["submit_homework", "add_homework"],
    "teacher": [
        "submit_homework",
        "add_homework",
        "send_note",
        "add_student",
        "view_students",
        "add_lesson",
        "set_roles",
        "delete_content",
        "view_stats",
    ],
    "developer": list(PERM_KEYS),
}


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER UNIQUE,
    phone TEXT UNIQUE,
    first_name TEXT NOT NULL,
    last_name TEXT,
    role TEXT NOT NULL DEFAULT 'student',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS homework(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author_id INTEGER NOT NULL,
    subject TEXT NOT NULL,
    task TEXT,
    photo_url TEXT,
    due_date TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS homework_status(
    homework_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    accepted INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    reminded TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(homework_id, user_id)
);
CREATE TABLE IF NOT EXISTS submission(
    homework_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    text TEXT,
    photo_url TEXT,
    submitted_at TEXT NOT NULL,
    PRIMARY KEY(homework_id, user_id)
);
CREATE TABLE IF NOT EXISTS schedule(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    weekday INTEGER NOT NULL,
    time TEXT NOT NULL,
    subject TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_requests(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER NOT NULL,
    phone TEXT NOT NULL,
    full_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ptb_state(
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY(kind, key)
);
"""
# Postgres'da Telegram ID'lari 32-bitga sig'maydi, shuning uchun BIGINT
PG_SCHEMA = SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY").replace("INTEGER", "BIGINT")


class PgDB:
    """Postgres'ni sqlite3 kabi ishlatish: `?` belgilari, `with db() as c`, c.execute(...).fetchone().
    Jarayon davomida bitta ulanish qayta ishlatiladi (serverless'da har so'rovda ulanish sekin)."""

    _conn = None

    def _get(self):
        if PgDB._conn is None or PgDB._conn.closed:
            import psycopg
            from psycopg.rows import dict_row

            # prepare_threshold=None: Supabase pooler (transaction mode) prepared statement'larni qo'llamaydi
            PgDB._conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, prepare_threshold=None)
        return PgDB._conn

    def execute(self, sql, params=()):
        import psycopg

        conn = self._get()
        fresh = conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        try:
            return conn.execute(sql.replace("?", "%s"), params)
        except psycopg.OperationalError:
            # Uzilgan ulanish (masalan, pooler yopgan) — tranzaksiya boshida bo'lsa bir marta qayta ulanamiz
            if not (fresh and conn.broken):
                raise
            PgDB._conn = None
            return self._get().execute(sql.replace("?", "%s"), params)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        conn = self._get()
        if exc_type:
            conn.rollback()
        else:
            conn.commit()


def db():
    if DATABASE_URL:
        return PgDB()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    if DATABASE_URL:
        with db() as c:
            c.execute(PG_SCHEMA)
        return
    with db() as c:
        cols = [r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()]
        if cols and "phone" not in cols:
            # Eski sxemadan yangi sxemaga migratsiya
            c.executescript(
                """
                CREATE TABLE users_new(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER UNIQUE,
                    phone TEXT UNIQUE,
                    first_name TEXT NOT NULL,
                    last_name TEXT,
                    role TEXT NOT NULL DEFAULT 'student',
                    created_at TEXT NOT NULL
                );
                INSERT INTO users_new(tg_id, phone, first_name, last_name, role, created_at)
                    SELECT tg_id, NULL, first_name, last_name, role, created_at FROM users;
                DROP TABLE users;
                ALTER TABLE users_new RENAME TO users;
                """
            )
            # author_id va statuslarni yangi users.id ga o'tkazish
            c.execute(
                "UPDATE homework SET author_id=(SELECT id FROM users WHERE users.tg_id=homework.author_id) "
                "WHERE EXISTS(SELECT 1 FROM users WHERE users.tg_id=homework.author_id)"
            )
            c.executescript(
                """
                CREATE TABLE homework_status_new(
                    homework_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    accepted INTEGER NOT NULL DEFAULT 0,
                    done INTEGER NOT NULL DEFAULT 0,
                    reminded TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(homework_id, user_id)
                );
                INSERT INTO homework_status_new(homework_id, user_id, accepted, done, reminded)
                    SELECT s.homework_id, u.id, s.accepted, s.done, s.reminded
                    FROM homework_status s JOIN users u ON u.tg_id=s.tg_id;
                DROP TABLE homework_status;
                ALTER TABLE homework_status_new RENAME TO homework_status;
                """
            )
            log.info("Eski ma'lumot bazasi yangi sxemaga ko'chirildi")
        c.executescript(SCHEMA)


def get_setting(key, default=None):
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    with db() as c:
        c.execute(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_perms() -> dict:
    raw = get_setting("perms")
    if not raw:
        return {k: list(v) for k, v in DEFAULT_PERMS.items()}
    try:
        data = json.loads(raw)
        merged = {k: list(v) for k, v in DEFAULT_PERMS.items()}
        for role, perms in data.items():
            if role in merged:
                merged[role] = [p for p in perms if p in PERM_KEYS]
        return merged
    except Exception:
        return {k: list(v) for k, v in DEFAULT_PERMS.items()}


def save_perms(perms: dict):
    clean = {r: [p for p in (perms.get(r) or []) if p in PERM_KEYS] for r in DEFAULT_PERMS}
    set_setting("perms", json.dumps(clean))


def has_perm(user, perm: str) -> bool:
    if not user:
        return False
    role = user["role"]
    if role == "developer":
        return True
    return perm in get_perms().get(role, [])


# ---------------- Foydalanuvchilar ----------------
def get_user_by_tg(tg_id):
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id):
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_phone(phone):
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
        return dict(row) if row else None


def all_users(roles=None, with_tg_only=False):
    with db() as c:
        q = "SELECT * FROM users"
        cond, params = [], []
        if roles:
            cond.append(f"role IN ({','.join('?' * len(roles))})")
            params.extend(roles)
        if with_tg_only:
            cond.append("tg_id IS NOT NULL")
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY first_name, last_name"
        rows = c.execute(q, params).fetchall()
        return [dict(r) for r in rows]


def student_users():
    return all_users(("student", "starosta"))


def display_name(u):
    return f"{u['first_name']} {u['last_name'] or ''}".strip()


def normalize_phone(raw):
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if len(digits) == 9:
        digits = "998" + digits
    return "+" + digits


def days_left_text(due_str):
    try:
        due = datetime.datetime.strptime(due_str, "%d.%m.%Y").date()
    except (ValueError, TypeError):
        return "?"
    left = (due - datetime.date.today()).days
    if left < 0:
        return "🔴 Muddati o'tgan"
    if left == 0:
        return "⏰ BUGUN topshirilishi kerak!"
    return f"⏳ {left} kun qoldi"


def due_sort_key(due_str):
    try:
        return datetime.datetime.strptime(due_str, "%d.%m.%Y")
    except (ValueError, TypeError):
        return datetime.datetime.max


def homework_text(hw):
    author = None
    if hw.get("author_id"):
        author = get_user_by_id(hw["author_id"])
    author_name = display_name(author) + f" ({ROLE_NAMES[author['role']]})" if author else "—"
    lines = [
        "🔔 UY VAZIFASI",
        "",
        f"📘 Mavzu: {hw['subject']}",
        f"📝 Vazifa: {hw['task'] or '(faqat rasm)'}",
        f"🗓 Muddat: {hw['due_date']}",
        days_left_text(hw["due_date"]),
        f"👤 Yubordi: {author_name}",
    ]
    return "\n".join(lines)


async def safe_send(bot, chat_id, text, markup=None):
    if chat_id is None:
        return
    try:
        await bot.send_message(chat_id, text, reply_markup=markup)
    except Forbidden:
        log.warning("Foydalanuvchi %s botni bloklagan", chat_id)
    except Exception as e:
        log.warning("Xabar yuborilmadi %s: %s", chat_id, e)


def hw_inline(hw_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Oldim", callback_data=f"acc:{hw_id}"),
                InlineKeyboardButton("✔️ Bajarildi", callback_data=f"done:{hw_id}"),
            ],
            [InlineKeyboardButton("📤 Topshirish (rasm/matn)", callback_data=f"sub:{hw_id}")],
        ]
    )


async def send_hw_to_user(bot, hw, user, markup=True):
    """Vazifani bitta foydalanuvchiga yuborish (rasmli bo'lsa rasm bilan)."""
    if not user.get("tg_id"):
        return
    text = homework_text(hw)
    km = hw_inline(hw["id"]) if markup else None
    try:
        if hw.get("photo_url"):
            path = hw.get("_local_path")
            if path and os.path.exists(path):
                await bot.send_photo(user["tg_id"], open(path, "rb"), caption=text, reply_markup=km)
            elif hw["photo_url"].startswith("http"):
                await bot.send_photo(user["tg_id"], hw["photo_url"], caption=text, reply_markup=km)
            else:
                await bot.send_message(user["tg_id"], text + f"\n🖼 Rasm: {hw['photo_url']}", reply_markup=km)
        else:
            await bot.send_message(user["tg_id"], text, reply_markup=km)
    except Forbidden:
        log.warning("Foydalanuvchi %s botni bloklagan", user["tg_id"])
    except Exception as e:
        log.warning("Vazifa yuborilmadi %s: %s", user.get("tg_id"), e)


async def broadcast_homework(hw_id):
    with db() as c:
        hw = c.execute("SELECT * FROM homework WHERE id=?", (hw_id,)).fetchone()
    if not hw:
        return
    hw = dict(hw)
    for u in student_users():
        with db() as c:
            c.execute(
                "INSERT INTO homework_status(homework_id, user_id) VALUES(?,?) ON CONFLICT DO NOTHING",
                (hw_id, u["id"]),
            )
        await send_hw_to_user(bot_app.bot, hw, u)


# ---------------- Rasm saqlash (Supabase + lokal zaxira) ----------------
async def save_image(data: bytes, filename: str):
    """Rasmni Supabase Storage'ga yuklash; ishlamasa lokal saqlash.
    Qaytaradi: (url, lokal_path yoki None)"""
    fname = f"{datetime.date.today().isoformat()}_{uuid.uuid4().hex[:8]}_{re.sub(r'[^A-Za-z0-9._-]', '', filename) or 'rasm.jpg'}"
    ctype = "image/jpeg" if filename.lower().endswith((".jpg", ".jpeg")) else "image/png"
    try:
        url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{fname}"
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": ctype,
            "x-upsert": "true",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(url, content=data, headers=headers)
            if r.status_code in (200, 201):
                public = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{fname}"
                log.info("Rasm Supabase'ga yuklandi: %s", public)
                return public, None
            log.warning("Supabase yuklash xatosi (%s): %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("Supabase'ga ulanmadi: %s", e)
    local = os.path.join(UPLOADS_DIR, fname)
    with open(local, "wb") as f:
        f.write(data)
    return f"/uploads/{fname}", local


# ---------------- Menyular ----------------
def menu_keyboard(role):
    user_role = role
    perms = get_perms()
    rows = [[BTN_MY_HW, BTN_SCHEDULE]]
    admin = []
    if "add_homework" in perms.get(user_role, []) or user_role == "developer":
        admin.append(BTN_ADD_HW)
    if "send_note" in perms.get(user_role, []) or user_role == "developer":
        admin.append(BTN_ADD_NOTE)
    if admin:
        rows.append(admin)
    extra = []
    if "add_student" in perms.get(user_role, []) or user_role == "developer":
        extra.append(BTN_ADD_STUDENT)
    if "view_students" in perms.get(user_role, []) or user_role == "developer":
        extra.append(BTN_STUDENTS)
    if extra:
        rows.append(extra)
    rows2 = []
    if "set_roles" in perms.get(user_role, []) or user_role == "developer":
        rows2.append(BTN_SET_ROLE)
    if "add_lesson" in perms.get(user_role, []) or user_role == "developer":
        rows2.append(BTN_ADD_LESSON)
    if rows2:
        rows.append(rows2)
    if "delete_content" in perms.get(user_role, []) or user_role == "developer":
        rows.append([BTN_DEL_HW, BTN_DEL_SCHEDULE])
    if "view_stats" in perms.get(user_role, []) or user_role == "developer":
        rows.append([BTN_STATS])
    if WEBAPP_URL:
        rows.append([KeyboardButton("📱 Mini ilova", web_app=WebAppInfo(url=WEBAPP_URL))])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def login_keyboard():
    rows = [[KeyboardButton(BTN_LOGIN_PHONE, request_contact=True)]]
    if WEBAPP_URL:
        rows.append([KeyboardButton("📱 Mini ilova", web_app=WebAppInfo(url=WEBAPP_URL))])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def menu_reply_markup(user):
    return menu_keyboard(user["role"])


# ---------------- Asosiy buyruqlar ----------------
async def cmd_start(update: ContextTypes.DEFAULT_TYPE, context):
    user = get_user_by_tg(update.effective_user.id)
    if user:
        await update.message.reply_text(
            f"👋 Assalomu alaykum, {display_name(user)}!\n"
            f"🔒 Rolingiz: {ROLE_NAMES[user['role']]}\n\n"
            "Kerakli bo'limni tanlang:",
            reply_markup=menu_keyboard(user["role"]),
        )
    else:
        await update.message.reply_text(
            "👋 Botga xush kelibsiz!\n\n"
            "Uy vazifalari va dars jadvali shu botda.\n"
            "🔑 Kirish uchun «📱 Telefon raqam bilan kirish» tugmasini bosing.\n"
            "Sizni o'qituvchi ro'yxatdan o'tkazgan bo'lishi kerak.\n"
            "Agar o'qituvchi sizni qo'shmagan bo'lsa, raqam yuborganingizdan keyin "
            "ism va familiyangizni yozing — o'qituvchi tasdiqlaydi.",
            reply_markup=login_keyboard(),
        )


async def cmd_yordam(update: ContextTypes.DEFAULT_TYPE, context):
    user = get_user_by_tg(update.effective_user.id)
    text = (
        "📖 Buyruqlar:\n"
        "/start - botni qayta ishga tushirish\n"
        "/id - Telegram ID raqamingiz\n"
        "/yordam - yordam\n"
    )
    if user:
        if has_perm(user, "add_homework"):
            text += "/uyvazifa - uy vazifa qo'shish\n"
        if has_perm(user, "send_note"):
            text += "/eslatma - eslatma yuborish\n"
        if has_perm(user, "add_student"):
            text += "/oquvchi - o'quvchi qo'shish\n"
        if has_perm(user, "add_lesson"):
            text += "/dars - dars jadvaliga dars qo'shish\n"
        if has_perm(user, "set_roles"):
            text += "/rol - foydalanuvchiga rol berish\n"
    else:
        text += "🔑 Kirish uchun «📱 Telefon raqam bilan kirish» tugmasini bosing.\n"
    await update.message.reply_text(text)


async def cmd_id(update: ContextTypes.DEFAULT_TYPE, context):
    await update.message.reply_text(f"🆔 Sizning Telegram ID raqamingiz: {update.effective_user.id}")


async def cmd_dev(update: ContextTypes.DEFAULT_TYPE, context):
    user = get_user_by_tg(update.effective_user.id)
    if user:
        await update.message.reply_text(
            f"❗️ Siz allaqachon ro'yxatdan o'tgansiz. Rolingiz: {ROLE_NAMES[user['role']]}"
        )
        return
    with db() as c:
        dev = c.execute("SELECT id FROM users WHERE role='developer' LIMIT 1").fetchone()
        if dev:
            await update.message.reply_text("❗️ Developer allaqachon mavjud.")
            return
        c.execute(
            "INSERT INTO users(tg_id, phone, first_name, last_name, role, created_at) VALUES(?,?,?,?,?,?)",
            (
                update.effective_user.id,
                None,
                update.effective_user.first_name or "Developer",
                "",
                "developer",
                datetime.datetime.now().isoformat(),
            ),
        )
    await update.message.reply_text(
        "👑 Tabriklayman! Siz endi DEVELOPER ekansiz.\n"
        "To'liq boshqaruv: vazifalar, eslatmalar, o'quvchilar, dars jadvali, rollar, "
        "ruxsatlar, statistika. Barchasi «📱 Mini ilova»dagi Developer panelida.",
        reply_markup=menu_keyboard("developer"),
    )


async def cancel(update: ContextTypes.DEFAULT_TYPE, context):
    context.user_data.clear()
    user = get_user_by_tg(update.effective_user.id)
    await update.message.reply_text(
        "❌ Bekor qilindi.",
        reply_markup=menu_keyboard(user["role"]) if user else login_keyboard(),
    )
    return ConversationHandler.END


# ---------------- Telefon bilan kirish ----------------
async def login_contact(update: ContextTypes.DEFAULT_TYPE, context):
    contact = update.message.contact
    phone = normalize_phone(contact.phone_number)
    if not phone:
        await update.message.reply_text("❗️ Telefon raqam o'qilmadi. Qayta bosing:", reply_markup=login_keyboard())
        return ConversationHandler.END
    sender = update.effective_user.id
    user = get_user_by_phone(phone)
    if user:
        if user["tg_id"] == sender:
            await update.message.reply_text(
                "Siz allaqachon shu hisobdasiz 🙂", reply_markup=menu_keyboard(user["role"])
            )
            return ConversationHandler.END
        with db() as c:
            c.execute("UPDATE users SET tg_id=NULL WHERE tg_id=?", (sender,))
            c.execute("UPDATE users SET tg_id=? WHERE id=?", (sender, user["id"]))
        await update.message.reply_text(
            f"✅ Hisobga kirdingiz! Xush kelibsiz, {display_name(user)}.\n"
            f"🔒 Rolingiz: {ROLE_NAMES[user['role']]}",
            reply_markup=menu_keyboard(user["role"]),
        )
        return ConversationHandler.END
    context.user_data["pending_phone"] = phone
    await update.message.reply_text(
        "🆕 Raqamingiz bazada topilmadi.\n"
        "O'qituvchi tasdiqlashi uchun ism va familiyangizni yozing "
        "(masalan: Ali Valiyev):",
        reply_markup=ReplyKeyboardMarkup([["❌ Bekor"]], resize_keyboard=True),
    )
    return S_PEND_NAME


async def pend_name(update: ContextTypes.DEFAULT_TYPE, context):
    text = update.message.text.strip()
    if text == "❌ Bekor":
        return await cancel(update, context)
    parts = text.split()
    if len(parts) < 2:
        await update.message.reply_text("❗️ Ism va familiyani alohida yozing. Masalan: Ali Valiyev")
        return S_PEND_NAME
    phone = context.user_data.get("pending_phone")
    if not phone:
        return ConversationHandler.END
    with db() as c:
        cur = c.execute(
            "INSERT INTO pending_requests(tg_id, phone, full_name, created_at) VALUES(?,?,?,?) RETURNING id",
            (update.effective_user.id, phone, text, datetime.datetime.now().isoformat()),
        )
        req_id = cur.fetchone()["id"]
    context.user_data.clear()
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"pay:{req_id}"),
                InlineKeyboardButton("❌ Bekor qilish", callback_data=f"prej:{req_id}"),
            ]
        ]
    )
    for t in all_users(("teacher", "developer"), with_tg_only=True):
        await safe_send(
            bot_app.bot,
            t["tg_id"],
            "🆕 YANGI SO'ROV\n\n"
            f"👤 {text}\n"
            f"📱 {phone}\n"
            f"🆔 Telegram ID: {update.effective_user.id}",
            kb,
        )
    await update.message.reply_text(
        "✅ So'rovingiz yuborildi! O'qituvchi tasdiqlasa, botning barcha imkoniyatlari ochiladi.",
        reply_markup=login_keyboard(),
    )
    return ConversationHandler.END


async def cb_approve(update: ContextTypes.DEFAULT_TYPE, context):
    q = update.callback_query
    user = get_user_by_tg(update.effective_user.id)
    if not has_perm(user, "add_student"):
        await q.answer("❗️ Ruxsat yo'q", show_alert=True)
        return
    req_id = int(q.data.split(":")[1])
    with db() as c:
        req = c.execute("SELECT * FROM pending_requests WHERE id=?", (req_id,)).fetchone()
        if not req:
            await q.answer("So'rov topilmadi", show_alert=True)
            return
        exists = c.execute("SELECT 1 FROM users WHERE phone=?", (req["phone"],)).fetchone()
        if exists:
            c.execute("DELETE FROM pending_requests WHERE id=?", (req_id,))
            await q.answer("Bu raqam allaqachon ro'yxatda", show_alert=True)
            return
        parts = req["full_name"].split(None, 1)
        c.execute(
            "INSERT INTO users(tg_id, phone, first_name, last_name, role, created_at) VALUES(?,?,?,?,?,?)",
            (
                req["tg_id"],
                req["phone"],
                parts[0],
                parts[1] if len(parts) > 1 else "",
                "student",
                datetime.datetime.now().isoformat(),
            ),
        )
        c.execute("DELETE FROM pending_requests WHERE id=?", (req_id,))
    await q.answer("✅ O'quvchi qo'shildi!")
    try:
        await q.edit_message_text(f"✅ {req['full_name']} o'quvchi sifatida qo'shildi.")
    except Exception:
        pass
    u = get_user_by_tg(req["tg_id"])
    if u:
        await safe_send(
            bot_app.bot, req["tg_id"],
            "🎉 O'qituvchi so'rovingizni tasdiqladi! Endi botning barcha imkoniyatlari sizda.",
            reply_markup=menu_keyboard("student"),
        )


async def cb_reject(update: ContextTypes.DEFAULT_TYPE, context):
    q = update.callback_query
    user = get_user_by_tg(update.effective_user.id)
    if not has_perm(user, "add_student"):
        await q.answer("❗️ Ruxsat yo'q", show_alert=True)
        return
    req_id = int(q.data.split(":")[1])
    with db() as c:
        req = c.execute("SELECT * FROM pending_requests WHERE id=?", (req_id,)).fetchone()
        if req:
            c.execute("DELETE FROM pending_requests WHERE id=?", (req_id,))
    await q.answer("❌ So'rov bekor qilindi")
    try:
        await q.edit_message_text("❌ So'rov bekor qilindi.")
    except Exception:
        pass
    if req:
        await safe_send(bot_app.bot, req["tg_id"], "❌ So'rovingiz o'qituvchi tomonidan bekor qilindi.")


# ---------------- Uy vazifa qo'shish (matn/rasm) ----------------
async def hw_entry(update: ContextTypes.DEFAULT_TYPE, context):
    user = get_user_by_tg(update.effective_user.id)
    if not has_perm(user, "add_homework"):
        await update.message.reply_text("❗️ Bu bo'lim sizga ruxsat etilmagan.")
        return ConversationHandler.END
    context.user_data["hw"] = {"author": user}
    await update.message.reply_text(
        "📘 Fan nomini yozing (masalan: Matematika):",
        reply_markup=ReplyKeyboardMarkup([["❌ Bekor"]], resize_keyboard=True),
    )
    return S_HW_SUBJECT


async def hw_subject(update: ContextTypes.DEFAULT_TYPE, context):
    context.user_data["hw"]["subject"] = update.message.text.strip()
    await update.message.reply_text(
        "📝 Vazifa matnini yozing YOKI 📷 rasm yuboring (ikkalasi ham bo'lishi mumkin):\n"
        "✅ Faqat matn → «⏭ Davom etish» bosing\n"
        "✅ Faqat rasm → rasm yuborib, «⏭ Davom etish» bosing",
        reply_markup=ReplyKeyboardMarkup([["⏭ Davom etish"], ["❌ Bekor"]], resize_keyboard=True),
    )
    return S_HW_TASK


async def hw_task_text(update: ContextTypes.DEFAULT_TYPE, context):
    text = update.message.text.strip()
    if text == "❌ Bekor":
        return await cancel(update, context)
    if text == "⏭ Davom etish":
        if not context.user_data["hw"].get("task") and not context.user_data["hw"].get("photo_url"):
            await update.message.reply_text("❗️ Avval matn yozing yoki rasm yuboring:")
            return S_HW_TASK
        await update.message.reply_text("🗓 Vazifa muddatini yozing (masalan: 25.10.2026):")
        return S_HW_DATE
    context.user_data["hw"]["task"] = text
    await update.message.reply_text("🗓 Vazifa muddatini yozing (masalan: 25.10.2026):")
    return S_HW_DATE


async def hw_task_photo(update: ContextTypes.DEFAULT_TYPE, context):
    photo = update.message.photo[-1]
    f = await photo.get_file()
    data = bytes(await f.download_as_bytearray())
    url, local = await save_image(data, "vazifa.jpg")
    if local:
        context.user_data["hw"].setdefault("_local_paths", []).append(local)
    context.user_data["hw"]["photo_url"] = url
    await update.message.reply_text("🖼 Rasm qo'shildi. Endi «⏭ Davom etish» bosing yoki matn ham yozing:")
    return S_HW_TASK


async def hw_date(update: ContextTypes.DEFAULT_TYPE, context):
    text = update.message.text.strip()
    if text == "❌ Bekor":
        return await cancel(update, context)
    if not DATE_RE.match(text):
        await update.message.reply_text("❗️ Sana formati noto'g'ri. Masalan: 25.10.2026. Qayta yozing:")
        return S_HW_DATE
    try:
        due = datetime.datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        await update.message.reply_text("❗️ Bunday sana yo'q. Masalan: 25.10.2026. Qayta yozing:")
        return S_HW_DATE
    data = context.user_data["hw"]
    with db() as c:
        cur = c.execute(
            "INSERT INTO homework(author_id, subject, task, photo_url, due_date, created_at) VALUES(?,?,?,?,?,?) RETURNING id",
            (
                data["author"]["id"],
                data["subject"],
                data.get("task"),
                data.get("photo_url"),
                text,
                datetime.datetime.now().isoformat(),
            ),
        )
        hw_id = cur.fetchone()["id"]
    context.user_data.clear()
    left = (due - datetime.date.today()).days
    await update.message.reply_text(
        f"✅ Uy vazifasi qo'shildi va barcha o'quvchilarga yuborildi!\n\n"
        f"📘 {data['subject']}\n🗓 {text}\n⏳ {max(left, 0)} kun qoldi.\n"
        "Eslatmalar: 3 kun, 2 kun, 1 kun va muddat kuni o'zi avtomatik yuboriladi 🔔",
        reply_markup=menu_keyboard(data["author"]["role"]),
    )
    await broadcast_homework(hw_id)
    return ConversationHandler.END


# ---------------- Eslatma (o'qituvchi) ----------------
async def note_entry(update: ContextTypes.DEFAULT_TYPE, context):
    user = get_user_by_tg(update.effective_user.id)
    if not has_perm(user, "send_note"):
        await update.message.reply_text("❗️ Bu bo'lim sizga ruxsat etilmagan.")
        return ConversationHandler.END
    context.user_data["note_author"] = user
    await update.message.reply_text("📣 Eslatma matnini yozing (barcha o'quvchilarga yuboriladi):")
    return S_NOTE


async def note_send(update: ContextTypes.DEFAULT_TYPE, context):
    author = context.user_data.pop("note_author", None)
    text = update.message.text.strip()
    msg = (
        "📣 ESLATMA\n\n"
        f"{text}\n\n"
        f"👤 Yubordi: {display_name(author)} ({ROLE_NAMES[author['role']]})"
    )
    for u in all_users(("student", "starosta"), with_tg_only=True):
        await safe_send(context.bot, u["tg_id"], msg)
    await update.message.reply_text("✅ Eslatma barcha o'quvchilarga yuborildi!", reply_markup=menu_keyboard(author["role"]))
    return ConversationHandler.END


# ---------------- O'quvchi qo'shish (o'qituvchi) ----------------
async def stu_entry(update: ContextTypes.DEFAULT_TYPE, context):
    user = get_user_by_tg(update.effective_user.id)
    if not has_perm(user, "add_student"):
        await update.message.reply_text("❗️ Bu bo'lim sizga ruxsat etilmagan.")
        return ConversationHandler.END
    await update.message.reply_text(
        "📱 O'quvchining telefon raqamini yozing (masalan: +998901234567 yoki 901234567):"
    )
    return S_STU_PHONE


async def stu_phone(update: ContextTypes.DEFAULT_TYPE, context):
    text = update.message.text.strip()
    if text == "❌ Bekor":
        return await cancel(update, context)
    phone = normalize_phone(text)
    if not phone or len(phone) < 10:
        await update.message.reply_text("❗️ Telefon raqam noto'g'ri. Masalan: +998901234567. Qayta yozing:")
        return S_STU_PHONE
    if get_user_by_phone(phone):
        await update.message.reply_text("❗️ Bu raqam allaqachon ro'yxatda. Boshqa raqam yozing:")
        return S_STU_PHONE
    context.user_data["stu"] = {"phone": phone}
    await update.message.reply_text("📝 O'quvchining ismini yozing:")
    return S_STU_NAME


async def stu_name(update: ContextTypes.DEFAULT_TYPE, context):
    name = update.message.text.strip()
    if name == "❌ Bekor":
        return await cancel(update, context)
    if not (2 <= len(name) <= 30):
        await update.message.reply_text("❗️ Ism 2-30 harf oralig'ida bo'lishi kerak. Qayta yozing:")
        return S_STU_NAME
    context.user_data["stu"]["name"] = name
    await update.message.reply_text("📝 O'quvchining familiyasini yozing:")
    return S_STU_SURNAME


async def stu_surname(update: ContextTypes.DEFAULT_TYPE, context):
    surname = update.message.text.strip()
    if surname == "❌ Bekor":
        return await cancel(update, context)
    if not (2 <= len(surname) <= 30):
        await update.message.reply_text("❗️ Familiya 2-30 harf oralig'ida bo'lishi kerak. Qayta yozing:")
        return S_STU_SURNAME
    stu = context.user_data["stu"]
    with db() as c:
        cur = c.execute(
            "INSERT INTO users(tg_id, phone, first_name, last_name, role, created_at) VALUES(?,?,?,?,?,?) RETURNING id",
            (None, stu["phone"], stu["name"], surname, "student", datetime.datetime.now().isoformat()),
        )
        user_id = cur.fetchone()["id"]
    # Hali muddati o'tmagan vazifalarni yangi o'quvchiga berish
    today_iso = datetime.date.today().strftime("%Y%m%d")
    with db() as c:
        c.execute(
            "INSERT INTO homework_status(homework_id, user_id) "
            "SELECT id, ? FROM homework "
            "WHERE substr(due_date,7,4)||substr(due_date,4,2)||substr(due_date,1,2) >= ? ON CONFLICT DO NOTHING",
            (user_id, today_iso),
        )
    context.user_data.clear()
    await update.message.reply_text(
        f"✅ O'quvchi qo'shildi: {stu['name']} {surname} ({stu['phone']})\n"
        "O'quvchi botga kirib «📱 Telefon raqam bilan kirish» tugmasi orqali shu raqam bilan kirsagina ulanadi.\n"
        f"Hisob raqami (ID): {user_id}",
        reply_markup=menu_keyboard(get_user_by_tg(update.effective_user.id)["role"]),
    )
    return ConversationHandler.END


# ---------------- Dars jadvali ----------------
async def sch_entry(update: ContextTypes.DEFAULT_TYPE, context):
    user = get_user_by_tg(update.effective_user.id)
    if not has_perm(user, "add_lesson"):
        await update.message.reply_text("❗️ Bu bo'lim sizga ruxsat etilmagan.")
        return ConversationHandler.END
    await update.message.reply_text(
        "🗓 Qaysi kunga dars qo'shamiz?",
        reply_markup=ReplyKeyboardMarkup([[w] for w in WEEKDAYS] + [["❌ Bekor"]], resize_keyboard=True),
    )
    return S_SCH_DAY


async def sch_day(update: ContextTypes.DEFAULT_TYPE, context):
    text = update.message.text.strip()
    if text == "❌ Bekor":
        return await cancel(update, context)
    if text not in WEEKDAYS:
        await update.message.reply_text("❗️ Hafta kunini ro'yxatdan tanlang:")
        return S_SCH_DAY
    context.user_data["sch"] = {"weekday": WEEKDAYS.index(text)}
    await update.message.reply_text("🕐 Dars vaqtini yozing (masalan: 09:00):")
    return S_SCH_TIME


async def sch_time(update: ContextTypes.DEFAULT_TYPE, context):
    text = update.message.text.strip()
    if text == "❌ Bekor":
        return await cancel(update, context)
    if not TIME_RE.match(text):
        await update.message.reply_text("❗️ Vaqt formati noto'g'ri. Masalan: 09:00. Qayta yozing:")
        return S_SCH_TIME
    context.user_data["sch"]["time"] = f"{int(text.split(':')[0]):02d}:{text.split(':')[1]}"
    await update.message.reply_text("📘 Fan nomini yozing:")
    return S_SCH_SUBJ


async def sch_subject(update: ContextTypes.DEFAULT_TYPE, context):
    data = context.user_data["sch"]
    subject = update.message.text.strip()
    with db() as c:
        c.execute(
            "INSERT INTO schedule(weekday, time, subject) VALUES(?,?,?)",
            (data["weekday"], data["time"], subject),
        )
    context.user_data.clear()
    await update.message.reply_text(
        f"✅ Dars qo'shildi: {WEEKDAYS[data['weekday']]}, {data['time']} — {subject}",
        reply_markup=menu_keyboard(get_user_by_tg(update.effective_user.id)["role"]),
    )
    return ConversationHandler.END


def schedule_lines(weekday):
    with db() as c:
        rows = c.execute(
            "SELECT * FROM schedule WHERE weekday=? ORDER BY time", (weekday,)
        ).fetchall()
    if not rows:
        return None
    return "\n".join(f"🕐 {r['time']} — {r['subject']}" for r in rows)


# ---------------- Rol berish ----------------
def role_allowed_for_giver(giver_role):
    if giver_role == "developer":
        return ["student", "starosta", "teacher", "developer"]
    return ["student", "starosta"]


async def role_entry(update: ContextTypes.DEFAULT_TYPE, context):
    user = get_user_by_tg(update.effective_user.id)
    if not has_perm(user, "set_roles"):
        await update.message.reply_text("❗️ Bu bo'lim sizga ruxsat etilmagan.")
        return ConversationHandler.END
    context.user_data["giver_role"] = user["role"]
    await update.message.reply_text(
        "🆔 Rol beriladigan foydalanuvchining hisob raqamini (ID) yozing.\n"
        "(ID raqamlarni 👥 O'quvchilar bo'limida ko'rish mumkin)"
    )
    return S_ROLE_ID


async def role_id(update: ContextTypes.DEFAULT_TYPE, context):
    try:
        target_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❗️ Faqat raqam kiriting (masalan: 3):")
        return S_ROLE_ID
    target = get_user_by_id(target_id)
    if not target:
        await update.message.reply_text("❗️ Bu ID raqamli foydalanuvchi topilmadi. Qayta yozing:")
        return S_ROLE_ID
    if target["role"] == "developer" and context.user_data["giver_role"] != "developer":
        await update.message.reply_text("❗️ Developer rolini faqat developer o'zgartira oladi. Boshqa ID yozing:")
        return S_ROLE_ID
    context.user_data["target_id"] = target_id
    options = role_allowed_for_giver(context.user_data["giver_role"])
    kb = [[ROLE_NAMES[r]] for r in options]
    kb.append(["❌ Bekor"])
    await update.message.reply_text(
        f"👤 {display_name(target)} ({target['phone'] or 'telefonsiz'}) uchun rol tanlang:\n"
        f"Hozirgi roli: {ROLE_NAMES[target['role']]}",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True),
    )
    return S_ROLE_ROLE


async def role_pick(update: ContextTypes.DEFAULT_TYPE, context):
    text = update.message.text.strip()
    if text == "❌ Bekor":
        return await cancel(update, context)
    role_map = {v: k for k, v in ROLE_NAMES.items()}
    if text not in role_map:
        await update.message.reply_text("❗️ Ro'yxatdan rol tanlang:")
        return S_ROLE_ROLE
    role = role_map[text]
    giver = context.user_data["giver_role"]
    if role not in role_allowed_for_giver(giver):
        await update.message.reply_text("❗️ Siz bu rolni bera olmaysiz. Boshqa rol tanlang:")
        return S_ROLE_ROLE
    target_id = context.user_data.pop("target_id")
    context.user_data.clear()
    with db() as c:
        c.execute("UPDATE users SET role=? WHERE id=?", (role, target_id))
    target = get_user_by_id(target_id)
    await update.message.reply_text(
        f"✅ Rol o'zgartirildi: {display_name(target)} → {ROLE_NAMES[role]}",
        reply_markup=menu_keyboard(get_user_by_tg(update.effective_user.id)["role"]),
    )
    await safe_send(
        context.bot,
        target.get("tg_id"),
        f"🔔 Sizning rolingiz o'zgartirildi: {ROLE_NAMES[role]}",
        menu_keyboard(role) if target.get("tg_id") else None,
    )
    return ConversationHandler.END


# ---------------- Ko'rish bo'limlari ----------------
async def show_my_homework(update: ContextTypes.DEFAULT_TYPE, context):
    user = get_user_by_tg(update.effective_user.id)
    if not user:
        return
    today = datetime.date.today()
    if user["role"] in ("student", "starosta"):
        with db() as c:
            rows = c.execute(
                "SELECT h.*, s.accepted, s.done FROM homework h "
                "JOIN homework_status s ON s.homework_id=h.id AND s.user_id=?",
                (user["id"],),
            ).fetchall()
        rows = sorted((dict(r) for r in rows), key=lambda r: due_sort_key(r["due_date"]))
        if not rows:
            await update.message.reply_text("📭 Sizga hali uy vazifasi berilmagan.")
            return
        for hw in rows:
            status = "✔️ Bajarildi" if hw["done"] else ("✅ Qabul qilingan" if hw["accepted"] else "⏳ Kutilmoqda")
            text = f"{homework_text(hw)}\n\n📌 Holat: {status}"
            try:
                due_ok = datetime.datetime.strptime(hw["due_date"], "%d.%m.%Y").date() >= today
            except ValueError:
                due_ok = False
            if due_ok and not hw["done"]:
                await send_hw_to_user(context.bot, hw, user)
            else:
                await send_hw_to_user(context.bot, hw, user, markup=False)
    else:
        with db() as c:
            rows = c.execute(
                "SELECT * FROM homework WHERE author_id=? ORDER BY id DESC LIMIT 30", (user["id"],)
            ).fetchall()
            done_counts = c.execute(
                "SELECT homework_id, COUNT(*) total, SUM(done) done_n FROM homework_status GROUP BY homework_id"
            ).fetchall()
        counts = {r["homework_id"]: (r["done_n"] or 0, r["total"]) for r in done_counts}
        rows = sorted((dict(r) for r in rows), key=lambda r: due_sort_key(r["due_date"]))
        if not rows:
            await update.message.reply_text("📭 Siz hali uy vazifasi yubormagansiz.")
            return
        for hw in rows:
            done, total = counts.get(hw["id"], (0, 0))
            text = f"{homework_text(hw)}\n\n✅ Bajarilgan: {done}/{total}"
            if hw.get("photo_url"):
                if hw["photo_url"].startswith("http"):
                    await context.bot.send_photo(user["tg_id"], hw["photo_url"], caption=text)
                else:
                    local = os.path.join(UPLOADS_DIR, os.path.basename(hw["photo_url"]))
                    if os.path.exists(local):
                        await context.bot.send_photo(user["tg_id"], open(local, "rb"), caption=text)
                    else:
                        await safe_send(context.bot, user["tg_id"], text + f"\n🖼 {hw['photo_url']}")
            else:
                await update.message.reply_text(text)


async def show_schedule(update: ContextTypes.DEFAULT_TYPE, context):
    with db() as c:
        rows = c.execute("SELECT * FROM schedule ORDER BY weekday, time").fetchall()
    if not rows:
        await update.message.reply_text("📭 Dars jadvali hali to'ldirilmagan.")
        return
    lines = ["📅 DARS JADVALI\n"]
    for i, day in enumerate(WEEKDAYS):
        day_rows = [r for r in rows if r["weekday"] == i]
        if day_rows:
            lines.append(f"—— {day} ——")
            lines.extend(f"🕐 {r['time']} — {r['subject']}" for r in day_rows)
    await update.message.reply_text("\n".join(lines))


async def show_students(update: ContextTypes.DEFAULT_TYPE, context):
    user = get_user_by_tg(update.effective_user.id)
    if not has_perm(user, "view_students"):
        await update.message.reply_text("❗️ Bu bo'lim sizga ruxsat etilmagan.")
        return
    users = all_users()
    if not users:
        await update.message.reply_text("📭 Hali o'quvchi qo'shilmagan.")
        return
    lines = [f"👥 Jami {len(users)} foydalanuvchi:\n"]
    for u in users:
        tg = "✅ bog'langan" if u["tg_id"] else "⭕️ bog'lanmagan"
        lines.append(
            f"№{u['id']} — {display_name(u)} — {u['phone'] or 'telefonsiz'} — {ROLE_NAMES[u['role']]} — {tg}"
        )
    text = "\n".join(lines)
    for i in range(0, len(text), 3900):
        await update.message.reply_text(text[i : i + 3900])


async def show_tasks(update: ContextTypes.DEFAULT_TYPE, context):
    user = get_user_by_tg(update.effective_user.id)
    if not has_perm(user, "view_students") and not has_perm(user, "add_homework"):
        await update.message.reply_text("❗️ Bu bo'lim sizga ruxsat etilmagan.")
        return
    with db() as c:
        rows = c.execute("SELECT * FROM homework ORDER BY id DESC LIMIT 30").fetchall()
    if not rows:
        await update.message.reply_text("📭 Vazifalar yo'q.")
        return
    kb = [
        [InlineKeyboardButton(f"👁 {r['id']}. {r['subject']} ({r['due_date']})", callback_data=f"viewhw:{r['id']}")]
        for r in rows
    ]
    await update.message.reply_text(
        "📋 Vazifalar. Holatini ko'rish uchun tanlang:",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def show_stats(update: ContextTypes.DEFAULT_TYPE, context):
    user = get_user_by_tg(update.effective_user.id)
    if not has_perm(user, "view_stats"):
        await update.message.reply_text("❗️ Bu bo'lim sizga ruxsat etilmagan.")
        return
    with db() as c:
        total_users = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        students = c.execute("SELECT COUNT(*) n FROM users WHERE role IN ('student','starosta')").fetchone()["n"]
        teachers = c.execute("SELECT COUNT(*) n FROM users WHERE role='teacher'").fetchone()["n"]
        hws = c.execute("SELECT COUNT(*) n FROM homework").fetchone()["n"]
        lessons = c.execute("SELECT COUNT(*) n FROM schedule").fetchone()["n"]
        subs = c.execute("SELECT COUNT(*) n FROM submission").fetchone()["n"]
    await update.message.reply_text(
        "📊 STATISTIKA\n\n"
        f"👥 Foydalanuvchilar: {total_users}\n"
        f"🎓 O'quvchi va starosalar: {students}\n"
        f"👨‍🏫 O'qituvchilar: {teachers}\n"
        f"📚 Vazifalar: {hws}\n"
        f"📤 Topshirilgan ishlar: {subs}\n"
        f"🗓 Dars jadvalida: {lessons} dars"
    )


async def del_hw_menu(update: ContextTypes.DEFAULT_TYPE, context):
    user = get_user_by_tg(update.effective_user.id)
    if not has_perm(user, "delete_content"):
        await update.message.reply_text("❗️ Bu bo'lim sizga ruxsat etilmagan.")
        return
    with db() as c:
        rows = c.execute("SELECT * FROM homework ORDER BY id DESC LIMIT 30").fetchall()
    if not rows:
        await update.message.reply_text("📭 O'chirish uchun vazifa yo'q.")
        return
    kb = [
        [InlineKeyboardButton(f"🗑 {r['id']}. {r['subject']} ({r['due_date']})", callback_data=f"delhw:{r['id']}")]
        for r in rows
    ]
    await update.message.reply_text(
        "🗑 O'chirish uchun vazifani tanlang (darhol o'chadi!):",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def del_schedule_menu(update: ContextTypes.DEFAULT_TYPE, context):
    user = get_user_by_tg(update.effective_user.id)
    if not has_perm(user, "delete_content"):
        await update.message.reply_text("❗️ Bu bo'lim sizga ruxsat etilmagan.")
        return
    with db() as c:
        rows = c.execute("SELECT * FROM schedule ORDER BY weekday, time").fetchall()
    if not rows:
        await update.message.reply_text("📭 O'chirish uchun dars yo'q.")
        return
    kb = [
        [
            InlineKeyboardButton(
                f"🗑 {WEEKDAYS[r['weekday']]} {r['time']} — {r['subject']}",
                callback_data=f"delsch:{r['id']}",
            )
        ]
        for r in rows
    ]
    await update.message.reply_text("🗑 O'chirish uchun darsni tanlang:", reply_markup=InlineKeyboardMarkup(kb))


# ---------------- Callbacklar ----------------
async def cb_acc(update: ContextTypes.DEFAULT_TYPE, context):
    q = update.callback_query
    user = get_user_by_tg(update.effective_user.id)
    if not user:
        await q.answer("❗️ Avval telefon raqam bilan kiring.", show_alert=True)
        return
    hw_id = int(q.data.split(":")[1])
    with db() as c:
        cur = c.execute(
            "UPDATE homework_status SET accepted=1 WHERE homework_id=? AND user_id=?",
            (hw_id, user["id"]),
        )
        if cur.rowcount == 0:
            await q.answer("❗️ Bu vazifa sizga berilmagan.", show_alert=True)
            return
    await q.answer("✅ Qabul qilindi!")


async def cb_done(update: ContextTypes.DEFAULT_TYPE, context):
    q = update.callback_query
    user = get_user_by_tg(update.effective_user.id)
    if not user:
        await q.answer("❗️ Avval telefon raqam bilan kiring.", show_alert=True)
        return
    hw_id = int(q.data.split(":")[1])
    with db() as c:
        prev = c.execute(
            "SELECT done FROM homework_status WHERE homework_id=? AND user_id=?",
            (hw_id, user["id"]),
        ).fetchone()
        cur = c.execute(
            "UPDATE homework_status SET accepted=1, done=1 WHERE homework_id=? AND user_id=?",
            (hw_id, user["id"]),
        )
        if cur.rowcount == 0:
            await q.answer("❗️ Bu vazifa sizga berilmagan.", show_alert=True)
            return
        hw = c.execute("SELECT author_id, subject FROM homework WHERE id=?", (hw_id,)).fetchone()
    already_done = bool(prev and prev["done"])
    await q.answer("✔️ Bajarildi deb belgilandi!" if not already_done else "✔️ Allaqachon belgilangan")
    if hw and not already_done:
        author = get_user_by_id(hw["author_id"])
        if author and author.get("tg_id"):
            with db() as c:
                done_count = c.execute(
                    "SELECT COUNT(*) n FROM homework_status WHERE homework_id=? AND done=1", (hw_id,)
                ).fetchone()["n"]
                total = c.execute(
                    "SELECT COUNT(*) n FROM homework_status WHERE homework_id=?", (hw_id,)
                ).fetchone()["n"]
            await safe_send(
                context.bot,
                author["tg_id"],
                f"✔️ {display_name(user)} vazifani bajarildi deb belgiladi.\n"
                f"📘 {hw['subject']}\n"
                f"👥 Jami bajarilgan: {done_count}/{total}",
            )


async def cb_view_hw(update: ContextTypes.DEFAULT_TYPE, context):
    q = update.callback_query
    user = get_user_by_tg(update.effective_user.id)
    if not has_perm(user, "view_students") and not has_perm(user, "add_homework"):
        await q.answer("❗️ Ruxsat yo'q", show_alert=True)
        return
    hw_id = int(q.data.split(":")[1])
    with db() as c:
        hw = c.execute("SELECT * FROM homework WHERE id=?", (hw_id,)).fetchone()
        if not hw:
            await q.answer("Vazifa o'chirilgan", show_alert=True)
            return
        statuses = c.execute(
            "SELECT s.*, u.first_name, u.last_name, u.role, "
            "sub.photo_url AS sub_photo, sub.text AS sub_text "
            "FROM homework_status s "
            "JOIN users u ON u.id=s.user_id "
            "LEFT JOIN submission sub ON sub.homework_id=s.homework_id AND sub.user_id=s.user_id "
            "WHERE s.homework_id=? ORDER BY u.first_name",
            (hw_id,),
        ).fetchall()
    lines = [homework_text(dict(hw)), "", "👥 O'QUVCHILAR HOLATI:"]
    for s in statuses:
        name = f"{s['first_name']} {s['last_name'] or ''}".strip()
        if s["done"]:
            mark = "✔️ Bajarildi"
        elif s["accepted"]:
            mark = "✅ Qabul qilgan"
        else:
            mark = "⏳ Javob bermagan"
        extra = ""
        if s["sub_photo"]:
            extra = " 📤 topshirilgan"
        elif s["sub_text"]:
            extra = f" 📤 matn: {s['sub_text'][:40]}"
        lines.append(f"• {name} — {mark}{extra}")
    await q.message.reply_text("\n".join(lines))
    await q.answer()


async def cb_del_hw(update: ContextTypes.DEFAULT_TYPE, context):
    q = update.callback_query
    user = get_user_by_tg(update.effective_user.id)
    if not has_perm(user, "delete_content"):
        await q.answer("❗️ Ruxsat yo'q", show_alert=True)
        return
    hw_id = int(q.data.split(":")[1])
    with db() as c:
        c.execute("DELETE FROM homework WHERE id=?", (hw_id,))
        c.execute("DELETE FROM homework_status WHERE homework_id=?", (hw_id,))
        c.execute("DELETE FROM submission WHERE homework_id=?", (hw_id,))
    await q.answer(f"🗑 {hw_id}-vazifa o'chirildi!")
    try:
        await q.edit_message_text(f"🗑 {hw_id}-vazifa o'chirildi.")
    except Exception:
        pass


async def cb_del_schedule(update: ContextTypes.DEFAULT_TYPE, context):
    q = update.callback_query
    user = get_user_by_tg(update.effective_user.id)
    if not has_perm(user, "delete_content"):
        await q.answer("❗️ Ruxsat yo'q", show_alert=True)
        return
    sch_id = int(q.data.split(":")[1])
    with db() as c:
        c.execute("DELETE FROM schedule WHERE id=?", (sch_id,))
    await q.answer("🗑 Dars o'chirildi!")
    try:
        await q.edit_message_text("🗑 Dars jadvalidan o'chirildi.")
    except Exception:
        pass


# ---------------- Vazifa topshirish (o'quvchi: rasm/matn) ----------------
async def sub_entry(update: ContextTypes.DEFAULT_TYPE, context):
    q = update.callback_query
    user = get_user_by_tg(update.effective_user.id)
    if not user:
        await q.answer("❗️ Avval telefon raqam bilan kiring.", show_alert=True)
        return ConversationHandler.END
    hw_id = int(q.data.split(":")[1])
    with db() as c:
        hw = c.execute("SELECT * FROM homework WHERE id=?", (hw_id,)).fetchone()
        assigned = c.execute(
            "SELECT 1 FROM homework_status WHERE homework_id=? AND user_id=?", (hw_id, user["id"])
        ).fetchone()
    if not hw or not assigned:
        await q.answer("❗️ Bu vazifa sizga berilmagan.", show_alert=True)
        return ConversationHandler.END
    context.user_data["submit"] = {"hw_id": hw_id, "user": user, "subject": hw["subject"]}
    await q.answer()
    await q.message.reply_text(
        f"📤 «{hw['subject']}» vazifasini topshirish:\n📷 Rasm yuboring yoki 📝 matn yozing:",
        reply_markup=ReplyKeyboardMarkup([["❌ Bekor"]], resize_keyboard=True),
    )
    return S_SUBMIT


async def submit_receive_photo(update: ContextTypes.DEFAULT_TYPE, context):
    photo = update.message.photo[-1]
    f = await photo.get_file()
    data = bytes(await f.download_as_bytearray())
    url, local = await save_image(data, "topshirish.jpg")
    await finish_submission(context, text=None, photo_url=url, local=local)
    return ConversationHandler.END


async def submit_receive_text(update: ContextTypes.DEFAULT_TYPE, context):
    text = update.message.text.strip()
    if text == "❌ Bekor":
        return await cancel(update, context)
    await finish_submission(context, text=text, photo_url=None, local=None)
    return ConversationHandler.END


async def finish_submission(context, text, photo_url, local):
    data = context.user_data["submit"]
    user = data["user"]
    hw_id = data["hw_id"]
    with db() as c:
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
    context.user_data.clear()
    await context.bot.send_message(
        user["tg_id"],
        "✅ Vazifangiz topshirildi va bajarilgan deb belgilandi!",
        reply_markup=menu_keyboard(user["role"]),
    )
    author = get_user_by_id(hw["author_id"]) if hw else None
    if author and author.get("tg_id"):
        msg = (
            f"📤 {display_name(user)} vazifani topshirdi.\n"
            f"📘 {hw['subject']}\n"
            + (f"📝 {text}\n" if text else "")
            + ("🖼 Rasm ilova qilingan" if photo_url else "")
        )
        try:
            if photo_url and local and os.path.exists(local):
                await context.bot.send_photo(author["tg_id"], open(local, "rb"), caption=msg)
            elif photo_url and photo_url.startswith("http"):
                await context.bot.send_photo(author["tg_id"], photo_url, caption=msg)
            else:
                await safe_send(context.bot, author["tg_id"], msg)
        except Exception:
            await safe_send(context.bot, author["tg_id"], msg)


# ---------------- Avtomatik eslatmalar ----------------
async def job_morning(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.date.today()
    lessons = schedule_lines(today.weekday())
    if not lessons:
        return
    text = f"🌅 Bugun ({WEEKDAYS[today.weekday()]}) dars jadvali:\n\n{lessons}"
    for u in all_users(with_tg_only=True):
        await safe_send(context.bot, u["tg_id"], text)


async def job_lessons_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Darsga 1/2/3 kun qolganda eslatish."""
    today = datetime.date.today()
    parts = ["🗓 DARS ESLATMASI\n"]
    found = False
    for offset in (3, 2, 1):
        day = today + datetime.timedelta(days=offset)
        lessons = schedule_lines(day.weekday())
        if lessons:
            found = True
            parts.append(f"\n⏳ {offset} kun qoldi — {WEEKDAYS[day.weekday()]} ({day.strftime('%d.%m.%Y')}):\n{lessons}")
    if not found:
        return
    for u in all_users(with_tg_only=True):
        await safe_send(context.bot, u["tg_id"], "\n".join(parts))


async def job_homework(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.date.today()
    with db() as c:
        rows = c.execute("SELECT * FROM homework").fetchall()
    for r in rows:
        hw = dict(r)
        try:
            due = datetime.datetime.strptime(hw["due_date"], "%d.%m.%Y").date()
        except ValueError:
            continue
        left = (due - today).days
        if left < 0 or left > 3:
            continue
        text = (
            f"⏰ ESLATMA!\n\n{homework_text(hw)}\n\n"
            + ("⏰ Muddat BUGUN! Vazifani unutmang!" if left == 0 else f"⏳ Muddatga {left} kun qoldi. Vazifani bajarishni unutmang!")
        )
        with db() as c:
            recipients = c.execute(
                "SELECT s.*, u.tg_id FROM homework_status s JOIN users u ON u.id=s.user_id "
                "WHERE s.homework_id=? AND s.done=0",
                (hw["id"],),
            ).fetchall()
        for rec in recipients:
            if not rec["tg_id"]:
                continue
            with db() as c:
                row = c.execute(
                    "SELECT reminded FROM homework_status WHERE homework_id=? AND user_id=?",
                    (hw["id"], rec["user_id"]),
                ).fetchone()
                if not row:
                    c.execute(
                        "INSERT INTO homework_status(homework_id, user_id) VALUES(?,?)",
                        (hw["id"], rec["user_id"]),
                    )
                    reminded = ""
                else:
                    reminded = row["reminded"]
                if str(left) in reminded.split(","):
                    continue
                c.execute(
                    "UPDATE homework_status SET reminded=? WHERE homework_id=? AND user_id=?",
                    (reminded + f",{left}" if reminded else str(left), hw["id"], rec["user_id"]),
                )
            await safe_send(context.bot, rec["tg_id"], text)


async def job_evening(context: ContextTypes.DEFAULT_TYPE):
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    lessons = schedule_lines(tomorrow.weekday())
    with db() as c:
        due_rows = c.execute("SELECT * FROM homework WHERE due_date=?", (tomorrow.strftime("%d.%m.%Y"),)).fetchall()
    parts = [f"🌙 Ertaga ({WEEKDAYS[tomorrow.weekday()]}) dars jadvali:"]
    if lessons:
        parts.append(lessons)
    if due_rows:
        parts.append("\n📚 Ertaga topshiriladigan uy vazifalari:")
        for r in due_rows:
            parts.append(f"• {r['subject']}: {r['task'] or '(rasm)'}")
    if not lessons and not due_rows:
        return
    parts.append("\n👁 Uy vazifalaringizni ko'rish uchun «🏠 Uy ishlarim» tugmasini bosing.")
    text = "\n".join(parts)
    for u in all_users(with_tg_only=True):
        await safe_send(context.bot, u["tg_id"], text)


# ---------------- Handlerlar ro'yxati ----------------
def register_handlers(app):
    hw_conv = ConversationHandler(
        name="hw",
        persistent=True,
        entry_points=[
            MessageHandler(filters.Text([BTN_ADD_HW]), hw_entry),
            CommandHandler("uyvazifa", hw_entry),
        ],
        states={
            S_HW_SUBJECT: [
                CommandHandler("cancel", cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, hw_subject),
            ],
            S_HW_TASK: [
                CommandHandler("cancel", cancel),
                MessageHandler(filters.PHOTO, hw_task_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, hw_task_text),
            ],
            S_HW_DATE: [
                CommandHandler("cancel", cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, hw_date),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    note_conv = ConversationHandler(
        name="note",
        persistent=True,
        entry_points=[
            MessageHandler(filters.Text([BTN_ADD_NOTE]), note_entry),
            CommandHandler("eslatma", note_entry),
        ],
        states={
            S_NOTE: [
                CommandHandler("cancel", cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, note_send),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    stu_conv = ConversationHandler(
        name="stu",
        persistent=True,
        entry_points=[
            MessageHandler(filters.Text([BTN_ADD_STUDENT]), stu_entry),
            CommandHandler("oquvchi", stu_entry),
        ],
        states={
            S_STU_PHONE: [
                CommandHandler("cancel", cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, stu_phone),
            ],
            S_STU_NAME: [
                CommandHandler("cancel", cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, stu_name),
            ],
            S_STU_SURNAME: [
                CommandHandler("cancel", cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, stu_surname),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    sch_conv = ConversationHandler(
        name="sch",
        persistent=True,
        entry_points=[
            MessageHandler(filters.Text([BTN_ADD_LESSON]), sch_entry),
            CommandHandler("dars", sch_entry),
        ],
        states={
            S_SCH_DAY: [
                CommandHandler("cancel", cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, sch_day),
            ],
            S_SCH_TIME: [
                CommandHandler("cancel", cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, sch_time),
            ],
            S_SCH_SUBJ: [
                CommandHandler("cancel", cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, sch_subject),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    role_conv = ConversationHandler(
        name="role",
        persistent=True,
        entry_points=[
            MessageHandler(filters.Text([BTN_SET_ROLE]), role_entry),
            CommandHandler("rol", role_entry),
        ],
        states={
            S_ROLE_ID: [
                CommandHandler("cancel", cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, role_id),
            ],
            S_ROLE_ROLE: [
                CommandHandler("cancel", cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, role_pick),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    pend_conv = ConversationHandler(
        name="pend",
        persistent=True,
        entry_points=[MessageHandler(filters.CONTACT, login_contact)],
        states={
            S_PEND_NAME: [
                CommandHandler("cancel", cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, pend_name),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    submit_conv = ConversationHandler(
        name="submit",
        persistent=True,
        entry_points=[CallbackQueryHandler(sub_entry, pattern=r"^sub:\d+$")],
        states={
            S_SUBMIT: [
                CommandHandler("cancel", cancel),
                MessageHandler(filters.PHOTO, submit_receive_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, submit_receive_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("yordam", cmd_yordam))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("dev", cmd_dev))
    app.add_handler(pend_conv)
    app.add_handler(submit_conv)
    app.add_handler(hw_conv)
    app.add_handler(note_conv)
    app.add_handler(stu_conv)
    app.add_handler(sch_conv)
    app.add_handler(role_conv)

    app.add_handler(CallbackQueryHandler(cb_acc, pattern=r"^acc:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_done, pattern=r"^done:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_view_hw, pattern=r"^viewhw:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_del_hw, pattern=r"^delhw:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_del_schedule, pattern=r"^delsch:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_approve, pattern=r"^pay:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_reject, pattern=r"^prej:\d+$"))

    app.add_handler(MessageHandler(filters.Text([BTN_MY_HW]), show_my_homework))
    app.add_handler(MessageHandler(filters.Text([BTN_SCHEDULE]), show_schedule))
    app.add_handler(MessageHandler(filters.Text([BTN_STUDENTS]), show_students))
    app.add_handler(MessageHandler(filters.Text([BTN_TASKS]), show_tasks))
    app.add_handler(MessageHandler(filters.Text([BTN_STATS]), show_stats))
    app.add_handler(MessageHandler(filters.Text([BTN_DEL_HW]), del_hw_menu))
    app.add_handler(MessageHandler(filters.Text([BTN_DEL_SCHEDULE]), del_schedule_menu))

    app.add_handler(MessageHandler(filters.CONTACT, login_contact))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_menu))


async def unknown_menu(update: ContextTypes.DEFAULT_TYPE, context):
    user = get_user_by_tg(update.effective_user.id)
    if not user:
        await update.message.reply_text(
            "🤖 Tushunmadi. Kirish uchun «📱 Telefon raqam bilan kirish» tugmasini bosing:",
            reply_markup=login_keyboard(),
        )
        return
    await update.message.reply_text(
        "🤖 Buyruq tushunmadi. Kerakli bo'limni tanlang:",
        reply_markup=menu_keyboard(user["role"]),
    )


async def on_error(update, context):
    log.error("Xatolik: %s", context.error, exc_info=context.error)


class DBPersistence(BasePersistence):
    """Suhbat bosqichlari va user_data bazada (ptb_state jadvali) saqlanadi.
    Vercel'da har bir xabar alohida chaqiruv, shuning uchun xotiraga tayanib bo'lmaydi."""

    def __init__(self):
        super().__init__(
            store_data=PersistenceInput(bot_data=False, chat_data=False, user_data=True, callback_data=False),
            update_interval=60,
        )

    @staticmethod
    def _load(kind):
        with db() as c:
            rows = c.execute("SELECT key, value FROM ptb_state WHERE kind=?", (kind,)).fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    @staticmethod
    def _save(kind, key, value):
        with db() as c:
            if value is None:
                c.execute("DELETE FROM ptb_state WHERE kind=? AND key=?", (kind, key))
            else:
                c.execute(
                    "INSERT INTO ptb_state(kind, key, value) VALUES(?,?,?) "
                    "ON CONFLICT(kind, key) DO UPDATE SET value=excluded.value",
                    (kind, key, json.dumps(value, ensure_ascii=False)),
                )

    async def get_user_data(self):
        return {int(k): v for k, v in self._load("user").items()}

    async def update_user_data(self, user_id, data):
        self._save("user", str(user_id), dict(data) or None)

    async def drop_user_data(self, user_id):
        self._save("user", str(user_id), None)

    async def get_conversations(self, name):
        return {tuple(json.loads(k)): v for k, v in self._load(f"conv:{name}").items()}

    async def update_conversation(self, name, key, new_state):
        self._save(f"conv:{name}", json.dumps(list(key)), new_state)

    # chat_data / bot_data / callback_data ishlatilmaydi
    async def get_chat_data(self):
        return {}

    async def get_bot_data(self):
        return {}

    async def get_callback_data(self):
        return None

    async def update_chat_data(self, chat_id, data):
        pass

    async def update_bot_data(self, data):
        pass

    async def update_callback_data(self, data):
        pass

    async def drop_chat_data(self, chat_id):
        pass

    async def refresh_user_data(self, user_id, user_data):
        pass

    async def refresh_chat_data(self, chat_id, chat_data):
        pass

    async def refresh_bot_data(self, bot_data):
        pass

    async def flush(self):
        pass


async def post_init(app: Application):
    """Lokal rejim: Mini App web-serverini bot bilan birga ishga tushirish."""
    global bot_app
    bot_app = app
    import uvicorn
    from server import create_web_app

    server = uvicorn.Server(uvicorn.Config(create_web_app(), host="0.0.0.0", port=WEB_PORT, log_level="warning"))
    server.install_signal_handlers = lambda: None  # Ctrl+C'ni bot boshqaradi
    app.create_task(server.serve())
    log.info("Mini App web-server %s-portda ishga tushdi", WEB_PORT)


def main():
    """Lokal rejim (polling). Vercel'da bu ishlatilmaydi — kirish nuqtasi api/index.py."""
    if not BOT_TOKEN.strip():
        raise SystemExit("❗️ .env faylida BOT_TOKEN yo'q!")
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).persistence(DBPersistence()).post_init(post_init).build()
    register_handlers(app)
    app.add_error_handler(on_error)
    local_tz = datetime.datetime.now().astimezone().tzinfo
    app.job_queue.run_daily(job_morning, time=datetime.time(7, 30, tzinfo=local_tz), name="morning")
    app.job_queue.run_daily(job_homework, time=datetime.time(8, 0, tzinfo=local_tz), name="hw_reminder")
    app.job_queue.run_daily(job_lessons_reminder, time=datetime.time(19, 0, tzinfo=local_tz), name="lessons_reminder")
    app.job_queue.run_daily(job_evening, time=datetime.time(19, 30, tzinfo=local_tz), name="evening")
    log.info("Bot ishga tushdi (lokal, polling). Diqqat: bu Vercel webhook'ini o'chiradi!")
    app.run_polling()


if __name__ == "__main__":
    main()

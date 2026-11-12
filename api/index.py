"""Vercel serverless kirish nuqtasi (ASGI).

Bot bu yerda WEBHOOK rejimida ishlaydi: har bir Telegram xabari alohida chaqiruv.
Yo'llar:
  GET  /                     → Mini App
  GET/POST /api/...          → Mini App API (server.py)
  POST /api/webhook          → Telegram update
  GET  /api/setup            → webhook'ni ulash (deploy'dan keyin bir marta brauzerda oching)
  GET  /api/cron/morning     → 07:30 va 08:00 eslatmalari (Vercel Cron)
  GET  /api/cron/evening     → 19:00 va 19:30 eslatmalari (Vercel Cron)
"""
import hmac
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from starlette.responses import JSONResponse, PlainTextResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402
from telegram import Update  # noqa: E402
from telegram.ext import ApplicationBuilder, ExtBot  # noqa: E402

import bot as core  # noqa: E402
import server  # noqa: E402

_bot: ExtBot | None = None

CRON_JOBS = {
    "morning": (core.job_morning, core.job_homework),
    "evening": (core.job_lessons_reminder, core.job_evening),
}


async def _ensure_ready():
    """Sovuq ishga tushishda bir marta: baza jadvallari va bot ulanishi."""
    global _bot
    if _bot is None:
        core.init_db()
        bot = ExtBot(core.BOT_TOKEN)
        await bot.initialize()
        core.bot_app = SimpleNamespace(bot=bot)  # server.py va broadcast shu orqali yuboradi
        _bot = bot


async def webhook(request):
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(token, core.WEBHOOK_SECRET):
        return PlainTextResponse("forbidden", status_code=403)
    data = await request.json()
    # Har so'rovda yangi Application: suhbat holati bazadan yangidan o'qiladi
    # (bir nechta serverless nusxa bir vaqtda ishlasa ham holat to'g'ri bo'ladi)
    app = (
        ApplicationBuilder().bot(_bot).updater(None).job_queue(None)
        .persistence(core.DBPersistence()).build()
    )
    core.register_handlers(app)
    app.add_error_handler(core.on_error)
    await app.initialize()
    await app.process_update(Update.de_json(data, _bot))
    await app.update_persistence()
    return PlainTextResponse("ok")


async def setup(request):
    url = f"https://{request.url.hostname}/api/webhook"
    await _bot.set_webhook(url, secret_token=core.WEBHOOK_SECRET, allowed_updates=Update.ALL_TYPES)
    info = await _bot.get_webhook_info()
    return JSONResponse({"ok": True, "webhook": info.url, "mini_app": core.WEBAPP_URL or None})


async def cron(request):
    secret = os.environ.get("CRON_SECRET", "")
    if not secret or not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {secret}"):
        return PlainTextResponse("unauthorized", status_code=401)
    jobs = CRON_JOBS.get(request.path_params["name"])
    if not jobs:
        return PlainTextResponse("not found", status_code=404)
    ctx = SimpleNamespace(bot=_bot)  # job_* funksiyalari faqat context.bot ishlatadi
    for job in jobs:
        await job(ctx)
    return JSONResponse({"ok": True, "ran": [j.__name__ for j in jobs]})


_app = server.create_web_app(
    [
        Route("/api/webhook", webhook, methods=["POST"]),
        Route("/api/setup", setup),
        Route("/api/cron/{name}", cron),
    ]
)


async def app(scope, receive, send):
    if scope["type"] == "http":
        await _ensure_ready()
    await _app(scope, receive, send)

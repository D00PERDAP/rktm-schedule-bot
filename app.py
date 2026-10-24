
import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
WEBAPP_DIR = BASE_DIR / "webapp"
INDEX_FILE = WEBAPP_DIR / "index.html"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    os.getenv("RENDER_EXTERNAL_URL", "https://rktm-schedule-bot.onrender.com")
).rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

SCHEDULE_URLS = {
    "raspisan": "https://rktm.info/wp-content/uploads/HTML/raspisan.html",
    "Praspisan": "https://rktm.info/wp-content/uploads/HTML/Praspisan.html",
    "Araspisan": "https://rktm.info/wp-content/uploads/HTML/Araspisan.html",
}

bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()


@dp.message(CommandStart())
async def start_command(message: Message):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📅 Открыть расписание",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                )
            ]
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

    await message.answer(
        "Привет! Это расписание РКТМ.\n\n"
        "Нажми кнопку ниже, чтобы открыть расписание.",
        reply_markup=keyboard,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    if bot:
        try:
            await bot.set_webhook(
                url=f"{WEBAPP_URL}/telegram/webhook",
                secret_token=WEBHOOK_SECRET or None,
                allowed_updates=dp.resolve_used_update_types(),
                drop_pending_updates=False,
            )
            logger.info("Telegram webhook configured")
        except Exception:
            logger.exception("Failed to configure Telegram webhook")

    yield

    if bot:
        try:
            await bot.session.close()
        except Exception:
            logger.exception("Failed to close Telegram bot session")


app = FastAPI(
    title="РКТМ — расписание",
    lifespan=lifespan,
)


@app.get("/", response_class=HTMLResponse)
async def index():
    if not INDEX_FILE.is_file():
        raise HTTPException(
            status_code=500,
            detail="Не найден файл webapp/index.html",
        )

    return FileResponse(INDEX_FILE, media_type="text/html")


@app.get("/health")
async def health():
    return {
        "ok": True,
        "bot_configured": bot is not None,
        "webapp_exists": INDEX_FILE.is_file(),
    }


@app.get("/api/schedule/{source}")
async def get_schedule(source: str):
    url = SCHEDULE_URLS.get(source)

    if not url:
        raise HTTPException(
            status_code=404,
            detail=f"Источник не найден: {source}",
        )

    try:
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; RKTM-Schedule/1.0)"
            },
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

        content_type = response.headers.get("content-type", "")

        if "text/html" not in content_type and "<html" not in response.text[:1000].lower():
            logger.warning(
                "Schedule source returned unexpected content type: %s",
                content_type,
            )

        return {"html": response.text}

    except httpx.HTTPStatusError as exc:
        logger.exception("Schedule source returned HTTP error")
        raise HTTPException(
            status_code=502,
            detail=f"Сайт расписания вернул HTTP {exc.response.status_code}",
        ) from exc

    except httpx.HTTPError as exc:
        logger.exception("Could not fetch schedule")
        raise HTTPException(
            status_code=502,
            detail="Не удалось получить расписание с сайта РКТМ",
        ) from exc


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if not bot:
        raise HTTPException(
            status_code=503,
            detail="BOT_TOKEN не настроен в Render Environment",
        )

    if WEBHOOK_SECRET:
        received_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token", ""
        )
        if received_secret != WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Invalid webhook secret")

    try:
        data = await request.json()
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
        return {"ok": True}

    except Exception as exc:
        logger.exception("Error processing Telegram update")
        raise HTTPException(
            status_code=500,
            detail="Ошибка обработки обновления Telegram",
        ) from exc


@app.get("/api")
async def api_info():
    return {
        "service": "RKTM Schedule",
        "sources": list(SCHEDULE_URLS.keys()),
        "schedule_endpoint": "/api/schedule/{source}",
    }

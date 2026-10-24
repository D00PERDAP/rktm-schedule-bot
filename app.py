
import os
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
    Update,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBAPP_URL = os.getenv(
    "WEBAPP_URL", "https://rktm-schedule-bot.onrender.com"
).strip().rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
REPLACEMENTS_URL = os.getenv("REPLACEMENTS_URL", "").strip()

ALLOWED_HOST = "rktm.info"

SOURCES = {
    "main": (
        "Расписание",
        "https://rktm.info/wp-content/uploads/HTML/raspisan.html",
    ),
    "p": (
        "Расписание П",
        "https://rktm.info/wp-content/uploads/HTML/Praspisan.html",
    ),
    "a": (
        "Расписание А",
        "https://rktm.info/wp-content/uploads/HTML/Araspisan.html",
    ),
}

bot = Bot(BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(
                text="📅 Открыть расписание",
                web_app=WebAppInfo(url=WEBAPP_URL),
            )
        ]],
        resize_keyboard=True,
    )
    await message.answer(
        "📚 Расписание РКТМ\n\n"
        "Нажми кнопку ниже, чтобы открыть мини-приложение "
        "и посмотреть расписание колледжа.",
        reply_markup=keyboard,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    if bot:
        options = {
            "url": f"{WEBAPP_URL}/telegram/webhook",
            "drop_pending_updates": False,
        }
        if WEBHOOK_SECRET:
            options["secret_token"] = WEBHOOK_SECRET
        await bot.set_webhook(**options)

    yield

    if bot:
        await bot.session.close()


app = FastAPI(title="РКТМ — расписание", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/")
async def index():
    return FileResponse("webapp/index.html")


@app.get("/health")
async def health():
    return {"ok": True, "bot_configured": bot is not None}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if not bot:
        raise HTTPException(
            status_code=503,
            detail="Добавьте BOT_TOKEN в Environment на Render",
        )

    if WEBHOOK_SECRET:
        supplied = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token", ""
        )
        if supplied != WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Invalid secret")

    try:
        data = await request.json()
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Telegram update")

    return {"ok": True}


async def fetch(url: str) -> str:
    if urlparse(url).hostname != ALLOWED_HOST:
        raise HTTPException(status_code=400, detail="Недопустимый источник")

    try:
        async with httpx.AsyncClient(
            timeout=25, follow_redirects=True
        ) as client:
            response = await client.get(
                url,
                headers={"User-Agent": "RKTM-Schedule/1.0"},
            )
            response.raise_for_status()
            response.encoding = response.encoding or "utf-8"
            return response.text
    except httpx.HTTPError:
        raise HTTPException(
            status_code=502,
            detail="Не удалось загрузить расписание с сайта РКТМ",
        )


@app.get("/api/schedule/{source}")
async def schedule(source: str):
    if source not in SOURCES:
        raise HTTPException(status_code=404, detail="Источник не найден")

    label, url = SOURCES[source]
    html = await fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    tables = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [
                cell.get_text(" ", strip=True)
                for cell in tr.find_all(["th", "td"])
            ]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)

    return {
        "label": label,
        "source": url,
        "updated_at": datetime.now().isoformat(timespec="minutes"),
        "tables": tables,
        "text": soup.get_text("\n", strip=True),
    }


@app.get("/api/replacements")
async def replacements():
    url = REPLACEMENTS_URL

    if not url:
        return {
            "available": False,
            "message": "Ссылка на PDF замен не настроена.",
        }

    if (
        urlparse(url).hostname != ALLOWED_HOST
        or not url.lower().split("?")[0].endswith(".pdf")
    ):
        raise HTTPException(
            status_code=400,
            detail="Некорректная ссылка на PDF",
        )

    return {
        "available": True,
        "url": url,
        "updated_at": datetime.now().isoformat(timespec="minutes"),
    }


@app.get("/api/replacements/open")
async def open_replacements():
    url = REPLACEMENTS_URL

    if not url:
        raise HTTPException(
            status_code=404,
            detail="Ссылка на замены не настроена",
        )

    if (
        urlparse(url).hostname != ALLOWED_HOST
        or not url.lower().split("?")[0].endswith(".pdf")
    ):
        raise HTTPException(status_code=400, detail="Некорректная ссылка")

    return RedirectResponse(url)


import os
import logging
import re
from html.parser import HTMLParser
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urljoin

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
INDEX_FILE = BASE_DIR / "webapp" / "index.html"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    "https://rktm-schedule-bot.onrender.com"
).rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

SCHEDULE_BASE = "https://rktm.info/wp-content/uploads/HTML/"

SCHEDULE_URLS = {
    "raspisan": urljoin(SCHEDULE_BASE, "raspisan.html"),
    "Praspisan": urljoin(SCHEDULE_BASE, "Praspisan.html"),
    "Araspisan": urljoin(SCHEDULE_BASE, "Araspisan.html"),
}

bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()


class GroupParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.groups = []
        self.href = None
        self.text_parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self.href = dict(attrs).get("href")
            self.text_parts = []

    def handle_data(self, data):
        if self.href is not None:
            self.text_parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() != "a" or self.href is None:
            return

        name = " ".join("".join(self.text_parts).split())
        href = self.href.strip()

        if (
            name
            and href
            and re.fullmatch(r"[^/\\\\?#]+\\.html?", href, re.I)
            and name.lower() not in ("здесь", "скачать")
        ):
            item = {"name": name, "file": href}
            if item not in self.groups:
                self.groups.append(item)

        self.href = None
        self.text_parts = []


@dp.message(CommandStart())
async def start_command(message: Message):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(
                text="📅 Открыть расписание",
                web_app=WebAppInfo(url=WEBAPP_URL),
            )
        ]],
        resize_keyboard=True,
        is_persistent=True,
    )
    await message.answer(
        "Привет! Это расписание РКТМ.\n"
        "Нажми кнопку ниже, чтобы открыть его.",
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
            )
            logger.info("Telegram webhook configured")
        except Exception:
            logger.exception("Failed to configure Telegram webhook")
    yield
    if bot:
        await bot.session.close()


app = FastAPI(title="РКТМ — расписание", lifespan=lifespan)


async def fetch_html(url: str) -> str:
    try:
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 RKTM-Schedule/1.0"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            response.encoding = response.encoding or "utf-8"
            return response.text
    except httpx.HTTPStatusError as exc:
        logger.exception("Schedule source HTTP error")
        raise HTTPException(
            status_code=502,
            detail=f"Сайт РКТМ вернул HTTP {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        logger.exception("Schedule source unavailable")
        raise HTTPException(
            status_code=502,
            detail="Не удалось получить данные с сайта РКТМ",
        ) from exc


@app.get("/", response_class=HTMLResponse)
async def index():
    if not INDEX_FILE.is_file():
        raise HTTPException(500, "Не найден webapp/index.html")
    return FileResponse(INDEX_FILE, media_type="text/html")


@app.get("/health")
async def health():
    return {
        "ok": True,
        "bot_configured": bot is not None,
        "webapp_exists": INDEX_FILE.is_file(),
    }


@app.get("/api/groups/{source}")
async def get_groups(source: str):
    if source not in SCHEDULE_URLS:
        raise HTTPException(404, "Неизвестный раздел расписания")

    html = await fetch_html(SCHEDULE_URLS[source])
    parser = GroupParser()
    parser.feed(html)

    return {
        "source": source,
        "base_url": SCHEDULE_BASE,
        "groups": parser.groups,
    }


@app.get("/api/group/{source}/{filename}")
async def get_group_schedule(source: str, filename: str):
    if source not in SCHEDULE_URLS:
        raise HTTPException(404, "Неизвестный раздел расписания")

    # Разрешаем только имя HTML-файла, без папок и адресов извне.
    if not re.fullmatch(r"[^/\\\\?#]+\\.html?", filename, re.I):
        raise HTTPException(400, "Некорректный файл группы")

    url = urljoin(SCHEDULE_BASE, filename)
    html = await fetch_html(url)

    return {
        "name": filename,
        "html": html,
        "base_url": SCHEDULE_BASE,
    }


@app.get("/api/schedule/{source}")
async def get_schedule(source: str):
    if source not in SCHEDULE_URLS:
        raise HTTPException(404, "Неизвестный раздел расписания")
    return {
        "html": await fetch_html(SCHEDULE_URLS[source])
    }


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if not bot:
        raise HTTPException(503, "BOT_TOKEN не настроен")

    if WEBHOOK_SECRET:
        secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token", ""
        )
        if secret != WEBHOOK_SECRET:
            raise HTTPException(403, "Invalid webhook secret")

    try:
        data = await request.json()
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
        return {"ok": True}
    except Exception as exc:
        logger.exception("Telegram update error")
        raise HTTPException(500, "Ошибка обработки Telegram") from exc


@app.get("/api")
async def api_info():
    return {
        "service": "RKTM Schedule",
        "sources": list(SCHEDULE_URLS.keys()),
        "groups_endpoint": "/api/groups/raspisan",
    }

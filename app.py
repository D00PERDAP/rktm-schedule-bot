
import os
import re
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from html.parser import HTMLParser
from urllib.parse import urljoin, unquote

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
    "https://rktm-schedule-bot.onrender.com",
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
        self.current_href = None
        self.current_text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self.current_href = dict(attrs).get("href")
            self.current_text = []

    def handle_data(self, data):
        if self.current_href is not None:
            self.current_text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() != "a" or self.current_href is None:
            return

        name = " ".join("".join(self.current_text).split())
        href = unquote(self.current_href.strip())

        if (
            name
            and re.fullmatch(r"[^/\\?#]+\.html?", href, re.I)
            and not name.lower() in {"здесь", "скачать"}
        ):
            item = {"name": name, "file": href}
            if item not in self.groups:
                self.groups.append(item)

        self.current_href = None
        self.current_text = []


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
            )
            logger.info("Telegram webhook configured")
        except Exception:
            logger.exception("Не удалось настроить Telegram webhook")

    yield

    if bot:
        await bot.session.close()


app = FastAPI(title="РКТМ — расписание", lifespan=lifespan)


async def fetch_html(url: str) -> str:
    try:
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            content = response.content

            # Сначала определяем кодировку из HTTP-заголовка.
            content_type = response.headers.get("content-type", "")
            match = re.search(
                r"charset\s*=\s*[\"']?([^;\"'\s]+)",
                content_type,
                re.I,
            )
            encoding = match.group(1).strip() if match else ""

            # Затем учитываем META старых HTML-файлов.
            if not encoding:
                head = content[:4096].decode("ascii", errors="ignore")
                meta = re.search(
                    rb"charset\s*=\s*[\"']?([\w-]+)",
                    content[:4096],
                    re.I,
                )
                if meta:
                    encoding = meta.group(1).decode("ascii", errors="ignore")

            # На сайте встречаются старые страницы в Windows-1251.
            candidates = []
            if encoding:
                candidates.append(encoding)
            candidates.extend(["utf-8", "cp1251", "windows-1251"])

            for candidate in dict.fromkeys(candidates):
                try:
                    decoded = content.decode(candidate)
                    if "\ufffd" not in decoded:
                        return decoded
                except (LookupError, UnicodeDecodeError):
                    continue

            return content.decode("cp1251", errors="replace")

    except httpx.HTTPStatusError as exc:
        logger.exception("Ошибка HTTP источника расписания")
        raise HTTPException(
            status_code=502,
            detail=f"Сайт РКТМ вернул HTTP {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        logger.exception("Не удалось получить расписание")
        raise HTTPException(
            status_code=502,
            detail="Не удалось получить данные с сайта РКТМ",
        ) from exc


def get_source(source: str) -> str:
    if source not in SCHEDULE_URLS:
        raise HTTPException(status_code=404, detail="Раздел не найден")
    return source


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


@app.get("/api/groups/{source}")
async def get_groups(source: str):
    get_source(source)
    html = await fetch_html(SCHEDULE_URLS[source])

    parser = GroupParser()
    parser.feed(html)

    return {
        "source": source,
        "groups": parser.groups,
    }


@app.get("/api/group/{source}/{filename}")
async def get_group_schedule(source: str, filename: str):
    get_source(source)
    filename = unquote(filename)

    # Только имя файла, без перехода к произвольным адресам.
    if (
        not re.fullmatch(r"[^/\\?#]+\.html?", filename, re.I)
        or ".." in filename
    ):
        raise HTTPException(status_code=400, detail="Некорректное имя файла")

    url = urljoin(SCHEDULE_BASE, filename)
    html = await fetch_html(url)

    return {
        "name": filename,
        "html": html,
        "base_url": SCHEDULE_BASE,
    }


@app.get("/api/schedule/{source}")
async def get_schedule(source: str):
    get_source(source)
    return {"html": await fetch_html(SCHEDULE_URLS[source])}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if not bot:
        raise HTTPException(
            status_code=503,
            detail="BOT_TOKEN не настроен в Render",
        )

    if WEBHOOK_SECRET:
        received = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token", ""
        )
        if received != WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Invalid webhook secret")

    try:
        data = await request.json()
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
        return {"ok": True}
    except Exception as exc:
        logger.exception("Ошибка обработки обновления Telegram")
        raise HTTPException(
            status_code=500,
            detail="Ошибка обработки обновления Telegram",
        ) from exc


@app.get("/api")
async def api_info():
    return {
        "service": "RKTM Schedule",
        "sources": list(SCHEDULE_URLS.keys()),
    }

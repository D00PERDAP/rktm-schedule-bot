import os
from datetime import datetime
from urllib.parse import urlparse
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()
app = FastAPI(title="РКТМ — расписание")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=False,
    allow_methods=["GET"], allow_headers=["*"]
)

SOURCES = {
    "main": ("Расписание", "https://rktm.info/wp-content/uploads/HTML/raspisan.html"),
    "p": ("Расписание П", "https://rktm.info/wp-content/uploads/HTML/Praspisan.html"),
    "a": ("Расписание А", "https://rktm.info/wp-content/uploads/HTML/Araspisan.html"),
}
REPLACEMENTS_URL = os.getenv("REPLACEMENTS_URL", "").strip()
ALLOWED_HOST = "rktm.info"

async def fetch(url: str) -> str:
    if urlparse(url).hostname != ALLOWED_HOST:
        raise HTTPException(status_code=400, detail="Разрешён только домен rktm.info")
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "RKTM-Schedule/1.0"})
            response.raise_for_status()
            response.encoding = response.encoding or "utf-8"
            return response.text
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось загрузить источник: {exc}")

@app.get("/")
async def index():
    return FileResponse("webapp/index.html")

@app.get("/api/schedule/{source}")
async def schedule(source: str):
    if source not in SOURCES:
        raise HTTPException(status_code=404, detail="Неизвестный источник расписания")
    label, url = SOURCES[source]
    html = await fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    # Удаляем служебные элементы, но сохраняем таблицы и заголовки для читаемости.
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    tables = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)
    text = soup.get_text("\n", strip=True)
    return {"label": label, "source": url, "updated_at": datetime.now().isoformat(timespec="minutes"),
            "tables": tables, "text": text}

def get_replacements_url() -> str:
    # Точка расширения: можно заменить на парсинг страницы сайта с актуальной ссылкой.
    # Сейчас используем явно заданный URL, не пытаясь угадать имя ежедневного PDF.
    return REPLACEMENTS_URL

@app.get("/api/replacements")
async def replacements():
    url = get_replacements_url()
    if not url:
        return {"available": False, "message": "Ссылка на PDF замен ещё не настроена."}
    if urlparse(url).hostname != ALLOWED_HOST or not url.lower().split("?")[0].endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Некорректная ссылка на PDF замен")
    return {"available": True, "url": url, "updated_at": datetime.now().isoformat(timespec="minutes")}

@app.get("/api/replacements/open")
async def open_replacements():
    url = get_replacements_url()
    if not url:
        raise HTTPException(status_code=404, detail="Ссылка на замены не настроена")
    return RedirectResponse(url)

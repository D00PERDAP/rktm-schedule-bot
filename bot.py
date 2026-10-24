import os
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()

if not TOKEN:
    raise RuntimeError("Заполните BOT_TOKEN в .env")
if not WEBAPP_URL.startswith("https://"):
    raise RuntimeError("WEBAPP_URL должен быть публичным HTTPS-адресом")

bot = Bot(TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def start(message: Message):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📅 Открыть расписание", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True
    )
    await message.answer(
        "Расписание РКТМ\n\nОткройте мини-приложение, выберите нужный раздел и найдите расписание своей группы.",
        reply_markup=keyboard
    )

@dp.message(F.text == "📅 Открыть расписание")
async def open_schedule(message: Message):
    await message.answer("Нажмите кнопку «Открыть расписание» в меню ниже.")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

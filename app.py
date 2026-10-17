import asyncio
import logging
import os
import threading
from flask import Flask
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# Настройки
TOKEN = os.environ.get("BOT_TOKEN")
app = Flask(__name__)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- БЛОК TELEGRAM БОТА ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🚗 Привет! Я бот для поиска автозапчастей.\n\n"
        "Скоро здесь можно будет искать детали по OEM-номеру и покупать их у проверенных разборок!"
    )

async def start_bot():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

# --- БЛОК FLASK (чтобы Render не засыпал) ---
@app.route('/')
def home():
    return "AutoBot is running!"

if __name__ == "__main__":
    # Запускаем бота в отдельном потоке
    threading.Thread(target=lambda: asyncio.run(start_bot()), daemon=True).start()
    # Запускаем Flask (веб-сервер)
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

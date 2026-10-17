import asyncio
import logging
import os
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN не задан!")

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🚗 Привет! Я бот для поиска автозапчастей.\n\n"
        "Скоро здесь можно будет искать детали по OEM-номеру и покупать их у проверенных разборок!"
    )

async def health_check(request):
    return web.Response(text="AutoBot is running!")

async def main():
    logging.basicConfig(level=logging.INFO)
    # Удаляем вебхук и сбрасываем старые сообщения
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запускаем фоновый polling
    polling_task = asyncio.create_task(dp.start_polling(bot))
    
    # Запускаем веб-сервер для Render
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 5000)))
    await site.start()
    
    logging.info("Бот запущен и слушает порт!")
    await polling_task

if __name__ == "__main__":
    asyncio.run(main())

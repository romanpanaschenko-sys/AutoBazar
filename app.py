import asyncio
import logging
import os
import sqlite3
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")

if not TOKEN or not CHANNEL_ID:
    raise ValueError("Заданы не все переменные окружения!")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('auto_bazar.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            oem TEXT,
            price TEXT,
            photo_id TEXT
        )
    ''')
    conn.commit()
    conn.close()

# --- СОСТОЯНИЯ ---
class AddAdState(StatesGroup):
    waiting_for_photo = State()
    waiting_for_name = State()
    waiting_for_oem = State()
    waiting_for_price = State()

class SearchState(StatesGroup):
    waiting_for_oem = State()

# --- ГЛАВНОЕ МЕНЮ ---
def get_main_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="📦 Разместить объявление", callback_data="add_ad")
    builder.button(text="🔍 Поиск по OEM", callback_data="search_oem")
    builder.adjust(1)
    return builder.as_markup()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🚗 Привет! Добро пожаловать в авто-барахолку.\n"
        "Выбери действие в меню ниже:",
        reply_markup=get_main_menu()
    )

# --- ДОБАВЛЕНИЕ (С ФОТО) ---
@dp.callback_query(F.data == "add_ad")
async def start_add_ad(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📸 Шаг 1: Отправьте фотографию запчасти:")
    await state.set_state(AddAdState.waiting_for_photo)
    await callback.answer()

@dp.message(AddAdState.waiting_for_photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    # Берем фото в максимальном разрешении
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)
    
    await message.answer("📝 Шаг 2: Введите название детали (например: Стартер Passat B6):")
    await state.set_state(AddAdState.waiting_for_name)

# Если пользователь отправил текст вместо фото
@dp.message(AddAdState.waiting_for_photo)
async def process_photo_error(message: types.Message):
    await message.answer("⚠️ Пожалуйста, отправьте именно фотографию детали (нажмите на скрепку).")

@dp.message(AddAdState.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("🔍 Шаг 3: Введите OEM-номер детали (или напишите 'нет'):")
    await state.set_state(AddAdState.waiting_for_oem)

@dp.message(AddAdState.waiting_for_oem)
async def process_oem(message: types.Message, state: FSMContext):
    await state.update_data(oem=message.text)
    await message.answer("💰 Шаг 4: Введите цену в грн (только цифры):")
    await state.set_state(AddAdState.waiting_for_price)

@dp.message(AddAdState.waiting_for_price)
async def process_price(message: types.Message, state: FSMContext):
    data = await state.get_data()
    name = data['name']
    oem = data['oem']
    price = message.text
    photo_id = data['photo_id']
    
    # Сохраняем в БД
    conn = sqlite3.connect('auto_bazar.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO ads (user_id, name, oem, price, photo_id) VALUES (?, ?, ?, ?, ?)", 
                   (message.from_user.id, name, oem, price, photo_id))
    conn.commit()
    conn.close()
    
    # Публикуем в канал
    post_text = (
        f"⚙️ **{name}**\n\n"
        f"🔍 **OEM:** `{oem}`\n"
        f"💰 **Цена:** {price} грн\n\n"
        f"👤 Продавец: @{message.from_user.username if message.from_user.username else 'скрыт'}"
    )
    
    try:
        await bot.send_photo(chat_id=CHANNEL_ID, photo=photo_id, caption=post_text, parse_mode="Markdown")
        await message.answer(
            f"✅ Объявление успешно опубликовано в канале!",
            reply_markup=get_main_menu()
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка публикации в канал: {e}", reply_markup=get_main_menu())
        
    await state.clear()

# --- ПОИСК ---
@dp.callback_query(F.data == "search_oem")
async def start_search(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите OEM-номер для поиска:")
    await state.set_state(SearchState.waiting_for_oem)
    await callback.answer()

@dp.message(SearchState.waiting_for_oem)
async def process_search(message: types.Message, state: FSMContext):
    search_query = message.text.strip().upper()
    
    conn = sqlite3.connect('auto_bazar.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name, oem, price, photo_id FROM ads WHERE UPPER(REPLACE(REPLACE(oem, ' ', ''), '-', '')) LIKE ?", (f"%{search_query}%",))
    results = cursor.fetchall()
    conn.close()
    
    if results:
        await message.answer(f"Найдено {len(results)} объявлений:")
        for res in results:
            caption = f"⚙️ {res[0]}\n🔍 OEM: {res[1]}\n💰 Цена: {res[2]} грн"
            await message.answer_photo(photo=res[3], caption=caption)
    else:
        await message.answer("По этому номеру ничего не найдено. 😔")
        
    await message.answer("Вернуться в меню:", reply_markup=get_main_menu())
    await state.clear()

# --- ВЕБ-СЕРВЕР ---
async def health_check(request):
    return web.Response(text="AutoBot is running!")

async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    
    polling_task = asyncio.create_task(dp.start_polling(bot))
    
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 5000)))
    await site.start()
    
    logging.info("Бот с публикацией в канал запущен!")
    await polling_task

if __name__ == "__main__":
    asyncio.run(main())

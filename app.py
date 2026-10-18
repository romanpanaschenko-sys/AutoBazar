import asyncio
import logging
import os
import psycopg2
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not TOKEN or not CHANNEL_ID or not DATABASE_URL:
    raise ValueError("Заданы не все переменные окружения!")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- ПОДКЛЮЧЕНИЕ К POSTGRESQL ---
def get_db_connection():
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url)

def init_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Создаем таблицу со всеми нужными полями
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ads (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                category TEXT,
                name TEXT,
                oem TEXT,
                price TEXT,
                photo_id TEXT,
                message_id BIGINT
            )
        ''')
        conn.commit()
        
        # Пытаемся добавить колонку message_id, если таблица была создана ранее без неё
        try:
            cursor.execute("ALTER TABLE ads ADD COLUMN message_id BIGINT;")
            conn.commit()
            logging.info("Добавлен столбец message_id.")
        except psycopg2.Error:
            conn.rollback() # Если колонка уже существует, просто откатываем ошибку
            
        cursor.close()
        conn.close()
        logging.info("База данных инициализирована.")
    except Exception as e:
        logging.error(f"Ошибка инициализации БД: {e}")

# --- СОСТОЯНИЯ ---
class AddAdState(StatesGroup):
    waiting_for_photo = State()
    waiting_for_category = State()
    waiting_for_name = State()
    waiting_for_oem = State()
    waiting_for_price = State()

class SearchState(StatesGroup):
    waiting_for_oem = State()

# --- ХЭШТЕГИ ---
def generate_hashtags(category, name, oem):
    cat_tag = "#" + "".join(c for c in category if c.isalnum())
    first_word = name.split()[0] if name else "Деталь"
    name_tag = "#" + "".join(c for c in first_word if c.isalnum())
    tags = f"{cat_tag} {name_tag} #АвтоБарахолка"
    if oem.lower() != "нет" and oem.strip():
        oem_tag = "#" + "".join(c for c in oem if c.isalnum())
        tags += f" {oem_tag}"
    return tags

# --- КЛАВИАТУРЫ ---
def get_main_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="📦 Разместить объявление", callback_data="add_ad")
    builder.button(text="🔍 Поиск по OEM", callback_data="search_oem")
    builder.button(text="👤 Мои объявления", callback_data="my_ads")
    builder.adjust(1)
    return builder.as_markup()

def get_category_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="⚙️ Двигатель", callback_data="cat_Двигатель")
    builder.button(text="🛞 Подвеска", callback_data="cat_Подвеска")
    builder.button(text="🚪 Кузов", callback_data="cat_Кузов")
    builder.button(text="🔌 Электрика", callback_data="cat_Электрика")
    builder.button(text="📦 Другое", callback_data="cat_Другое")
    builder.adjust(2)
    return builder.as_markup()

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🚗 Привет! Добро пожаловать в авто-барахолку.\n"
        "Выбери действие в меню ниже:",
        reply_markup=get_main_menu()
    )

# --- ДОБАВЛЕНИЕ ---
@dp.callback_query(F.data == "add_ad")
async def start_add_ad(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📸 Шаг 1: Отправьте фотографию запчасти:")
    await state.set_state(AddAdState.waiting_for_photo)
    await callback.answer()

@dp.message(AddAdState.waiting_for_photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)
    
    await message.answer("📂 Шаг 2: Выберите категорию запчасти:", reply_markup=get_category_menu())
    await state.set_state(AddAdState.waiting_for_category)

@dp.message(AddAdState.waiting_for_photo)
async def process_photo_error(message: types.Message):
    await message.answer("⚠️ Пожалуйста, отправьте именно фотографию (нажмите на скрепку).")

@dp.callback_query(AddAdState.waiting_for_category, F.data.startswith("cat_"))
async def process_category(callback: types.CallbackQuery, state: FSMContext):
    category = callback.data.split("_")[1]
    await state.update_data(category=category)
    
    await callback.message.edit_text(f"Категория выбрана: {category}")
    await callback.message.answer("📝 Шаг 3: Введите название детали:")
    await state.set_state(AddAdState.waiting_for_name)
    await callback.answer()

@dp.message(AddAdState.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("🔍 Шаг 4: Введите OEM-номер (или напишите 'нет'):")
    await state.set_state(AddAdState.waiting_for_oem)

@dp.message(AddAdState.waiting_for_oem)
async def process_oem(message: types.Message, state: FSMContext):
    await state.update_data(oem=message.text)
    await message.answer("💰 Шаг 5: Введите цену в грн (только цифры):")
    await state.set_state(AddAdState.waiting_for_price)

@dp.message(AddAdState.waiting_for_price)
async def process_price(message: types.Message, state: FSMContext):
    data = await state.get_data()
    name = data['name']
    oem = data['oem']
    category = data['category']
    price = message.text
    photo_id = data['photo_id']
    user_id = message.from_user.id
    
    tags = generate_hashtags(category, name, oem)
    post_text = (
        f"📂 **Категория:** {category}\n"
        f"⚙️ **{name}**\n\n"
        f"🔍 **OEM:** `{oem}`\n"
        f"💰 **Цена:** {price} грн\n\n"
        f"{tags}"
    )
    
    contact_btn = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Написать продавцу", url=f"tg://user?id={user_id}")]
    ])
    
    try:
        # Публикуем и СОХРАНЯЕМ ID сообщения в канале
        sent_msg = await bot.send_photo(
            chat_id=CHANNEL_ID, 
            photo=photo_id, 
            caption=post_text, 
            parse_mode="Markdown", 
            reply_markup=contact_btn
        )
        message_id = sent_msg.message_id
        
        # Сохраняем в БД вместе с message_id
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO ads (user_id, category, name, oem, price, photo_id, message_id) VALUES (%s, %s, %s, %s, %s, %s, %s)", 
            (user_id, category, name, oem, price, photo_id, message_id)
        )
        conn.commit()
        cursor.close()
        conn.close()
        
        await message.answer("✅ Объявление опубликовано в канале!", reply_markup=get_main_menu())
    except Exception as e:
        await message.answer(f"❌ Ошибка публикации: {e}", reply_markup=get_main_menu())
        
    await state.clear()

# --- МОИ ОБЪЯВЛЕНИЯ ---
@dp.callback_query(F.data == "my_ads")
async def show_my_ads(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price FROM ads WHERE user_id = %s", (user_id,))
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        
        if not results:
            await callback.message.answer("У вас пока нет объявлений.")
            return

        await callback.message.answer("Ваши объявления (нажмите 🗑, чтобы удалить):")
        for res in results:
            ad_id, name, price = res
            # Создаем кнопку удаления для каждого объявления
            del_btn = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{ad_id}")]
            ])
            await callback.message.answer(f"⚙️ {name}\n💰 {price} грн", reply_markup=del_btn)
            
    except Exception as e:
        await callback.message.answer(f"Ошибка: {e}")
    await callback.answer()

# --- УДАЛЕНИЕ ---
@dp.callback_query(F.data.startswith("del_"))
async def delete_ad(callback: types.CallbackQuery):
    ad_id = callback.data.split("_")[1]
    user_id = callback.from_user.id
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Получаем message_id и проверяем, что это объявление принадлежит этому юзеру
        cursor.execute("SELECT message_id FROM ads WHERE id = %s AND user_id = %s", (ad_id, user_id))
        result = cursor.fetchone()
        
        if result:
            message_id = result[0]
            # Пытаемся удалить сообщение из канала
            try:
                await bot.delete_message(chat_id=CHANNEL_ID, message_id=message_id)
            except Exception as e:
                logging.warning(f"Не удалось удалить сообщение из канала: {e}")
            
            # Удаляем из базы данных
            cursor.execute("DELETE FROM ads WHERE id = %s", (ad_id,))
            conn.commit()
            
            await callback.message.edit_text("✅ Объявление удалено из базы и канала.")
        else:
            await callback.message.edit_text("⚠️ Объявление не найдено или не принадлежит вам.")
            
        cursor.close()
        conn.close()
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка удаления: {e}")
    await callback.answer()

# --- ПОИСК ---
@dp.callback_query(F.data == "search_oem")
async def start_search(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите OEM-номер для поиска:")
    await state.set_state(SearchState.waiting_for_oem)
    await callback.answer()

@dp.message(SearchState.waiting_for_oem)
async def process_search(message: types.Message, state: FSMContext):
    search_query = message.text.strip().upper()
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, oem, price, photo_id, category, user_id FROM ads WHERE UPPER(REPLACE(REPLACE(oem, ' ', ''), '-', '')) LIKE %s", 
            (f"%{search_query}%",)
        )
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        
        if results:
            await message.answer(f"Найдено {len(results)} объявлений:")
            for res in results:
                contact_btn = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💬 Написать продавцу", url=f"tg://user?id={res[5]}")]
                ])
                caption = f"📂 {res[4]}\n⚙️ {res[0]}\n🔍 OEM: {res[1]}\n💰 Цена: {res[2]} грн"
                await message.answer_photo(photo=res[3], caption=caption, reply_markup=contact_btn)
        else:
            await message.answer("По этому номеру ничего не найдено. 😔")
            
    except Exception as e:
        await message.answer(f"❌ Ошибка поиска: {e}")
        
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
    
    logging.info("Бот с удалением и My Ads запущен!")
    await polling_task

if __name__ == "__main__":
    asyncio.run(main())

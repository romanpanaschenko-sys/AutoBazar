import asyncio
import logging
import os
import psycopg2
from datetime import datetime, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# --- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---
TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")
GROUP_ID = os.environ.get("GROUP_ID")
ADMIN_ID = 405871469 

if not TOKEN or not CHANNEL_ID or not DATABASE_URL:
    raise ValueError("Заданы не все переменные окружения!")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ ---
def get_db_connection():
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url)

def init_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ads (
                id SERIAL PRIMARY KEY, user_id BIGINT, category TEXT, name TEXT, oem TEXT, 
                price TEXT, photo_id TEXT, message_id BIGINT, car_info TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS requests (
                id SERIAL PRIMARY KEY, user_id BIGINT, description TEXT, oem TEXT, 
                budget TEXT, message_id BIGINT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        try:
            cursor.execute("ALTER TABLE ads ADD COLUMN IF NOT EXISTS car_info TEXT;")
            cursor.execute("ALTER TABLE ads ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")
            cursor.execute("ALTER TABLE requests ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")
        except Exception:
            conn.rollback()
        conn.commit(); cursor.close(); conn.close()
    except Exception as e:
        logging.error(f"Ошибка БД: {e}")

# --- АВТООЧИСТКА ---
async def auto_clean_loop():
    while True:
        try:
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("SELECT id, message_id FROM ads WHERE created_at < NOW() - INTERVAL '30 days'")
            for ad_id, msg_id in cursor.fetchall():
                try: await bot.delete_message(chat_id=CHANNEL_ID, message_id=msg_id)
                except: pass
                cursor.execute("DELETE FROM ads WHERE id = %s", (ad_id,))
            cursor.execute("SELECT id, message_id FROM requests WHERE created_at < NOW() - INTERVAL '30 days'")
            for req_id, msg_id in cursor.fetchall():
                try: await bot.delete_message(chat_id=CHANNEL_ID, message_id=msg_id)
                except: pass
                cursor.execute("DELETE FROM requests WHERE id = %s", (req_id,))
            conn.commit(); cursor.close(); conn.close()
        except Exception as e: logging.error(f"Ошибка автоочистки: {e}")
        await asyncio.sleep(3600)

# --- СОСТОЯНИЯ ---
class AddAdState(StatesGroup):
    waiting_for_photo = State()
    waiting_for_car_info = State()
    waiting_for_category = State()
    waiting_for_name = State()
    waiting_for_oem = State()
    waiting_for_price = State()

class WantedAdState(StatesGroup):
    waiting_for_desc = State()
    waiting_for_oem = State()
    waiting_for_budget = State()

class SearchState(StatesGroup):
    waiting_for_oem = State()

# --- КЛАВИАТУРЫ ---
def get_main_menu():
    b = InlineKeyboardBuilder()
    b.button(text="📦 Продать запчасть", callback_data="add_ad")
    b.button(text="🔎 Куплю запчасть (Запрос)", callback_data="wanted_ad")
    b.button(text="🔍 Поиск по OEM", callback_data="search_oem")
    b.button(text="👤 Мои объявления", callback_data="my_ads")
    b.adjust(1)
    return b.as_markup()

def get_category_menu():
    b = InlineKeyboardBuilder()
    b.button(text="⚙️ Двигатель", callback_data="cat_Двигатель")
    b.button(text="🛞 Подвеска", callback_data="cat_Подвеска")
    b.button(text="🚪 Кузов", callback_data="cat_Кузов")
    b.button(text="🔌 Электрика", callback_data="cat_Электрика")
    b.button(text="📦 Другое", callback_data="cat_Другое")
    b.adjust(2)
    return b.as_markup()

def get_admin_menu():
    b = InlineKeyboardBuilder()
    b.button(text="📊 Статистика", callback_data="admin_stats")
    b.button(text="🗑 Удалить последнее объявление", callback_data="admin_del_ad")
    b.button(text="🗑 Удалить последний запрос", callback_data="admin_del_req")
    b.adjust(1)
    return b.as_markup()

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🚗 Привет! Добро пожаловать в авто-барахолку.\nВыбери действие:", reply_markup=get_main_menu())

# ==================== ОПЛАТА И VIP ====================
@dp.callback_query(F.data.startswith("pin_"))
async def pin_ad_invoice(callback: types.CallbackQuery):
    ad_id = callback.data.split("_")[1]
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT message_id FROM ads WHERE id = %s AND user_id = %s", (ad_id, callback.from_user.id))
    result = cursor.fetchone(); cursor.close(); conn.close()
    if result:
        await bot.send_invoice(
            chat_id=callback.from_user.id, title="Закрепление", description="Закреп на 24 часа.",
            payload=f"pin_{ad_id}_{result[0]}", provider_token="", currency="XTR",
            prices=[LabeledPrice(label="Закрепление", amount=50)],
        )
    await callback.answer()

@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    _, ad_id, msg_id = message.successful_payment.invoice_payload.split("_")
    try:
        await bot.pin_chat_message(chat_id=CHANNEL_ID, message_id=int(msg_id))
        await message.answer("✅ Оплата прошла! Объявление закреплено.")
    except Exception as e: await message.answer(f"❌ Ошибка: {e}")

# ==================== АДМИН ====================
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("👑 Панель администратора:", reply_markup=get_admin_menu())

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM ads"); ads_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM requests"); req_count = cursor.fetchone()[0]
    cursor.close(); conn.close()
    await callback.message.edit_text(f"📊 **Статистика:**\n\n📦 Объявлений: {ads_count}\n🔎 Запросов: {req_count}", parse_mode="Markdown", reply_markup=get_admin_menu())
    await callback.answer()

@dp.callback_query(F.data == "admin_del_ad")
async def admin_del_ad(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, message_id FROM ads ORDER BY id DESC LIMIT 1"); result = cursor.fetchone()
    if result:
        try: await bot.delete_message(chat_id=CHANNEL_ID, message_id=result[1])
        except: pass
        cursor.execute("DELETE FROM ads WHERE id = %s", (result[0],)); conn.commit()
    cursor.close(); conn.close()
    await callback.message.edit_text("✅ Удалено.", reply_markup=get_admin_menu())
    await callback.answer()

@dp.callback_query(F.data == "admin_del_req")
async def admin_del_req(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, message_id FROM requests ORDER BY id DESC LIMIT 1"); result = cursor.fetchone()
    if result:
        try: await bot.delete_message(chat_id=CHANNEL_ID, message_id=result[1])
        except: pass
        cursor.execute("DELETE FROM requests WHERE id = %s", (result[0],)); conn.commit()
    cursor.close(); conn.close()
    await callback.message.edit_text("✅ Удалено.", reply_markup=get_admin_menu())
    await callback.answer()

# ==================== ДОБАВЛЕНИЕ ====================
@dp.callback_query(F.data == "add_ad")
async def start_add_ad(callback: types.CallbackQuery, state: FSMContext):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM ads WHERE user_id = %s", (callback.from_user.id,)); count = cursor.fetchone()[0]
    cursor.close(); conn.close()
    if count >= 15:
        await callback.message.answer("⚠️ У вас 15 активных объявлений. Удалите старые.")
    else:
        await callback.message.answer("📸 Шаг 1: Отправьте фотографию:")
        await state.set_state(AddAdState.waiting_for_photo)
    await callback.answer()

@dp.message(AddAdState.waiting_for_photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if 'photo_id' in data: return # Игнорируем альбомы
    await state.update_data(photo_id=message.photo[-1].file_id)
    await message.answer("🚘 Шаг 2: Для какого авто? (BMW X5 E43 2009):")
    await state.set_state(AddAdState.waiting_for_car_info)

@dp.message(AddAdState.waiting_for_photo)
async def process_photo_error(message: types.Message):
    await message.answer("⚠️ Отправьте именно фотографию.")

@dp.message(AddAdState.waiting_for_car_info, F.photo)
async def process_car_info_photo_error(message: types.Message):
    await message.answer("⚠️ Фото уже принято. Напишите текстом (BMW X5...).")

@dp.message(AddAdState.waiting_for_car_info)
async def process_car_info(message: types.Message, state: FSMContext):
    await state.update_data(car_info=message.text)
    await message.answer("📂 Шаг 3: Выберите категорию:", reply_markup=get_category_menu())
    await state.set_state(AddAdState.waiting_for_category)

@dp.callback_query(AddAdState.waiting_for_category, F.data.startswith("cat_"))
async def process_category(callback: types.CallbackQuery, state: FSMContext):
    category = callback.data.split("_")[1]
    await state.update_data(category=category)
    await callback.message.edit_text(f"Категория: {category}")
    await callback.message.answer("📝 Шаг 4: Название детали:")
    await state.set_state(AddAdState.waiting_for_name)
    await callback.answer()

@dp.message(AddAdState.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("🔍 Шаг 5: OEM-номер (или 'нет'):")
    await state.set_state(AddAdState.waiting_for_oem)

@dp.message(AddAdState.waiting_for_oem)
async def process_oem(message: types.Message, state: FSMContext):
    await state.update_data(oem=message.text)
    await message.answer("💰 Шаг 6: Цена в грн:")
    await state.set_state(AddAdState.waiting_for_price)

@dp.message(AddAdState.waiting_for_price)
async def process_price(message: types.Message, state: FSMContext):
    data = await state.get_data()
    post_text = (f"🚘 **Авто:** {data['car_info']}\n📂 **Категория:** {data['category']}\n⚙️ **{data['name']}**\n\n"
                 f"🔍 **OEM:** `{data['oem']}`\n💰 **Цена:** {message.text} грн\n\n#АвтоБарахолка")
    
    contact_btn = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Написать продавцу", url=f"tg://user?id={message.from_user.id}")]])
    
    try:
        sent_msg = await bot.send_photo(chat_id=CHANNEL_ID, photo=data['photo_id'], caption=post_text, parse_mode="Markdown", reply_markup=contact_btn)
        if GROUP_ID:
            try: await bot.send_message(chat_id=GROUP_ID, text=f"🆕 **Новое объявление!**\n\n{post_text}", parse_mode="Markdown")
            except: pass

        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("INSERT INTO ads (user_id, category, name, oem, price, photo_id, message_id, car_info) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", 
                       (message.from_user.id, data['category'], data['name'], data['oem'], message.text, data['photo_id'], sent_msg.message_id, data['car_info']))
        conn.commit(); cursor.close(); conn.close()
        await message.answer("✅ Опубликовано!", reply_markup=get_main_menu())
    except Exception as e: await message.answer(f"❌ Ошибка: {e}", reply_markup=get_main_menu())
    await state.clear()

# ==================== ЗАПРОС ====================
@dp.callback_query(F.data == "wanted_ad")
async def start_wanted_ad(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📝 Опишите, что ищете:")
    await state.set_state(WantedAdState.waiting_for_desc)
    await callback.answer()

@dp.message(WantedAdState.waiting_for_desc)
async def wanted_desc(message: types.Message, state: FSMContext):
    await state.update_data(desc=message.text)
    await message.answer("🔍 OEM (или 'нет'):")
    await state.set_state(WantedAdState.waiting_for_oem)

@dp.message(WantedAdState.waiting_for_oem)
async def wanted_oem(message: types.Message, state: FSMContext):
    await state.update_data(oem=message.text)
    await message.answer("💰 Бюджет (грн):")
    await state.set_state(WantedAdState.waiting_for_budget)

@dp.message(WantedAdState.waiting_for_budget)
async def wanted_budget(message: types.Message, state: FSMContext):
    data = await state.get_data()
    post_text = (f"🔎 **КУПЛЮ**\n\n📝 {data['desc']}\n🔍 OEM: `{data['oem']}`\n💰 Бюджет: {message.text} грн\n\n#Куплю")
    contact_btn = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Предложить", url=f"tg://user?id={message.from_user.id}")]])
    try:
        sent_msg = await bot.send_message(chat_id=CHANNEL_ID, text=post_text, parse_mode="Markdown", reply_markup=contact_btn)
        if GROUP_ID:
            try: await bot.send_message(chat_id=GROUP_ID, text=f"🔔 **Запрос!**\n\n{post_text}", parse_mode="Markdown")
            except: pass
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("INSERT INTO requests (user_id, description, oem, budget, message_id) VALUES (%s, %s, %s, %s, %s)", 
                       (message.from_user.id, data['desc'], data['oem'], message.text, sent_msg.message_id))
        conn.commit(); cursor.close(); conn.close()
        await message.answer("✅ Опубликовано!", reply_markup=get_main_menu())
    except Exception as e: await message.answer(f"❌ Ошибка: {e}", reply_markup=get_main_menu())
    await state.clear()

# ==================== МОИ И ПОИСК ====================
@dp.callback_query(F.data == "my_ads")
async def show_my_ads(callback: types.CallbackQuery):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, name, price FROM ads WHERE user_id = %s", (callback.from_user.id,))
    results = cursor.fetchall(); cursor.close(); conn.close()
    if not results: await callback.message.answer("Нет объявлений.")
    else:
        for ad_id, name, price in results:
            buttons = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⭐ VIP (50 ⭐)", callback_data=f"pin_{ad_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{ad_id}")
            ]])
            await callback.message.answer(f"⚙️ {name}\n💰 {price} грн", reply_markup=buttons)
    await callback.answer()

@dp.callback_query(F.data.startswith("del_"))
async def delete_ad(callback: types.CallbackQuery):
    ad_id = callback.data.split("_")[1]
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT message_id FROM ads WHERE id = %s AND user_id = %s", (ad_id, callback.from_user.id))
    result = cursor.fetchone()
    if result:
        try: await bot.delete_message(chat_id=CHANNEL_ID, message_id=result[0])
        except: pass
        cursor.execute("DELETE FROM ads WHERE id = %s", (ad_id,)); conn.commit()
        await callback.message.edit_text("✅ Удалено.")
    cursor.close(); conn.close()
    await callback.answer()

@dp.callback_query(F.data == "search_oem")
async def start_search(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите OEM или название:")
    await state.set_state(SearchState.waiting_for_oem)
    await callback.answer()

@dp.message(SearchState.waiting_for_oem)
async def process_search(message: types.Message, state: FSMContext):
    search_query = message.text.strip().upper()
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT name, oem, price, photo_id, category, user_id, car_info FROM ads WHERE UPPER(REPLACE(REPLACE(oem, ' ', ''), '-', '')) LIKE %s OR UPPER(name) LIKE %s", (f"%{search_query}%", f"%{search_query}%"))
    results = cursor.fetchall(); cursor.close(); conn.close()
    if results:
        await message.answer(f"Найдено {len(results)}:")
        for res in results:
            btn = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Написать", url=f"tg://user?id={res[5]}")]])
            caption = f"🚘 {res[6]}\n📂 {res[4]}\n⚙️ {res[0]}\n🔍 OEM: {res[1]}\n💰 {res[2]} грн"
            await message.answer_photo(photo=res[3], caption=caption, reply_markup=btn)
    else: await message.answer("Ничего не найдено. 😔")
    await message.answer("Меню:", reply_markup=get_main_menu())
    await state.clear()

# ==================== API ДЛЯ MINI APP ====================
async def api_ads(request):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT id, name, oem, price, category, user_id, photo_id, car_info FROM ads ORDER BY id DESC LIMIT 50")
        rows = cursor.fetchall(); cursor.close(); conn.close()

        ads_list = []
        for row in rows:
            photo_url = ""
            if row[6]:
                # Генерируем ссылку на НАШ прокси-сервер, а не на Telegram
                photo_url = f"https://autobazar-a22p.onrender.com/api/photo/{row[6]}"

            ads_list.append({
                "id": row[0], "name": row[1], "oem": row[2], "price": row[3],
                "category": row[4], "user_id": row[5], "photo_url": photo_url, "car_info": row[7]
            })
        return web.json_response(ads_list, headers={'Access-Control-Allow-Origin': '*'})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500, headers={'Access-Control-Allow-Origin': '*'})

# НОВЫЙ ЭНДПОИНТ: ПРОКСИ ДЛЯ СКАЧИВАНИЯ ФОТО ИЗ TELEGRAM
async def api_photo(request):
    file_id = request.match_info['file_id']
    try:
        # Скачиваем файл из телеграма
        file_info = await bot.get_file(file_id)
        file_bytes = await bot.download_file(file_info.file_path)
        # Отдаем картинку в браузер
        return web.Response(body=file_bytes.read(), content_type='image/jpeg')
    except Exception as e:
        return web.Response(text=str(e), status=404)

# --- ВЕБ-СЕРВЕР ---
async def health_check(request):
    return web.Response(text="AutoBot is running!")

async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    await asyncio.sleep(3) 
    await bot.delete_webhook(drop_pending_updates=True)
    
    asyncio.create_task(auto_clean_loop())
    polling_task = asyncio.create_task(dp.start_polling(bot))
    
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/api/ads', api_ads)
    app.router.add_get('/api/photo/{file_id}', api_photo) # <-- ДОБАВИЛИ НОВЫЙ МАРШРУТ
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 5000)))
    await site.start()
    await polling_task

if __name__ == "__main__":
    asyncio.run(main())

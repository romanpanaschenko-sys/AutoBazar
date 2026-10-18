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

# --- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---
TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")
GROUP_ID = os.environ.get("GROUP_ID")
ADMIN_ID = 1087968824  # Твой ID

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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ads (
                id SERIAL PRIMARY KEY,
                user_id BIGINT, category TEXT, name TEXT, oem TEXT, 
                price TEXT, photo_id TEXT, message_id BIGINT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS requests (
                id SERIAL PRIMARY KEY,
                user_id BIGINT, description TEXT, oem TEXT, 
                budget TEXT, message_id BIGINT
            )
        ''')
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logging.error(f"Ошибка инициализации БД: {e}")

# --- СОСТОЯНИЯ ---
class AddAdState(StatesGroup):
    waiting_for_photo = State()
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
    builder.button(text="📦 Продать запчасть", callback_data="add_ad")
    builder.button(text="🔎 Куплю запчасть (Запрос)", callback_data="wanted_ad")
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

# --- АДМИН ПАНЕЛЬ КЛАВИАТУРА ---
def get_admin_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика", callback_data="admin_stats")
    builder.button(text="🗑 Удалить последнее объявление", callback_data="admin_del_ad")
    builder.button(text="🗑 Удалить последний запрос", callback_data="admin_del_req")
    builder.adjust(1)
    return builder.as_markup()

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🚗 Привет! Добро пожаловать в авто-барахолку.\n"
        "Выбери действие в меню ниже:",
        reply_markup=get_main_menu()
    )

# ==================== ДИАГНОСТИКА И АДМИН-ПАНЕЛЬ ====================
@dp.message(Command("myid"))
async def get_myid(message: types.Message):
    await message.reply(f"Ваш реальный ID: `{message.from_user.id}`\nID в коде бота: `{ADMIN_ID}`", parse_mode="Markdown")

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        # Если ID не совпадает, бот скажет об этом (чтобы мы поняли причину)
        await message.answer(f"⛔ Доступ запрещен.\nБот видит ваш ID как: `{message.from_user.id}`\nА в коде стоит: `{ADMIN_ID}`", parse_mode="Markdown")
        return
    await message.answer("👑 Панель администратора:", reply_markup=get_admin_menu())

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM ads")
        ads_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM requests")
        req_count = cursor.fetchone()[0]
        cursor.close(); conn.close()
        
        await callback.message.edit_text(
            f"📊 **Статистика базы данных:**\n\n"
            f"📦 Объявлений о продаже: {ads_count}\n"
            f"🔎 Запросов на покупку: {req_count}",
            parse_mode="Markdown",
            reply_markup=get_admin_menu()
        )
    except Exception as e:
        await callback.message.edit_text(f"Ошибка: {e}", reply_markup=get_admin_menu())
    await callback.answer()

@dp.callback_query(F.data == "admin_del_ad")
async def admin_del_ad(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT id, message_id, name FROM ads ORDER BY id DESC LIMIT 1")
        result = cursor.fetchone()
        
        if result:
            ad_id, msg_id, name = result
            try: await bot.delete_message(chat_id=CHANNEL_ID, message_id=msg_id)
            except: pass
            cursor.execute("DELETE FROM ads WHERE id = %s", (ad_id,))
            conn.commit()
            await callback.message.edit_text(f"✅ Последнее объявление «{name}» удалено.", reply_markup=get_admin_menu())
        else:
            await callback.message.edit_text("В базе нет объявлений.", reply_markup=get_admin_menu())
        cursor.close(); conn.close()
    except Exception as e:
        await callback.message.edit_text(f"Ошибка: {e}", reply_markup=get_admin_menu())
    await callback.answer()

@dp.callback_query(F.data == "admin_del_req")
async def admin_del_req(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT id, message_id, description FROM requests ORDER BY id DESC LIMIT 1")
        result = cursor.fetchone()
        
        if result:
            req_id, msg_id, desc = result
            try: await bot.delete_message(chat_id=CHANNEL_ID, message_id=msg_id)
            except: pass
            cursor.execute("DELETE FROM requests WHERE id = %s", (req_id,))
            conn.commit()
            await callback.message.edit_text(f"✅ Последний запрос удален.", reply_markup=get_admin_menu())
        else:
            await callback.message.edit_text("В базе нет запросов.", reply_markup=get_admin_menu())
        cursor.close(); conn.close()
    except Exception as e:
        await callback.message.edit_text(f"Ошибка: {e}", reply_markup=get_admin_menu())
    await callback.answer()

# ==================== ДОБАВЛЕНИЕ ПРОДАЖИ ====================
@dp.callback_query(F.data == "add_ad")
async def start_add_ad(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM ads WHERE user_id = %s", (user_id,))
        count = cursor.fetchone()[0]
        cursor.close(); conn.close()
        
        if count >= 15:
            await callback.message.answer("⚠️ У вас уже 15 активных объявлений. Удалите старые в разделе «Мои объявления».")
            await callback.answer()
            return
    except Exception:
        pass

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
    await message.answer("⚠️ Пожалуйста, отправьте именно фотографию.")

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
    await message.answer("💰 Шаг 5: Введите цену в грн:")
    await state.set_state(AddAdState.waiting_for_price)

@dp.message(AddAdState.waiting_for_price)
async def process_price(message: types.Message, state: FSMContext):
    data = await state.get_data()
    tags = generate_hashtags(data['category'], data['name'], data['oem'])
    post_text = (f"📂 **Категория:** {data['category']}\n⚙️ **{data['name']}**\n\n"
                 f"🔍 **OEM:** `{data['oem']}`\n💰 **Цена:** {message.text} грн\n\n{tags}")
    
    contact_btn = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Написать продавцу", url=f"tg://user?id={message.from_user.id}")]])
    
    try:
        sent_msg = await bot.send_photo(chat_id=CHANNEL_ID, photo=data['photo_id'], caption=post_text, parse_mode="Markdown", reply_markup=contact_btn)
        
        if GROUP_ID:
            try:
                await bot.send_message(chat_id=GROUP_ID, text=f"🆕 **Новое объявление о продаже!**\n\n{post_text}", parse_mode="Markdown")
            except Exception: pass

        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("INSERT INTO ads (user_id, category, name, oem, price, photo_id, message_id) VALUES (%s, %s, %s, %s, %s, %s, %s)", 
                       (message.from_user.id, data['category'], data['name'], data['oem'], message.text, data['photo_id'], sent_msg.message_id))
        conn.commit(); cursor.close(); conn.close()
        await message.answer("✅ Объявление опубликовано в канале!", reply_markup=get_main_menu())
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}", reply_markup=get_main_menu())
    await state.clear()

# ==================== ЗАПРОС (КУПЛЮ) ====================
@dp.callback_query(F.data == "wanted_ad")
async def start_wanted_ad(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📝 Опишите, какую запчасть вы ищете (марка, модель, деталь):")
    await state.set_state(WantedAdState.waiting_for_desc)
    await callback.answer()

@dp.message(WantedAdState.waiting_for_desc)
async def wanted_desc(message: types.Message, state: FSMContext):
    await state.update_data(desc=message.text)
    await message.answer("🔍 Введите OEM-номер (если знаете) или напишите 'нет':")
    await state.set_state(WantedAdState.waiting_for_oem)

@dp.message(WantedAdState.waiting_for_oem)
async def wanted_oem(message: types.Message, state: FSMContext):
    await state.update_data(oem=message.text)
    await message.answer("💰 Укажите ваш бюджет в грн (или напишите 'договорная'):")
    await state.set_state(WantedAdState.waiting_for_budget)

@dp.message(WantedAdState.waiting_for_budget)
async def wanted_budget(message: types.Message, state: FSMContext):
    data = await state.get_data()
    post_text = (f"🔎 **КУПЛЮ ЗАПЧАСТЬ**\n\n"
                 f"📝 **Описание:** {data['desc']}\n"
                 f"🔍 **OEM:** `{data['oem']}`\n"
                 f"💰 **Бюджет:** {message.text} грн\n\n"
                 f"#Куплю #Запрос #АвтоБарахолка")
    
    contact_btn = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Предложить деталь", url=f"tg://user?id={message.from_user.id}")]])
    
    try:
        sent_msg = await bot.send_message(chat_id=CHANNEL_ID, text=post_text, parse_mode="Markdown", reply_markup=contact_btn)
        
        if GROUP_ID:
            try:
                await bot.send_message(chat_id=GROUP_ID, text=f"🔔 **Новый запрос на поиск запчасти!**\n\n{post_text}", parse_mode="Markdown")
            except Exception: pass

        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("INSERT INTO requests (user_id, description, oem, budget, message_id) VALUES (%s, %s, %s, %s, %s)", 
                       (message.from_user.id, data['desc'], data['oem'], message.text, sent_msg.message_id))
        conn.commit(); cursor.close(); conn.close()
        await message.answer("✅ Ваш запрос опубликован в канале и отправлен в группу!", reply_markup=get_main_menu())
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}", reply_markup=get_main_menu())
    await state.clear()

# ==================== МОИ ОБЪЯВЛЕНИЯ, ПОИСК, VIP ====================
@dp.callback_query(F.data == "my_ads")
async def show_my_ads(callback: types.CallbackQuery):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT id, name, price FROM ads WHERE user_id = %s", (callback.from_user.id,))
        results = cursor.fetchall(); cursor.close(); conn.close()
        
        if not results:
            await callback.message.answer("У вас пока нет объявлений о продаже.")
        else:
            await callback.message.answer("Ваши объявления на продажу:")
            for ad_id, name, price in results:
                buttons = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="⭐ Закрепить", callback_data=f"pin_{ad_id}"),
                    InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{ad_id}")
                ]])
                await callback.message.answer(f"⚙️ {name}\n💰 {price} грн", reply_markup=buttons)
    except Exception as e:
        await callback.message.answer(f"Ошибка: {e}")
    await callback.answer()

@dp.callback_query(F.data.startswith("pin_"))
async def pin_ad(callback: types.CallbackQuery):
    ad_id = callback.data.split("_")[1]
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT message_id FROM ads WHERE id = %s AND user_id = %s", (ad_id, callback.from_user.id))
    result = cursor.fetchone(); cursor.close(); conn.close()
    if result:
        try:
            await bot.pin_chat_message(chat_id=CHANNEL_ID, message_id=result[0], disable_notification=False)
            await callback.message.edit_text("✅ Объявление закреплено в канале!")
        except Exception as e: await callback.message.edit_text(f"❌ Ошибка: {e}")
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
        cursor.execute("DELETE FROM ads WHERE id = %s", (ad_id,))
        conn.commit()
        await callback.message.edit_text("✅ Удалено.")
    cursor.close(); conn.close()
    await callback.answer()

@dp.callback_query(F.data == "search_oem")
async def start_search(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите OEM-номер для поиска:")
    await state.set_state(SearchState.waiting_for_oem)
    await callback.answer()

@dp.message(SearchState.waiting_for_oem)
async def process_search(message: types.Message, state: FSMContext):
    search_query = message.text.strip().upper()
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT name, oem, price, photo_id, category, user_id FROM ads WHERE UPPER(REPLACE(REPLACE(oem, ' ', ''), '-', '')) LIKE %s", (f"%{search_query}%",))
        results = cursor.fetchall(); cursor.close(); conn.close()
        
        if results:
            await message.answer(f"Найдено {len(results)} объявлений:")
            for res in results:
                btn = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Написать продавцу", url=f"tg://user?id={res[5]}")]])
                caption = f"📂 {res[4]}\n⚙️ {res[0]}\n🔍 OEM: {res[1]}\n💰 Цена: {res[2]} грн"
                await message.answer_photo(photo=res[3], caption=caption, reply_markup=btn)
        else:
            await message.answer("Ничего не найдено. 😔")
    except Exception as e: await message.answer(f"❌ Ошибка: {e}")
    await message.answer("Меню:", reply_markup=get_main_menu())
    await state.clear()

# --- ВЕБ-СЕРВЕР ---
async def health_check(request):
    return web.Response(text="AutoBot is running!")

async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    await asyncio.sleep(3) 
    await bot.delete_webhook(drop_pending_updates=True)
    polling_task = asyncio.create_task(dp.start_polling(bot))
    
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 5000)))
    await site.start()
    await polling_task

if __name__ == "__main__":
    asyncio.run(main())

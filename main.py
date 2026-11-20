import os
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================= НАСТРОЙКИ (ЧИТАЮТСЯ С САЙТА RAILWAY) =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID" 8158495271))

if not BOT_TOKEN:
    raise ValueError("Ошибка: Переменная окружения BOT_TOKEN не найдена! Проверь настройки в Railway.")
if ADMIN_ID == 8158495271:
    print("⚠️ Внимание: ADMIN_ID не установлен. Админ-панель будет недоступна.")

# Конфигурация буров (механика игры)
DRILLS = {
    "wood": {"name": "🪵 Деревянный бур", "base_rate": 1, "base_cost": 100},
    "stone": {"name": "🪨 Каменный бур", "base_rate": 5, "base_cost": 500},
    "iron": {"name": "🔩 Железный бур", "base_rate": 20, "base_cost": 2000},
    "diamond": {"name": "💎 Алмазный бур", "base_rate": 100, "base_cost": 10000}
}
MAX_SLOTS = 10

# ================= БАЗА ДАННЫХ =================
def init_db():
    conn = sqlite3.connect('bot_pay.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            coins INTEGER DEFAULT 1000,
            gold INTEGER DEFAULT 0,
            wood_count INTEGER DEFAULT 0,
            stone_count INTEGER DEFAULT 0,
            iron_count INTEGER DEFAULT 0,
            diamond_count INTEGER DEFAULT 0,
            wood_lvl INTEGER DEFAULT 1,
            stone_lvl INTEGER DEFAULT 1,
            iron_lvl INTEGER DEFAULT 1,
            diamond_lvl INTEGER DEFAULT 1,
            boost_expire INTEGER DEFAULT 0,
            last_income_ts INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

def get_user(user_id, username=None):
    conn = sqlite3.connect('bot_pay.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    if not row:
        now = int(datetime.now().timestamp())
        cursor.execute('''
            INSERT INTO users (user_id, username, coins, gold, last_income_ts)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, 1000, 0, now))
        conn.commit()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
    conn.close()
    return row

def save_user(user_id, **kwargs):
    conn = sqlite3.connect('bot_pay.db')
    cursor = conn.cursor()
    updates = ', '.join([f"{k} = ?" for k in kwargs.keys()])
    values = list(kwargs.values()) + [user_id]
    cursor.execute(f'UPDATE users SET {updates} WHERE user_id = ?', values)
    conn.commit()
    conn.close()

# ================= КЛАВИАТУРЫ =================
def get_main_menu(is_admin=False):
    kb = [
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🔩 Буры")],
        [KeyboardButton(text="🚀 Бусты"), KeyboardButton(text="🎁 Промо")]
    ]
    if is_admin:
        kb.append([KeyboardButton(text="⚙️ Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_drills_menu(user):
    builder = InlineKeyboardBuilder()
    # Считаем занятые слоты (индексы в кортеже: 4,5,6,7)
    total_count = user + user + user + user
    
    keys_map = {
        "wood": {"count_idx": 4, "lvl_idx": 8},
        "stone": {"count_idx": 5, "lvl_idx": 9},
        "iron": {"count_idx": 6, "lvl_idx": 10},
        "diamond": {"count_idx": 7, "lvl_idx": 11}
    }
    
    for key, data in DRILLS.items():
        idx = keys_map[key]
        count = user[idx["count_idx"]]
        lvl = user[idx["lvl_idx"]]
        buy_cost = data['base_cost'] * (lvl ** 2)
        upgrade_cost = data['base_cost'] * 10 * (lvl ** 2)
        
        if total_count < MAX_SLOTS:
            builder.button(text=f"🛒 Купить: {data['name']}", callback_data=f"buy_{key}")
        else:
            builder.button(text=f"❌ Лимит слотов", callback_data="no_action")
        
        if lvl < 10:
            builder.button(text=f"⬆️ Прокачать {data['name']}", callback_data=f"up_{key}")
        else:
            builder.button(text=f"✅ Макс. уровень", callback_data="no_action")
            
        if count > 0:
            builder.button(text=f"🗑️ Продать 1 шт.", callback_data=f"sell_{key}")
        
        builder.adjust(1)
    
    builder.button(text="🔙 Назад", callback_data="back_main")
    return builder.as_markup()

def get_admin_panel():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика пользователей", callback_data="admin_stats")
    builder.button(text="📢 Рассылка всем", callback_data="admin_broadcast")
    builder.button(text="💵 Начислить монеты (тест)", callback_data="admin_give_coins")
    builder.button(text="🔙 Назад", callback_data="back_main")
    return builder.as_markup()

# ================= ХЕНДЛЕРЫ =================
dp = Dispatcher()
bot = Bot(token=BOT_TOKEN)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = get_user(message.from_user.id, message.from_user.username)
    is_admin = (message.from_user.id == ADMIN_ID)
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}! Добро пожаловать в Bot.Pay!\n"
        f"Ты магнат буров. Покупай, прокачивай, продавай и добывай золото.",
        reply_markup=get_main_menu(is_admin)
    )

@dp.message(F.text == "👤 Профиль")
async def show_profile(message: types.Message):
    user = get_user(message.from_user.id)
    income, new_ts = calculate_income(user)
    
    if income > 0:
        save_user(user, gold=user + income, last_income_ts=new_ts)
        user = get_user(user)
        msg_text = f"💰 Получено золота: {income}!\n\n"
    else:
        msg_text = ""

    total_count = user + user + user + user
    
    msg_text += (
        f"👤 <b>Профиль Магната</b>\n"
        f"🪙 Монеты: {user}\n"
        f"💎 Золото: {user}\n"
        f"🔩 Занято слотов: {total_count}/{MAX_SLOTS}\n\n"
        f"🏗️ <b>Твои буры:</b>\n"
        f"🪵 Деревянный: {user} шт (Lvl {user})\n"
        f"🪨 Каменный: {user} шт (Lvl {user})\n"
        f"🔩 Железный: {user} шт (Lvl {user})\n"
        f"💎 Алмазный: {user} шт (Lvl {user})"
    )
    await message.answer(msg_text, parse_mode="HTML")

@dp.message(F.text == "🔩 Буры")
async def show_drills(message: types.Message):
    user = get_user(message.from_user.id)
    await message.answer("🏗️ Управление бурами:", reply_markup=get_drills_menu(user))

@dp.message(F.text == "⚙️ Админ-панель")
async def show_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ запрещен! Вы не администратор.")
        return
    await message.answer("⚙️ <b>Панель администратора</b>", parse_mode="HTML", reply_markup=get_admin_panel())

# ================= ЛОГИКА ДОХОДА =================
def calculate_income(user):
    # Распаковка кортежа из БД
    u_id, uname, coins, gold, w_c, s_c, i_c, d_c, w_l, s_l, i_l, d_l, boost_exp, last_ts = user
    now = int(datetime.now().timestamp())
    minutes_passed = (now - last_ts) // 60
    if minutes_passed <= 0: return 0, now

    def get_rate(base, lvl): return base * (1.3 ** (lvl - 1))

    income = (w_c * get_rate(DRILLS['wood']['base_rate'], w_l) +
              s_c * get_rate(DRILLS['stone']['base_rate'], s_l) +
              i_c * get_rate(DRILLS['iron']['base_rate'], i_l) +
              d_c * get_rate(DRILLS['diamond']['base_rate'], d_l))

    if boost_exp > now: income *= 2.0
    return int(income * minutes_passed), now

# ================= CALLBACKS (БУРЫ) =================
@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    key = callback.data.split("_")
    keys_map = {"wood": {"count_idx": 4, "lvl_idx": 8}, "stone": {"count_idx": 5, "lvl_idx": 9},
                "iron": {"count_idx": 6, "lvl_idx": 10}, "diamond": {"count_idx": 7, "lvl_idx": 11}}
    idx = keys_map[key]
    current_count = user[idx["count_idx"]]
    current_lvl = user[idx["lvl_idx"]]
    total_count = user + user + user + user
    cost = DRILLS[key]['base_cost'] * (current_lvl ** 2)
    
    if user < cost:
        await callback.answer("❌ Недостаточно монет!", show_alert=True)
        return
    if total_count >= MAX_SLOTS:
        await callback.answer("❌ Лимит слотов (10/10) достигнут!", show_alert=True)
        return

    new_count = current_count + 1
    new_coins = user - cost
    update_data = {f"{key}_count": new_count, "coins": new_coins}
    save_user(user, **update_data)
    await callback.message.edit_text(f"✅ Куплен 1 шт. {DRILLS[key]['name']}!", reply_markup=get_drills_menu(get_user(callback.from_user.id)))

@dp.callback_query(F.data.startswith("up_"))
async def process_upgrade(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    key = callback.data.split("_")
    keys_map = {"wood": {"count_idx": 4, "lvl_idx": 8}, "stone": {"count_idx": 5, "lvl_idx": 9},
                "iron": {"count_idx": 6, "lvl_idx": 10}, "diamond": {"count_idx": 7, "lvl_idx": 11}}
    idx = keys_map[key]
    current_lvl = user[idx["lvl_idx"]]
    
    if current_lvl >= 10:
        await callback.answer("✅ Уровень уже максимальный!", show_alert=True)
        return

    cost = DRILLS[key]['base_cost'] * 10 * (current_lvl ** 2)
    if user < cost:
        await callback.answer("❌ Недостаточно монет!", show_alert=True)
        return

    new_lvl = current_lvl + 1
    new_coins = user - cost
    update_data = {f"{key}_lvl": new_lvl, "coins": new_coins}
    save_user(user, **update_data)
    await callback.message.edit_text(f"✅ Прокачан {DRILLS[key]['name']} до уровня {new_lvl}!", reply_markup=get_drills_menu(get_user(callback.from_user.id)))

@dp.callback_query(F.data.startswith("sell_"))
async def process_sell(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    key = callback.data.split("_")
    keys_map = {"wood": {"count_idx": 4, "lvl_idx": 8}, "stone": {"count_idx": 5, "lvl_idx": 9},
                "iron": {"count_idx": 6, "lvl_idx": 10}, "diamond": {"count_idx": 7, "lvl_idx": 11}}
    idx = keys_map[key]
    current_count = user[idx["count_idx"]]
    
    if current_count <= 0:
        await callback.answer("❌ Нет таких буров!", show_alert=True)
        return

    sell_reward = int(DRILLS[key]['base_cost'] * (user[idx["lvl_idx"]] ** 2) * 0.5)
    new_count = current_count - 1
    new_coins = user + sell_reward
    update_data = {f"{key}_count": new_count, "coins": new_coins}
    save_user(user, **update_data)
    await callback.message.edit_text(f"🗑️ Продан 1 шт. {DRILLS[key]['name']}. Получено монет: {sell_reward}", reply_markup=get_drills_menu(get_user(callback.from_user.id)))

@dp.callback_query(F.data == "back_main")
async def back_to_main(callback: types.CallbackQuery):
    is_admin = (callback.from_user.id == ADMIN_ID)
    await callback.message.edit_text("Главное меню:", reply_markup=get_main_menu(is_admin))

@dp.callback_query(F.data == "no_action")
async def no_action(callback: types.CallbackQuery):
    await callback.answer("Действие недоступно", show_alert=True)

# ================= АДМИНКА (ЗАГЛУШКИ) =================
@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect('bot_pay.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*), SUM(coins), SUM(gold) FROM users')
    res = cursor.fetchone()
    conn.close()
    await callback.answer(f"📊 Статистика: Пользователей={res}, Монет={res}, Золота={res}", show_alert=True)

@dp.callback_query(F.data == "admin_give_coins")
async def admin_give(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    # В реальном проекте тут нужен ввод суммы и ID пользователя
    user = get_user(callback.from_user.id)
    save_user(user, coins=user + 10000) # Пример: +10к монет админу
    await callback.answer("✅ Админу начислено 10000 монет (для теста)", show_alert=True)

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
    
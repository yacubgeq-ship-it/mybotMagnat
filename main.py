import os
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

if not BOT_TOKEN:
    raise ValueError("Ошибка: Переменная окружения BOT_TOKEN не найдена!")

# ================= КОНФИГУРАЦИЯ =================
DRILLS = {
    "wood": {"name": "🪵 Деревянный бур", "base_rate": 1, "base_cost": 100},
    "stone": {"name": "🪨 Каменный бур", "base_cost": 500},
    "iron": {"name": "🔩 Железный бур", "base_cost": 2000},
    "diamond": {"name": "💎 Алмазный бур", "base_cost": 10000}
}
MAX_SLOTS = 10

# Промокоды: "КОД": НАГРАДА
VALID_PROMOCODES = {
    "SAILIX2024": 5000,
    "WELCOME": 1000
}

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
            promo_used TEXT DEFAULT ''
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
        cursor.execute('INSERT INTO users (user_id, username, coins, last_income_ts) VALUES (?, ?, ?, ?)', 
                       (user_id, username, 1000, now))
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
def get_main_menu():
    kb = [
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🎁 Промо")],
        [KeyboardButton(text="🔩 Буры"), KeyboardButton(text="🚀 Бусты")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_drills_menu(user):
    builder = InlineKeyboardBuilder()
    total_count = user + user + user + user
    
    levels = [user, user, user, user]
    counts = [user, user, user, user]
    keys = ["wood", "stone", "iron", "diamond"]
    
    for i, key in enumerate(keys):
        count = counts[i]
        lvl = levels[i]
        data = DRILLS[key]
        cost = data['base_cost'] * (lvl ** 2)
        
        if total_count < MAX_SLOTS:
            builder.button(text=f"🛒 Купить: {data['name']}", callback_data=f"buy_{key}")
        else:
            builder.button(text="❌ Лимит слотов", callback_data="no_action")
            
        if lvl < 10:
            builder.button(text=f"⬆️ Прокачать ({lvl}/{10})", callback_data=f"up_{key}")
        else:
            builder.button(text="✅ Макс. уровень", callback_data="no_action")
            
        if count > 0:
            builder.button(text="🗑️ Продать 1 шт.", callback_data=f"sell_{key}")
            
    builder.button(text="🔙 Назад", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()

# ================= ХЕНДЛЕРЫ =================
dp = Dispatcher()
bot = Bot(token=BOT_TOKEN)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    get_user(message.from_user.id, message.from_user.username)
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}! Добро пожаловать в SAILIX MAGNAT!\n"
        f"Стань магнатом буров. Покупай, прокачивай и добывай золото.",
        reply_markup=get_main_menu()
    )

@dp.message(F.text == "👤 Профиль")
async def show_profile(message: types.Message):
    user = get_user(message.from_user.id)
    # Расчет дохода (упрощенно для стабильности)
    income = 0 
    if user != 0: # last_income_ts
        now = int(datetime.now().timestamp())
        minutes = (now - user) // 60
        if minutes > 0:
            rate = (user*1 + user*5 + user*20 + user*100)
            income = rate * minutes
            if income > 0:
                save_user(user, gold=user + income, last_income_ts=now)
                user = get_user(user)

    total_slots = user + user + user + user
    
    text = (
        f"👤 <b>Профиль Магната</b>\n"
        f"🪙 Монеты: {user}\n"
        f"💎 Золото: {user}\n"
        f"🔩 Занято слотов: {total_slots}/{MAX_SLOTS}\n\n"
        f"🏗️ <b>Твои буры:</b>\n"
        f"🪵 Деревянный: {user} шт (Lvl {user})\n"
        f"🪨 Каменный: {user} шт (Lvl {user})\n"
        f"🔩 Железный: {user} шт (Lvl {user})\n"
        f"💎 Алмазный: {user} шт (Lvl {user})"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "🎁 Промо")
async def show_promo_input(message: types.Message):
    await message.answer("🎟 Введите промокод в чат (например, SAILIX2024):")

@dp.message(F.text)
async def process_text_input(message: types.Message):
    text = message.text.strip().upper()
    user = get_user(message.from_user.id)
    
    if text in VALID_PROMOCODES:
        used_list = user.split(',') if user else 
        if text in used_list:
            await message.answer("❌ Этот промокод уже был активирован!")
            return
        
        reward = VALID_PROMOCODES[text]
        new_coins = user + reward
        used_list.append(text)
        
        save_user(user, coins=new_coins, promo_used=','.join(used_list))
        await message.answer(f"✅ Промокод '{text}' активирован!\n💰 Начислено: {reward} монет.")
    else:
        # Если это не промокод, игнорируем или можно добавить команду
        pass

@dp.message(F.text == "🔩 Буры")
async def show_drills(message: types.Message):
    user = get_user(message.from_user.id)
    await message.answer("🏗️ Управление бурами:", reply_markup=get_drills_menu(user))

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    key = callback.data.split("_")
    
    idx_map = {"wood": 4, "stone": 5, "iron": 6, "diamond": 7}
    lvl_map = {"wood": 8, "stone": 9, "iron": 10, "diamond": 11}
    
    count_idx = idx_map[key]
    lvl_idx = lvl_map[key]
    
    current_count = user[count_idx]
    current_lvl = user[lvl_idx]
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
    
    col_name = f"{key}_count"
    save_user(user, **{col_name: new_count, "coins": new_coins})
    await callback.message.edit_text(f"✅ Куплен 1 шт. {DRILLS[key]['name']}!", reply_markup=get_drills_menu(get_user(callback.from_user.id)))

@dp.callback_query(F.data.startswith("up_"))
async def process_upgrade(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    key = callback.data.split("_")
    lvl_map = {"wood": 8, "stone": 9, "iron": 10, "diamond": 11}
    lvl_idx = lvl_map[key]
    
    current_lvl = user[lvl_idx]
    if current_lvl >= 10:
        await callback.answer("✅ Уровень уже максимальный!", show_alert=True)
        return

    cost = DRILLS[key]['base_cost'] * 10 * (current_lvl ** 2)
    if user < cost:
        await callback.answer("❌ Недостаточно монет!", show_alert=True)
        return

    new_lvl = current_lvl + 1
    new_coins = user - cost
    col_name = f"{key}_lvl"
    
    save_user(user, **{col_name: new_lvl, "coins": new_coins})
    await callback.message.edit_text(f"✅ Прокачан {DRILLS[key]['name']} до уровня {new_lvl}!", reply_markup=get_drills_menu(get_user(callback.from_user.id)))

@dp.callback_query(F.data.startswith("sell_"))
async def process_sell(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    key = callback.data.split("_")
    idx_map = {"wood": 4, "stone": 5, "iron": 6, "diamond": 7}
    lvl_map = {"wood": 8, "stone": 9, "iron": 10, "diamond": 11}
    
    count_idx = idx_map[key]
    lvl_idx = lvl_map[key]
    
    current_count = user[count_idx]
    current_lvl = user[lvl_idx]
    
    if current_count <= 0:
        await callback.answer("❌ Нет таких буров!", show_alert=True)
        return

    sell_reward = int(DRILLS[key]['base_cost'] * (current_lvl ** 2) * 0.5)
    new_count = current_count - 1
    new_coins = user + sell_reward
    
    col_name = f"{key}_count"
    save_user(user, **{col_name: new_count, "coins": new_coins})
    await callback.message.edit_text(f"🗑️ Продан 1 шт. {DRILLS[key]['name']}. Получено монет: {sell_reward}", reply_markup=get_drills_menu(get_user(callback.from_user.id)))

@dp.callback_query(F.data == "back_main")
async def back_to_main(callback: types.CallbackQuery):
    await callback.message.edit_text("Главное меню:", reply_markup=get_main_menu())

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
    
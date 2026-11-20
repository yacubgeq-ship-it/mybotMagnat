import os
import sqlite3
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================= НАСТРОЙКИ (ЧИТАЮТСЯ С САЙТА RAILWAY) =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8158495271"))

if not BOT_TOKEN:
    raise ValueError("Ошибка: Переменная окружения BOT_TOKEN не найдена! Проверь настройки в Railway.")
if not os.getenv("ADMIN_ID"):
    print("Внимание: ADMIN_ID не установлен. Админ-панель будет недоступна.")

# Конфигурация буров (механика игры)
DRILLS = {
    "wood": {"name": "🪵 Деревянный бур", "base_rate": 1, "base_cost": 100},
    "stone": {"name": "🪨 Каменный бур", "base_rate": 5, "base_cost": 500},
    "iron": {"name": "🔩 Железный бур", "base_rate": 20, "base_cost": 2000},
    "diamond": {"name": "💎 Алмазный бур", "base_rate": 100, "base_cost": 10000}
}
MAX_SLOTS = 10
BOOST_COST = 500       # цена буста в монетах
BOOST_SECONDS = 1800   # буст действует 30 минут
PROMO_REWARD = 500     # награда за промокод

DB_PATH = "bot_pay.db"


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ================= БАЗА ДАННЫХ =================
def init_db():
    conn = db_connect()
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
    # Мягкая миграция: добавляем колонку для промокода, если её нет
    cursor.execute("PRAGMA table_info(users)")
    cols = [c[1] for c in cursor.fetchall()]
    if "promo_used" not in cols:
        cursor.execute("ALTER TABLE users ADD COLUMN promo_used INTEGER DEFAULT 0")
    conn.commit()
    conn.close()


def get_user(user_id, username=None):
    conn = db_connect()
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
    return dict(row)  # работаем со словарём, а не с кортежем


def save_user(user_id, **kwargs):
    if not kwargs:
        return
    conn = db_connect()
    cursor = conn.cursor()
    updates = ', '.join(f"{k} = ?" for k in kwargs)
    cursor.execute(f'UPDATE users SET {updates} WHERE user_id = ?', list(kwargs.values()) + [user_id])
    conn.commit()
    conn.close()


def total_slots(user):
    return user["wood_count"] + user["stone_count"] + user["iron_count"] + user["diamond_count"]


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
    total = total_slots(user)

    for key, data in DRILLS.items():
        count = user[f"{key}_count"]
        lvl = user[f"{key}_lvl"]
        buy_cost = data["base_cost"] * (lvl ** 2)
        upgrade_cost = data["base_cost"] * 10 * (lvl ** 2)

        if total < MAX_SLOTS:
            builder.button(text=f"🛒 Купить: {data['name']} ({buy_cost} монет)", callback_data=f"buy_{key}")
        else:
            builder.button(text="❌ Лимит слотов достигнут", callback_data="no_action")

        if lvl >= 10:
            builder.button(text=f"✅ {data['name']}: макс. уровень", callback_data="no_action")
        elif count == 0:
            # Прокачивать пустой бур нельзя — это и была ошибка на скриншоте
            builder.button(text=f"⬆️ {data['name']}: нет в наличии", callback_data="no_drill")
        else:
            builder.button(text=f"⬆️ Прокачать {data['name']} ({upgrade_cost} монет)", callback_data=f"up_{key}")

        if count > 0:
            builder.button(text=f"🗑️ Продать 1 шт. {data['name']}", callback_data=f"sell_{key}")

    builder.button(text="🔙 Назад", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()


def get_boost_menu(user):
    builder = InlineKeyboardBuilder()
    now = int(datetime.now().timestamp())
    if user["boost_expire"] > now:
        mins = (user["boost_expire"] - now) // 60 + 1
        builder.button(text=f"🚀 Буст активен ещё ~{mins} мин", callback_data="no_action")
    else:
        builder.button(text=f"🚀 Купить буст x2 на 30 мин ({BOOST_COST} монет)", callback_data="buy_boost")
    builder.button(text="🔙 Назад", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()


def get_promo_menu(user):
    builder = InlineKeyboardBuilder()
    if user["promo_used"]:
        builder.button(text="✅ Промокод уже использован", callback_data="no_action")
    else:
        builder.button(text=f"🎁 Активировать промокод SAILIX (+{PROMO_REWARD} монет)", callback_data="use_promo")
    builder.button(text="🔙 Назад", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_panel():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика пользователей", callback_data="admin_stats")
    builder.button(text="📢 Рассылка всем", callback_data="admin_broadcast")
    builder.button(text="💵 Начислить монеты (тест)", callback_data="admin_give_coins")
    builder.button(text="🔙 Назад", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()


# ================= ЛОГИКА ДОХОДА =================
def calculate_income(user):
    now = int(datetime.now().timestamp())
    minutes_passed = (now - user["last_income_ts"]) // 60
    if minutes_passed <= 0:
        return 0, now

    def get_rate(base, lvl):
        return base * (1.3 ** (lvl - 1))

    income = (user["wood_count"] * get_rate(DRILLS["wood"]["base_rate"], user["wood_lvl"]) +
              user["stone_count"] * get_rate(DRILLS["stone"]["base_rate"], user["stone_lvl"]) +
              user["iron_count"] * get_rate(DRILLS["iron"]["base_rate"], user["iron_lvl"]) +
              user["diamond_count"] * get_rate(DRILLS["diamond"]["base_rate"], user["diamond_lvl"]))

    if user["boost_expire"] > now:
        income *= 2.0
    return int(income * minutes_passed), now


# ================= ХЕНДЛЕРЫ =================
dp = Dispatcher()
bot = Bot(token=BOT_TOKEN)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    get_user(message.from_user.id, message.from_user.username)
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
        save_user(user["user_id"], gold=user["gold"] + income, last_income_ts=new_ts)
        user = get_user(user["user_id"])
        msg_text = f"💰 Получено золота: {income}!\n\n"
    else:
        msg_text = ""

    boost_line = ""
    now = int(datetime.now().timestamp())
    if user["boost_expire"] > now:
        mins = (user["boost_expire"] - now) // 60 + 1
        boost_line = f"🚀 Буст x2 активен ещё ~{mins} мин\n"

    msg_text += (
        f"👤 <b>Профиль Магната</b>\n"
        f"🪙 Монеты: {user['coins']}\n"
        f"💎 Золото: {user['gold']}\n"
        f"🔩 Занято слотов: {total_slots(user)}/{MAX_SLOTS}\n"
        f"{boost_line}\n"
        f"🏗️ <b>Твои буры:</b>\n"
        f"🪵 Деревянный: {user['wood_count']} шт (Lvl {user['wood_lvl']})\n"
        f"🪨 Каменный: {user['stone_count']} шт (Lvl {user['stone_lvl']})\n"
        f"🔩 Железный: {user['iron_count']} шт (Lvl {user['iron_lvl']})\n"
        f"💎 Алмазный: {user['diamond_count']} шт (Lvl {user['diamond_lvl']})"
    )
    await message.answer(msg_text, parse_mode="HTML")


@dp.message(F.text == "🔩 Буры")
async def show_drills(message: types.Message):
    user = get_user(message.from_user.id)
    await message.answer("🏗️ Управление бурами:", reply_markup=get_drills_menu(user))


@dp.message(F.text == "🚀 Бусты")
async def show_boosts(message: types.Message):
    user = get_user(message.from_user.id)
    await message.answer(
        "🚀 <b>Бусты</b>\nБуст удваивает добычу золота на 30 минут.",
        parse_mode="HTML",
        reply_markup=get_boost_menu(user)
    )


@dp.message(F.text == "🎁 Промо")
async def show_promo(message: types.Message):
    user = get_user(message.from_user.id)
    await message.answer(
        "🎁 <b>Промокод</b>\nОдин промокод на аккаунт — активируй и получи монеты.",
        parse_mode="HTML",
        reply_markup=get_promo_menu(user)
    )


@dp.message(F.text == "⚙️ Админ-панель")
async def show_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ запрещен! Вы не администратор.")
        return
    await message.answer("⚙️ <b>Панель администратора</b>", parse_mode="HTML", reply_markup=get_admin_panel())


# ================= CALLBACKS (БУРЫ) =================
@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    key = callback.data.split("_", 1)[1]
    if key not in DRILLS:
        await callback.answer("Неизвестный бур", show_alert=True)
        return

    current_lvl = user[f"{key}_lvl"]
    total = total_slots(user)
    cost = DRILLS[key]["base_cost"] * (current_lvl ** 2)

    if total >= MAX_SLOTS:
        await callback.answer("❌ Лимит слотов (10/10) достигнут!", show_alert=True)
        return
    if user["coins"] < cost:
        await callback.answer("❌ Недостаточно монет!", show_alert=True)
        return

    save_user(user["user_id"], **{f"{key}_count": user[f"{key}_count"] + 1, "coins": user["coins"] - cost})
    await callback.message.edit_text(
        f"✅ Куплен 1 шт. {DRILLS[key]['name']}!",
        reply_markup=get_drills_menu(get_user(callback.from_user.id))
    )


@dp.callback_query(F.data.startswith("up_"))
async def process_upgrade(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    key = callback.data.split("_", 1)[1]
    if key not in DRILLS:
        await callback.answer("Неизвестный бур", show_alert=True)
        return

    current_lvl = user[f"{key}_lvl"]
    current_count = user[f"{key}_count"]

    if current_lvl >= 10:
        await callback.answer("✅ Уровень уже максимальный!", show_alert=True)
        return
    if current_count <= 0:
        # Вторая ошибка со скриншота: раньше можно было качать бур, которого нет
        await callback.answer("❌ Сначала купите этот бур!", show_alert=True)
        return

    cost = DRILLS[key]["base_cost"] * 10 * (current_lvl ** 2)
    if user["coins"] < cost:
        await callback.answer("❌ Недостаточно монет!", show_alert=True)
        return

    save_user(user["user_id"], **{f"{key}_lvl": current_lvl + 1, "coins": user["coins"] - cost})
    await callback.message.edit_text(
        f"✅ Прокачан {DRILLS[key]['name']} до уровня {current_lvl + 1}!",
        reply_markup=get_drills_menu(get_user(callback.from_user.id))
    )


@dp.callback_query(F.data.startswith("sell_"))
async def process_sell(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    key = callback.data.split("_", 1)[1]
    if key not in DRILLS:
        await callback.answer("Неизвестный бур", show_alert=True)
        return

    current_count = user[f"{key}_count"]
    if current_count <= 0:
        await callback.answer("❌ Нет таких буров!", show_alert=True)
        return

    sell_reward = int(DRILLS[key]["base_cost"] * (user[f"{key}_lvl"] ** 2) * 0.5)
    save_user(user["user_id"], **{f"{key}_count": current_count - 1, "coins": user["coins"] + sell_reward})
    await callback.message.edit_text(
        f"🗑️ Продан 1 шт. {DRILLS[key]['name']}. Получено монет: {sell_reward}",
        reply_markup=get_drills_menu(get_user(callback.from_user.id))
    )


# ================= CALLBACKS (БУСТЫ / ПРОМО) =================
@dp.callback_query(F.data == "buy_boost")
async def process_buy_boost(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    now = int(datetime.now().timestamp())
    if user["boost_expire"] > now:
        await callback.answer("🚀 Буст уже активен!", show_alert=True)
        return
    if user["coins"] < BOOST_COST:
        await callback.answer("❌ Недостаточно монет!", show_alert=True)
        return
    save_user(user["user_id"], coins=user["coins"] - BOOST_COST, boost_expire=now + BOOST_SECONDS)
    await callback.message.edit_text("🚀 Буст x2 активирован на 30 минут!", reply_markup=get_boost_menu(get_user(callback.from_user.id)))


@dp.callback_query(F.data == "use_promo")
async def process_promo(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    if user["promo_used"]:
        await callback.answer("🎁 Промокод уже использован!", show_alert=True)
        return
    save_user(user["user_id"], coins=user["coins"] + PROMO_REWARD, promo_used=1)
    await callback.message.edit_text(f"🎁 Промокод активирован! Начислено {PROMO_REWARD} монет.", reply_markup=get_promo_menu(get_user(callback.from_user.id)))


@dp.callback_query(F.data == "no_drill")
async def no_drill(callback: types.CallbackQuery):
    await callback.answer("❌ У вас нет этого бура — сначала купите его!", show_alert=True)


@dp.callback_query(F.data == "back_main")
async def back_to_main(callback: types.CallbackQuery):
    # ReplyKeyboard нельзя прикрепить к edit_text, поэтому просто редактируем текст
    await callback.message.edit_text("🏠 Главное меню. Используй кнопки клавиатуры снизу.")
    await callback.answer()


@dp.callback_query(F.data == "no_action")
async def no_action(callback: types.CallbackQuery):
    await callback.answer("Действие недоступно", show_alert=True)


# ================= АДМИНКА (ЗАГЛУШКИ) =================
@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*), COALESCE(SUM(coins), 0), COALESCE(SUM(gold), 0) FROM users')
    users_count, coins_sum, gold_sum = cursor.fetchone()
    conn.close()
    await callback.answer(
        f"📊 Пользователей: {users_count}\n🪙 Монет у всех: {coins_sum}\n💎 Золота у всех: {gold_sum}",
        show_alert=True
    )


@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.answer("📢 Рассылка в разработке", show_alert=True)


@dp.callback_query(F.data == "admin_give_coins")
async def admin_give(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    user = get_user(callback.from_user.id)
    save_user(user["user_id"], coins=user["coins"] + 10000)
    await callback.answer("✅ Админу начислено 10000 монет (для теста)", show_alert=True)


async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

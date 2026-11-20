# -*- coding: utf-8 -*-
"""
SAILIX SEA BATTLE — классический морской бой в Telegram.
Стек: Python 3.10+, aiogram 3.x, SQLite.
Запуск на Railway: переменные окружения BOT_TOKEN и ADMIN_ID.
"""

import os
import re
import json
import random
import sqlite3
import asyncio
from datetime import datetime

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================= НАСТРОЙКИ (ЧИТАЮТСЯ С САЙТА RAILWAY) =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "bot_seabattle.db")

STAKE = 100        # ставка за бой (списывается при начале)
WIN_REWARD = 300   # выплата за победу
START_COINS = 1000 # стартовый капитал нового игрока

if not BOT_TOKEN:
    raise ValueError("Ошибка: переменная окружения BOT_TOKEN не найдена! Проверь настройки в Railway.")
if ADMIN_ID == 0:
    print("⚠️ Внимание: ADMIN_ID не установлен. Админ-панель будет недоступна.")

# ================= ИГРОВАЯ ЛОГИКА =================
SIZE = 10
LETTERS = "АБВГДЕЖЗИК"          # классическая русская раскладка поля
SHIP_SIZES = [4, 3, 3, 2, 2, 2, 1, 1, 1, 1]  # 1×4, 2×3, 3×2, 4×1
SHIP_NAMES = {4: "линкор", 3: "крейсер", 2: "эсминец", 1: "катер"}

COORD_RE = re.compile(
    r"^\s*([АБВГДЕЖЗИКабвгдежзикABCDEFGHJIabcdefghji])\s*[-\s.,]?(\d{1,2})\s*$"
)


def _can_place(occupied, r0, c0, size, vertical):
    """Проверяет, можно ли поставить корабль: не вылезает за поле
    и не касается других кораблей даже по диагонали."""
    for i in range(size):
        r, c = (r0 + i, c0) if vertical else (r0, c0 + i)
        if r >= SIZE or c >= SIZE:
            return False
        for rr in range(max(0, r - 1), min(SIZE, r + 2)):
            for cc in range(max(0, c - 1), min(SIZE, c + 2)):
                if occupied[rr][cc]:
                    return False
    return True


def place_ships():
    """Случайно расставляет флот. Возвращает доску:
    {"ships": [[[r,c],...], ...], "hits": [...], "misses": [...]}"""
    while True:
        occupied = [[0] * SIZE for _ in range(SIZE)]
        ships = []
        ok = True
        for size in SHIP_SIZES:
            placed = False
            for _ in range(500):
                vertical = random.choice((True, False))
                if vertical:
                    r0 = random.randint(0, SIZE - size)
                    c0 = random.randint(0, SIZE - 1)
                else:
                    r0 = random.randint(0, SIZE - 1)
                    c0 = random.randint(0, SIZE - size)
                if _can_place(occupied, r0, c0, size, vertical):
                    cells = []
                    for i in range(size):
                        r, c = (r0 + i, c0) if vertical else (r0, c0 + i)
                        occupied[r][c] = 1
                        cells.append([r, c])
                    ships.append(cells)
                    placed = True
                    break
            if not placed:
                ok = False
                break
        if ok:
            return {"ships": ships, "hits": [], "misses": []}


def _cell_set(cells):
    return {tuple(x) for x in cells}


def sunk_cells(board):
    """Клетки полностью уничтоженных кораблей."""
    hits = _cell_set(board["hits"])
    sunk = set()
    for ship in board["ships"]:
        if all(tuple(cell) in hits for cell in ship):
            sunk.update(tuple(cell) for cell in ship)
    return sunk


def all_sunk(board):
    hits = _cell_set(board["hits"])
    return all(all(tuple(cell) in hits for cell in ship) for ship in board["ships"])


def fire(board, r, c):
    """Выстрел по доске. Возвращает (результат, клетки_убитого_корабля)."""
    t = (r, c)
    if t in _cell_set(board["hits"]) or t in _cell_set(board["misses"]):
        return "repeat", None
    for ship in board["ships"]:
        cells = [tuple(cell) for cell in ship]
        if t in cells:
            board["hits"].append([r, c])
            if all(tuple(cell) in _cell_set(board["hits"]) for cell in ship):
                return "kill", [[r0, c0] for r0, c0 in cells]
            return "hit", None
    board["misses"].append([r, c])
    return "miss", None


def bot_pick(board):
    """Ход бота: сначала добивает раненые корабли, потом стреляет случайно."""
    hits = _cell_set(board["hits"])
    misses = _cell_set(board["misses"])
    shot = hits | misses
    wounded = [t for t in hits if t not in sunk_cells(board)]

    if wounded:
        # 1) если два попадания в одну линию — добиваем по линии
        line = []
        for r in range(SIZE):
            cs = sorted(cc for (rr, cc) in wounded if rr == r)
            if len(cs) >= 2:
                line.extend([(r, cs[0] - 1), (r, cs[-1] + 1)])
        for c in range(SIZE):
            rs = sorted(rr for (rr, cc) in wounded if cc == c)
            if len(rs) >= 2:
                line.extend([(rs[0] - 1, c), (rs[-1] + 1, c)])
        line = [t for t in line if 0 <= t[0] < SIZE and 0 <= t[1] < SIZE and t not in shot]
        if line:
            return random.choice(line)
        # 2) иначе — соседняя клетка раненого корабля
        neigh = []
        for (r, c) in wounded:
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (r + dr, c + dc)
                if 0 <= n[0] < SIZE and 0 <= n[1] < SIZE and n not in shot and n not in neigh:
                    neigh.append(n)
        if neigh:
            return random.choice(neigh)
    # 3) случайная свободная клетка
    free = [(r, c) for r in range(SIZE) for c in range(SIZE) if (r, c) not in shot]
    return random.choice(free)


def parse_coords(text):
    """Б5 / б-5 / j3 -> (row, col) или None."""
    m = COORD_RE.match(text or "")
    if not m:
        return None
    ch = m.group(1).upper()
    if ch in LETTERS:
        col = LETTERS.index(ch)
    elif ch in "ABCDEFGHIJ":
        col = "ABCDEFGHIJ".index(ch)
    else:
        return None
    row = int(m.group(2)) - 1
    if not (0 <= row < SIZE):
        return None
    return row, col


def cell_name(r, c):
    return f"{LETTERS[c]}{r + 1}"


def render_board(board, reveal):
    """Текстовое поле. reveal=True — показать корабли (своё поле/финал)."""
    hits = _cell_set(board["hits"])
    misses = _cell_set(board["misses"])
    sunk = sunk_cells(board)
    ship_cells = {tuple(cell) for ship in board["ships"] for cell in ship}

    lines = ["   " + " ".join(LETTERS)]
    for r in range(SIZE):
        row = [f"{r + 1:>2}"]
        for c in range(SIZE):
            t = (r, c)
            if t in sunk:
                row.append("☠️")
            elif t in hits:
                row.append("🔥")
            elif t in misses:
                row.append("🌊")
            elif reveal and t in ship_cells:
                row.append("🚢")
            else:
                row.append("▫️")
        lines.append(" ".join(row))
    return "\n".join(lines)


def game_text(state, extra=""):
    lines = [
        "⚓️ МОРСКОЙ БОЙ",
        "",
        "🤖 Поле бота:",
        render_board(state["bot"], reveal=False),
        "",
        "🧭 Ваше поле:",
        render_board(state["player"], reveal=True),
    ]
    if extra:
        lines += ["", extra]
    else:
        lines += ["", "🎯 Ваш ход: пришлите координаты, например Б5"]
    return "\n".join(lines)


def placing_text(state):
    return (
        "⚓️ МОРСКОЙ БОЙ\n\n"
        f"💰 Ставка: {STAKE} монет → победа приносит {WIN_REWARD}.\n"
        "Флот: 1×4, 2×3, 3×2, 4×1. Корабли не соприкасаются даже углами.\n"
        "Попадание даёт ещё один выстрел, промах — ход бота.\n\n"
        "🧭 Ваш флот (можно перетасовать):\n"
        + render_board(state["player"], reveal=True)
    )


def final_text(state, won, surrender=False, extra=""):
    if won:
        title = "🏆 ПОБЕДА!"
        body = f"Флот бота уничтожен. Награда: +{WIN_REWARD} монет!"
    elif surrender:
        title = "🏳️ Вы сдались"
        body = f"Ставка {STAKE} монет потеряна."
    else:
        title = "💀 ПОРАЖЕНИЕ"
        body = f"Ваш флот уничтожен. Ставка {STAKE} монет потеряна."
    lines = [
        title,
        "",
        "🤖 Поле бота (раскрыто):",
        render_board(state["bot"], reveal=True),
        "",
        "🧭 Ваше поле:",
        render_board(state["player"], reveal=True),
        "",
        body,
    ]
    if extra:
        lines += ["", extra]
    return "\n".join(lines)

# ================= КОНЕЦ ИГРОВОЙ ЛОГИКИ =================


# ================= БАЗА ДАННЫХ =================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            coins INTEGER DEFAULT 1000,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS games (
            game_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            state TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'placing',
            message_id INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_games_user ON games(user_id, status)")
    conn.commit()
    conn.close()


def get_user(user_id, username=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO users (user_id, username, coins) VALUES (?, ?, ?)",
            (user_id, username, START_COINS),
        )
        conn.commit()
        cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
    conn.close()
    return dict(row)


def save_user(user_id, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    updates = ", ".join(f"{k} = ?" for k in kwargs)
    cur.execute(f"UPDATE users SET {updates} WHERE user_id = ?", [*kwargs.values(), user_id])
    conn.commit()
    conn.close()


def create_game(user_id):
    state = {"player": place_ships(), "bot": place_ships(), "log": []}
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO games (user_id, state, status, created_at) VALUES (?, ?, 'placing', ?)",
        (user_id, json.dumps(state), int(datetime.now().timestamp())),
    )
    conn.commit()
    game_id = cur.lastrowid
    conn.close()
    return game_id


def get_active_game(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM games WHERE user_id = ? AND status IN ('placing', 'active') "
        "ORDER BY game_id DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def drop_game(game_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM games WHERE game_id = ?", (game_id,))
    conn.commit()
    conn.close()


def save_game(game_id, state, status=None, message_id=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    sets = ["state = ?"]
    vals = [json.dumps(state)]
    if status:
        sets.append("status = ?")
        vals.append(status)
    if message_id:
        sets.append("message_id = ?")
        vals.append(message_id)
    vals.append(game_id)
    cur.execute(f"UPDATE games SET {', '.join(sets)} WHERE game_id = ?", vals)
    conn.commit()
    conn.close()


def finish_game_state(user_id, won, surrender=False):
    user = get_user(user_id)
    updates = {"wins": user["wins"] + (1 if won else 0),
               "losses": user["losses"] + (0 if won else 1)}
    if won:
        updates["coins"] = user["coins"] + WIN_REWARD
    save_user(user_id, **updates)

# ================= КЛАВИАТУРЫ =================
def get_main_menu(is_admin=False):
    kb = [
        [KeyboardButton(text="🎯 Играть"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🏆 Топ")],
    ]
    if is_admin:
        kb.append([KeyboardButton(text="⚙️ Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def placing_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Расставить заново", callback_data="sb_shuffle")
    b.button(text="⚔️ В бой!", callback_data="sb_fight")
    b.button(text="❌ Отмена", callback_data="sb_cancel")
    b.adjust(2, 1)
    return b.as_markup()


def battle_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🏳️ Сдаться", callback_data="sb_surrender")
    return b.as_markup()


def finished_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🎯 Новая игра", callback_data="sb_new")
    return b.as_markup()


def admin_kb():
    b = InlineKeyboardBuilder()
    b.button(text="📊 Статистика", callback_data="admin_stats")
    b.button(text="💵 Начислить себе монеты", callback_data="admin_give_coins")
    b.button(text="📢 Рассылка всем", callback_data="admin_broadcast")
    b.adjust(1)
    return b.as_markup()

# ================= БОТ И ХЕНДЛЕРЫ =================
dp = Dispatcher()
bot = Bot(token=BOT_TOKEN)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = get_user(message.from_user.id, message.from_user.username)
    is_admin = message.from_user.id == ADMIN_ID
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}! Это классический морской бой.\n\n"
        f"• Ставка за бой — {STAKE} монет, победа приносит {WIN_REWARD}.\n"
        "• Флот расставляется автоматически, но можно перетасовать.\n"
        "• Стреляйте координатами вида Б5 (буква — столбец, число — строка).\n"
        "• Попадание даёт ещё один выстрел, промах — ход бота.\n\n"
        f"На счету: {user['coins']} монет. Удачи на морях! ⚓️",
        reply_markup=get_main_menu(is_admin),
    )


@dp.message(F.text == "🎯 Играть")
async def play(message: types.Message):
    user = get_user(message.from_user.id, message.from_user.username)
    existing = get_active_game(user["user_id"])
    if existing and existing["status"] == "active":
        await message.answer(
            "⚔️ У вас уже идёт бой! Пришлите координаты выстрела (например Б5) "
            "или нажмите «Сдаться» в сообщении игры."
        )
        return
    if existing:  # старая расстановка — отменяем, на ставку она не влияет
        drop_game(existing["game_id"])
    if user["coins"] < STAKE:
        await message.answer(
            f"❌ Не хватает монет: нужно {STAKE}, у вас {user['coins']}."
        )
        return
    game_id = create_game(user["user_id"])
    game = get_active_game(user["user_id"])
    state = json.loads(game["state"])
    msg = await message.answer(placing_text(state), reply_markup=placing_kb())
    save_game(game_id, state, message_id=msg.message_id)


@dp.message(F.text == "👤 Профиль")
async def show_profile(message: types.Message):
    user = get_user(message.from_user.id)
    total = user["wins"] + user["losses"]
    rate = f"{round(100 * user['wins'] / total)}%" if total else "—"
    await message.answer(
        "👤 <b>Профиль моряка</b>\n"
        f"🪙 Монеты: {user['coins']}\n"
        f"🏆 Победы: {user['wins']}\n"
        f"💀 Поражения: {user['losses']}\n"
        f"📈 Винрейт: {rate}",
        parse_mode="HTML",
    )


@dp.message(F.text == "🏆 Топ")
async def show_top(message: types.Message):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT username, wins, losses FROM users ORDER BY wins DESC, coins DESC LIMIT 10"
    )
    rows = cur.fetchall()
    conn.close()
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Адмиралы морей</b>", ""]
    for i, (uname, wins, losses) in enumerate(rows, 1):
        name = ("@" + uname) if uname else "безымянный"
        icon = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"{icon} {name} — {wins} побед / {losses} поражений")
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(F.text == "⚙️ Админ-панель")
async def show_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ запрещён! Вы не администратор.")
        return
    await message.answer("⚙️ <b>Панель администратора</b>", parse_mode="HTML",
                         reply_markup=admin_kb())


# ================= CALLBACKS =================
@dp.callback_query(F.data == "sb_shuffle")
async def cb_shuffle(callback: types.CallbackQuery):
    game = get_active_game(callback.from_user.id)
    if not game or game["status"] != "placing":
        await callback.answer("Эта расстановка уже неактуальна", show_alert=True)
        return
    state = json.loads(game["state"])
    state["player"] = place_ships()
    save_game(game["game_id"], state)
    await callback.message.edit_text(placing_text(state), reply_markup=placing_kb())
    await callback.answer("Расстановка обновлена ⚓️")


@dp.callback_query(F.data == "sb_fight")
async def cb_fight(callback: types.CallbackQuery):
    game = get_active_game(callback.from_user.id)
    if not game or game["status"] != "placing":
        await callback.answer("Бой уже начался или завершён", show_alert=True)
        return
    user = get_user(callback.from_user.id)
    if user["coins"] < STAKE:
        await callback.answer(f"❌ Нужно минимум {STAKE} монет!", show_alert=True)
        return
    state = json.loads(game["state"])
    save_user(user["user_id"], coins=user["coins"] - STAKE)
    save_game(game["game_id"], state, status="active")
    await callback.message.edit_text(
        game_text(state, f"💰 Со счёта списана ставка {STAKE} монет."),
        reply_markup=battle_kb(),
    )
    await callback.answer("В бой! ⚔️")


@dp.callback_query(F.data == "sb_cancel")
async def cb_cancel(callback: types.CallbackQuery):
    game = get_active_game(callback.from_user.id)
    if not game or game["status"] != "placing":
        await callback.answer("Нечего отменять", show_alert=True)
        return
    drop_game(game["game_id"])
    await callback.message.edit_text("⚓️ Игра отменена. Ставка не списана.",
                                     reply_markup=finished_kb())
    await callback.answer("Отменено")


@dp.callback_query(F.data == "sb_surrender")
async def cb_surrender(callback: types.CallbackQuery):
    game = get_active_game(callback.from_user.id)
    if not game or game["status"] != "active":
        await callback.answer("Активного боя нет", show_alert=True)
        return
    state = json.loads(game["state"])
    finish_game_state(callback.from_user.id, won=False, surrender=True)
    save_game(game["game_id"], state, status="finished")
    await callback.message.edit_text(
        final_text(state, won=False, surrender=True), reply_markup=finished_kb()
    )
    await callback.answer("Вы сдались 🏳️")


@dp.callback_query(F.data == "sb_new")
async def cb_new(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id, callback.from_user.username)
    existing = get_active_game(user["user_id"])
    if existing and existing["status"] == "active":
        await callback.answer("Сначала завершите текущий бой!", show_alert=True)
        return
    if existing:
        drop_game(existing["game_id"])
    if user["coins"] < STAKE:
        await callback.answer(f"❌ Нужно минимум {STAKE} монет!", show_alert=True)
        return
    create_game(user["user_id"])
    game = get_active_game(user["user_id"])
    state = json.loads(game["state"])
    await callback.message.edit_text(placing_text(state), reply_markup=placing_kb())
    save_game(game["game_id"], state, message_id=callback.message.message_id)
    await callback.answer("Новая игра! ⚓️")


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), COALESCE(SUM(coins), 0), COALESCE(SUM(wins), 0), "
                "COALESCE(SUM(losses), 0) FROM users")
    users, coins, wins, losses = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM games WHERE status = 'active'")
    active = cur.fetchone()[0]
    conn.close()
    await callback.answer(
        f"📊 Игроков: {users} | Монет в системе: {coins}\n"
        f"Побед: {wins} | Поражений: {losses} | Активных боёв: {active}",
        show_alert=True,
    )


@dp.callback_query(F.data == "admin_give_coins")
async def admin_give(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    user = get_user(callback.from_user.id)
    save_user(user["user_id"], coins=user["coins"] + 10000)
    await callback.answer("✅ Админу начислено 10000 монет (для теста)", show_alert=True)


@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.answer("📢 Рассылка — в разработке", show_alert=True)


@dp.callback_query(F.data == "no_action")
async def no_action(callback: types.CallbackQuery):
    await callback.answer("Действие недоступно", show_alert=True)


# ================= ВЫСТРЕЛЫ (ТЕКСТОВЫЕ КООРДИНАТЫ) =================
@dp.message(F.text)
async def on_shot(message: types.Message):
    game = get_active_game(message.from_user.id)
    if not game or game["status"] != "active":
        return  # обычный текст вне игры не трогаем
    coords = parse_coords(message.text)
    if not coords:
        await message.answer(
            "ℹ️ Формат координат: буква и число, например Б5 или Г-10 "
            "(буквы А–К, строки 1–10)."
        )
        return

    state = json.loads(game["state"])
    r, c = coords
    result, sunk = fire(state["bot"], r, c)
    if result == "repeat":
        await message.answer(f"ℹ️ {cell_name(r, c)} — сюда вы уже стреляли.")
        return

    events = []
    if result == "miss":
        events.append(f"🎯 {cell_name(r, c)} — мимо!")
    elif result == "hit":
        events.append(f"💥 {cell_name(r, c)} — попадание! Стреляйте ещё.")
    else:
        events.append(f"☠️ {cell_name(r, c)} — уничтожен {SHIP_NAMES[len(sunk)]}!")

    player_won = all_sunk(state["bot"])
    bot_lost_player = False

    if not player_won and result != "hit":
        await asyncio.sleep(1.0)
        events.append("🤖 Ход бота:")
        while not all_sunk(state["player"]):
            br, bc = bot_pick(state["player"])
            bres, bsunk = fire(state["player"], br, bc)
            if bres == "miss":
                events.append(f"🌊 {cell_name(br, bc)} — мимо")
                break
            if bres == "hit":
                events.append(f"💥 {cell_name(br, bc)} — попадание по вам!")
            else:
                events.append(f"☠️ {cell_name(br, bc)} — ваш {SHIP_NAMES[len(bsunk)]} уничтожен!")
        bot_lost_player = all_sunk(state["player"])

    if player_won:
        finish_game_state(message.from_user.id, won=True)
        save_game(game["game_id"], state, status="finished")
        await bot.edit_message_text(
            final_text(state, won=True, extra="\n".join(events)),
            chat_id=message.chat.id,
            message_id=game["message_id"],
            reply_markup=finished_kb(),
        )
    elif bot_lost_player:
        finish_game_state(message.from_user.id, won=False)
        save_game(game["game_id"], state, status="finished")
        await bot.edit_message_text(
            final_text(state, won=False, extra="\n".join(events)),
            chat_id=message.chat.id,
            message_id=game["message_id"],
            reply_markup=finished_kb(),
        )
    else:
        save_game(game["game_id"], state)
        await bot.edit_message_text(
            game_text(state, "\n".join(events)),
            chat_id=message.chat.id,
            message_id=game["message_id"],
            reply_markup=battle_kb(),
        )


async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

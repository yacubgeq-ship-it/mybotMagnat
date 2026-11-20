# -*- coding: utf-8 -*-
"""
SAILIX MAGNAT — «Морской бой».
Telegram-бот на aiogram 3: игра с ботом и PvP через вызов в группе.
Ручная расстановка кликами по полю, ставки, рейтинг, админ-панель.
"""
import os
import sqlite3
import json
import random
import re
import asyncio
from datetime import datetime

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================= НАСТРОЙКИ (ЧИТАЮТСЯ С САЙТА RAILWAY) =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not BOT_TOKEN:
    raise ValueError("Ошибка: переменная окружения BOT_TOKEN не найдена! Проверь настройки в Railway.")
if ADMIN_ID == 0:
    print("⚠️ Внимание: ADMIN_ID не установлен. Админ-панель будет недоступна.")

DB_PATH = "bot_pay.db"
BET = 100          # ставка за бой
WIN_REWARD = 300   # выплата победителю
LETTERS = "АБВГДЕЖЗИК"
LATIN = "ABCDEFGHIJ"
FLEET = {4: 1, 3: 2, 2: 3, 1: 4}   # размер корабля: сколько штук

# Символы поля (моноширинные, выравнивание не плывёт)
S_UNKNOWN = "·"   # неизвестная клетка
S_SHIP = "■"      # целый корабль (виден только на своём поле)
S_HIT = "×"       # попадание
S_DEAD = "✖"      # убитый корабль
S_MISS = "•"      # промах

# ================= БАЗА ДАННЫХ =================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        coins INTEGER DEFAULT 1000,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS games (
        game_id INTEGER PRIMARY KEY AUTOINCREMENT,
        mode TEXT,
        p1 INTEGER,
        p2 INTEGER,
        bet INTEGER,
        state TEXT,
        status TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS challenges (
        chat_id INTEGER,
        message_id INTEGER,
        p1 INTEGER,
        status TEXT
    )""")
    conn.commit()
    conn.close()

def get_user(user_id, username=None):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        c.execute("INSERT INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
        conn.commit()
        c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = c.fetchone()
    conn.close()
    return dict(row)

def save_user(user_id, **kw):
    if not kw:
        return
    conn = db()
    c = conn.cursor()
    sets = ", ".join(f"{k}=?" for k in kw)
    c.execute(f"UPDATE users SET {sets} WHERE user_id=?", (*kw.values(), user_id))
    conn.commit()
    conn.close()

def create_game(mode, p1, p2, bet):
    conn = db()
    c = conn.cursor()
    c.execute("INSERT INTO games (mode, p1, p2, bet, state, status) VALUES (?, ?, ?, ?, ?, ?)",
              (mode, p1, p2, bet, json.dumps({}, ensure_ascii=False), "placing"))
    conn.commit()
    gid = c.lastrowid
    conn.close()
    return gid

def save_game(gid, state, status=None):
    conn = db()
    c = conn.cursor()
    if status:
        c.execute("UPDATE games SET state=?, status=? WHERE game_id=?", (json.dumps(state, ensure_ascii=False), status, gid))
    else:
        c.execute("UPDATE games SET state=? WHERE game_id=?", (json.dumps(state, ensure_ascii=False), gid))
    conn.commit()
    conn.close()

def get_game(gid):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM games WHERE game_id=?", (gid,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    g = dict(row)
    g["state"] = json.loads(g["state"])
    return g

def get_active_game(user_id):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM games WHERE status='active' AND (p1=? OR p2=?)", (user_id, user_id))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    g = dict(row)
    g["state"] = json.loads(g["state"])
    return g

def get_pending_pvp(user_id):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM games WHERE status='placing' AND mode='pvp' AND (p1=? OR p2=?)", (user_id, user_id))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    g = dict(row)
    g["state"] = json.loads(g["state"])
    return g

def set_challenge_status(chat_id, message_id, status):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE challenges SET status=? WHERE chat_id=? AND message_id=?", (status, chat_id, message_id))
    conn.commit()
    conn.close()

def get_challenge(chat_id, message_id):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM challenges WHERE chat_id=? AND message_id=?", (chat_id, message_id))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

# ================= ЧИСТАЯ ИГРОВАЯ ЛОГИКА =================
def neighbors(cells):
    out = set()
    for r, c in cells:
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                out.add((r + dr, c + dc))
    return out

def field_map(side):
    m = {}
    for i, ship in enumerate(side["ships"]):
        for cell in ship:
            m[cell] = i
    return m

def can_place(cells, ships):
    for r, c in cells:
        if not (0 <= r < 10 and 0 <= c < 10):
            return False
    occ = set()
    for s in ships:
        occ.update(s)
    for cell in cells:
        if cell in occ:
            return False
    near = neighbors(cells)
    for s in ships:
        for cell in s:
            if cell in near:
                return False
    return True

def line_cells(a, b):
    (r1, c1), (r2, c2) = a, b
    if r1 == r2:
        return [(r1, c) for c in range(min(c1, c2), max(c1, c2) + 1)]
    if c1 == c2:
        return [(r, c1) for r in range(min(r1, r2), max(r1, r2) + 1)]
    return None

def random_fleet():
    ships = []
    for size in (4, 3, 2, 1):
        for _ in range(FLEET[size]):
            while True:
                horiz = random.random() < 0.5
                if horiz:
                    r = random.randint(0, 9)
                    c = random.randint(0, 9 - size + 1)
                    cells = [(r, c + i) for i in range(size)]
                else:
                    r = random.randint(0, 9 - size + 1)
                    c = random.randint(0, 9)
                    cells = [(r + i, c) for i in range(size)]
                if can_place(cells, ships):
                    ships.append(cells)
                    break
    return ships

def new_side(ships):
    return {"ships": ships, "hit": [], "miss": []}

def process_shot(side, r, c):
    """Возвращает ('repeat'|'miss'|'hit'|'dead', индекс_корабля или None)."""
    cell = (r, c)
    if cell in side["hit"] or cell in side["miss"]:
        return "repeat", None
    field = field_map(side)
    if cell in field:
        side["hit"].append(cell)
        idx = field[cell]
        if set(side["ships"][idx]) <= set(side["hit"]):
            return "dead", idx
        return "hit", None
    side["miss"].append(cell)
    return "miss", None

def is_fleet_dead(side):
    hits = set(side["hit"])
    for ship in side["ships"]:
        if not set(ship) <= hits:
            return False
    return True

def ships_alive(side):
    hits = set(side["hit"])
    n = 0
    for ship in side["ships"]:
        if not set(ship) <= hits:
            n += 1
    return n

def parse_coord(text):
    t = text.strip().upper().replace(" ", "").replace("-", "").replace(".", "")
    m = re.fullmatch(r"([А-ЯA-Z])(\d{1,2})", t)
    if not m:
        m = re.fullmatch(r"(\d{1,2})([А-ЯA-Z])", t)
        if not m:
            return None
        letter, num = m.group(2), int(m.group(1))
    else:
        letter, num = m.group(1), int(m.group(2))
    if letter in LETTERS:
        ci = LETTERS.index(letter)
    elif letter in LATIN:
        ci = LATIN.index(letter)
    else:
        return None
    r = num - 1
    if 0 <= r < 10 and 0 <= ci < 10:
        return (r, ci)
    return None

def render_board(side, reveal):
    """Поле 10×10 в моноширинном блоке. reveal=True — показывать свои корабли."""
    field = field_map(side)
    hits = set(side["hit"])
    miss = set(side["miss"])
    dead = set()
    for ship in side["ships"]:
        if set(ship) <= hits:
            dead.update(ship)
    lines = ["   " + " ".join(LETTERS)]
    for r in range(10):
        row = [f"{r + 1:>2}"]
        for c in range(10):
            cell = (r, c)
            if cell in hits:
                row.append(S_DEAD if cell in dead else S_HIT)
            elif cell in miss:
                row.append(S_MISS)
            elif reveal and cell in field:
                row.append(S_SHIP)
            else:
                row.append(S_UNKNOWN)
        lines.append(" ".join(row))
    return "\n".join(lines)

def ai_choose(side, ai_state):
    """Куда стрелять боту: сначала добивать раненое, потом шахматный поиск."""
    hits = set(side["hit"])
    miss = set(side["miss"])
    targets = [t for t in ai_state.get("targets", []) if t not in hits and t not in miss]
    if targets:
        return targets[0]
    candidates = [(r, c) for r in range(10) for c in range(10)
                  if (r, c) not in hits and (r, c) not in miss]
    parity = [x for x in candidates if (x[0] + x[1]) % 2 == 0]
    return random.choice(parity or candidates)

def ai_after_hit(side, ai_state, r, c):
    field = field_map(side)
    idx = field.get((r, c))
    if idx is None:
        return
    ship = side["ships"][idx]
    hits = set(side["hit"])
    ship_hits = [cell for cell in ship if cell in hits]
    targets = []
    if len(ship_hits) >= 2:
        ship_hits.sort()
        if ship_hits[0][0] == ship_hits[1][0]:
            dr, dc = 0, 1
        else:
            dr, dc = 1, 0
        ends = [(ship_hits[0][0] - dr, ship_hits[0][1] - dc),
                (ship_hits[-1][0] + dr, ship_hits[-1][1] + dc)]
        for cell in ends:
            if 0 <= cell[0] < 10 and 0 <= cell[1] < 10:
                targets.append(cell)
    else:
        for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            cell = (r + dr, c + dc)
            if 0 <= cell[0] < 10 and 0 <= cell[1] < 10:
                targets.append(cell)
    old = [t for t in ai_state.get("targets", []) if t not in targets]
    ai_state["targets"] = targets + old

def ai_prune_dead(side, ai_state, idx):
    ship = side["ships"][idx]
    near = neighbors(set(ship))
    hits = set(side["hit"])
    miss = set(side["miss"])
    ai_state["targets"] = [t for t in ai_state.get("targets", [])
                           if t not in near and t not in hits and t not in miss]

# ================= СОСТОЯНИЯ И КЛАВИАТУРЫ =================
class AdminStates(StatesGroup):
    amount = State()

placement_sessions = {}  # user_id -> сессия ручной расстановки

def main_menu(is_admin=False):
    kb = [[KeyboardButton(text="🎯 Играть"), KeyboardButton(text="👤 Профиль")],
          [KeyboardButton(text="🏆 Топ")]]
    if is_admin:
        kb.append([KeyboardButton(text="⚙️ Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def placement_grid_kb(session):
    placed = set()
    for s in session["placed"]:
        placed.update(s)
    start = tuple(session["start"]) if session.get("start") else None
    rows = []
    for r in range(10):
        row = []
        for c in range(10):
            cell = (r, c)
            if cell in placed:
                sym = "▣"
            elif start == cell:
                sym = "◉"
            else:
                sym = "·"
            row.append(InlineKeyboardButton(text=sym, callback_data=f"pl{r * 10 + c}"))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)

SHIP_ICONS = {1: "🚢", 2: "🚢🚢", 3: "🚢🚢🚢", 4: "🚢🚢🚢🚢"}

def ship_selector_kb(session):
    b = InlineKeyboardBuilder()
    if session.get("done"):
        b.button(text="⚔️ В бой!", callback_data="pfin")
        b.button(text="❌ Отмена", callback_data="pcan")
        b.adjust(1, 1)
        return b.as_markup()
    for size in (4, 3, 2, 1):
        left = session["remaining"][size]
        mark = " ✅" if left == 0 else (" 👈" if session.get("sel") == size else "")
        text = f"{SHIP_ICONS[size]} ×{FLEET[size]}{mark}" if left == FLEET[size] else f"{SHIP_ICONS[size]} осталось: {left}{mark}"
        b.button(text=text, callback_data="no" if left == 0 else f"sh{size}")
    b.button(text="❌ Отменить расстановку", callback_data="pcan")
    b.adjust(2, 2, 1)
    return b.as_markup()

def placement_text(session):
    lines = ["🚢 <b>Ручная расстановка</b>",
             "1️⃣ Выберите корабль внизу (иконка — размер).",
             "2️⃣ Кликните по полю: первая клетка — нос, вторая — корма (по одной линии).",
             "3️⃣ Одноклеточные ставятся одним нажатием.",
             "⚠️ Корабли не могут соприкасаться даже углами."]
    if session.get("start"):
        r, c = session["start"]
        lines.append(f"➡️ Начало выбрано: <b>{LETTERS[c]}{r + 1}</b>. Теперь выберите вторую клетку по линии.")
    return "\n".join(lines)

# ================= РЕНДЕР БОЯ =================
def battle_text(state, viewer):
    opp = "p2" if viewer == "p1" else "p1"
    txt = f"⚓ <b>Морской бой</b>: {state['names'][viewer]} против {state['names'][opp]}\n"
    txt += f"💰 Ставка: {BET} монет • Награда за победу: {WIN_REWARD}\n\n"
    txt += f"🎯 Флот противника:\n<pre>{render_board(state['sides'][opp], reveal=False)}</pre>\n"
    txt += f"🪖 Ваш флот:\n<pre>{render_board(state['sides'][viewer], reveal=True)}</pre>\n"
    txt += f"🚢 Кораблей на плаву: у вас {ships_alive(state['sides'][viewer])}, у противника {ships_alive(state['sides'][opp])}\n"
    if state["turn"] == viewer:
        txt += "\n🎯 <b>ВАШ ХОД</b> — отправьте координату, например: Б5"
    else:
        txt += "\n⏳ Ход противника, ждём..."
    return txt

SURR_KB = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏳️ Сдаться", callback_data="sur")]])

# ================= БОТ, ДИСПЕТЧЕР =================
dp = Dispatcher(storage=MemoryStorage())
bot = Bot(token=BOT_TOKEN)

# ================= ОБЩИЕ ХЕНДЛЕРЫ =================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.chat.type != "private":
        return
    get_user(message.from_user.id, message.from_user.username)
    await message.answer(
        f"⚓ Привет, {message.from_user.first_name}! Это «Морской бой» от SAILIX MAGNAT.\n\n"
        f"🎯 «Играть» — бой с ботом (ставка {BET}, победа +{WIN_REWARD}).\n"
        f"👥 PvP — в общем чате: наберите там «вызов», соперник нажмёт «Принять».\n"
        f"✋ Корабли можно расставить вручную, кликая по полю.",
        reply_markup=main_menu(message.from_user.id == ADMIN_ID))

@dp.message(F.text == "👤 Профиль", F.chat.type == "private")
async def show_profile(message: types.Message):
    u = get_user(message.from_user.id, message.from_user.username)
    total = u["wins"] + u["losses"]
    rate = round(u["wins"] * 100 / total) if total else 0
    await message.answer(
        f"👤 <b>Профиль адмирала</b>\n"
        f"🪙 Монеты: {u['coins']}\n"
        f"🏆 Победы: {u['wins']} • 💀 Поражения: {u['losses']}\n"
        f"📊 Винрейт: {rate}%",
        parse_mode="HTML")

@dp.message(F.text == "🏆 Топ", F.chat.type == "private")
async def show_top(message: types.Message):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT user_id, username, wins, losses, coins FROM users WHERE wins > 0 ORDER BY wins DESC, coins DESC LIMIT 10")
    rows = c.fetchall()
    conn.close()
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Топ-10 адмиралов</b>\n"]
    for i, row in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i + 1}."
        name = ("@" + row["username"]) if row["username"] else f"id{row['user_id']}"
        lines.append(f"{medal} {name} — {row['wins']} побед (🪙 {row['coins']})")
    if len(lines) == 1:
        lines.append("Пока пусто — станьте первым!")
    await message.answer("\n".join(lines), parse_mode="HTML")

@dp.message(F.text == "🎯 Играть", F.chat.type == "private")
async def play_menu(message: types.Message):
    active = get_active_game(message.from_user.id)
    if active:
        await message.answer("⚔️ У вас уже идёт бой! Продолжайте в сообщениях выше или нажмите «Сдаться» под ним.",
                             reply_markup=SURR_KB)
        return
    b = InlineKeyboardBuilder()
    b.button(text="🎲 С ботом: случайная расстановка", callback_data="g_rand")
    b.button(text="✋ С ботом: расставить вручную", callback_data="g_man")
    b.adjust(1, 1)
    await message.answer(
        f"⚓ Морской бой\nФлот: 1 линкор (4 кл), 2 крейсера (3 кл), 3 эсминца (2 кл), 4 катера (1 кл).\n"
        f"💰 Ставка {BET} списывается при старте боя, победа приносит +{WIN_REWARD}.\n\n"
        f"👥 PvP: добавьте бота в общий чат и наберите там «вызов» — соперник нажмёт «Принять», и вы сыграете один на один в боте.",
        reply_markup=b.as_markup())

# ================= РАССТАНОВКА =================
def begin_placement(user_id, kind, gid, side, chat_id, base_msg):
    session = {"kind": kind, "game_id": gid, "side": side, "chat_id": chat_id,
               "placed": [], "remaining": dict(FLEET), "sel": None, "start": None,
               "done": False, "base_msg": base_msg}
    placement_sessions[user_id] = session
    return session

def placement_complete(session):
    return sum(FLEET[s] - session["remaining"][s] for s in FLEET) == 10

@dp.callback_query(F.data.in_({"g_rand", "g_man"}))
async def cb_start_bot_game(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if get_active_game(uid):
        await callback.answer("Сначала завершите текущий бой!", show_alert=True)
        return
    placement_sessions.pop(uid, None)
    gid = create_game("bot", uid, 0, BET)
    state = {"mode": "bot", "p1": uid, "p2": 0, "bet": BET,
             "names": {"p1": "@" + (callback.from_user.username or str(uid)), "p2": "🤖 Бот"},
             "sides": {"p1": new_side([]), "p2": new_side(random_fleet())},
             "turn": "p1", "msgs": {}, "chats": {uid: uid}, "ai": {"targets": []}}
    save_game(gid, state)
    if callback.data == "g_rand":
        state["sides"]["p1"] = new_side(random_fleet())
        save_game(gid, state)
        await callback.message.edit_text("🚢 Флот расставлен автоматически.\nНажмите «В бой!», когда будете готовы.", reply_markup=None)
        await callback.message.answer("🪖 Ваш флот:\n<pre>" + render_board(state["sides"]["p1"], reveal=True) + "</pre>", parse_mode="HTML")
        b = InlineKeyboardBuilder()
        b.button(text="⚔️ В бой!", callback_data=f"bfin{gid}")
        b.button(text="❌ Отмена", callback_data="bcancel")
        b.adjust(1, 1)
        await callback.message.answer("К бою готовы? Ставка списывается при старте.", reply_markup=b.as_markup())
    else:
        session = begin_placement(uid, "bot", gid, "p1", uid, callback.message.message_id)
        grid = await callback.message.answer(placement_text(session), parse_mode="HTML", reply_markup=placement_grid_kb(session))
        sel = await callback.message.answer("Выберите корабль:", reply_markup=ship_selector_kb(session))
        session["grid_msg_id"] = grid.message_id
        session["sel_msg_id"] = sel.message_id
    await callback.answer()

@dp.callback_query(F.data.in_({"prand", "pman"}))
async def cb_start_pvp_placement(callback: types.CallbackQuery):
    uid = callback.from_user.id
    g = get_pending_pvp(uid)
    if not g:
        await callback.answer("Ожидаемая партия не найдена — попросите соперника прислать вызов заново.", show_alert=True)
        return
    placement_sessions.pop(uid, None)
    side = "p1" if g["p1"] == uid else "p2"
    state = g["state"]
    state.setdefault("placed", {})
    if callback.data == "prand":
        state["sides"][side] = new_side(random_fleet())
        state["placed"][side] = True
        save_game(g["game_id"], state)
        await callback.message.answer("🪖 Ваш флот:\n<pre>" + render_board(state["sides"][side], reveal=True) + "</pre>", parse_mode="HTML")
        if all(state["placed"].get(s) for s in ("p1", "p2")):
            await try_start_pvp_battle(g["game_id"], bot)
        else:
            await callback.message.answer("⏳ Ждём, пока соперник расставит флот...")
    else:
        session = begin_placement(uid, "pvp", g["game_id"], side, uid, callback.message.message_id)
        grid = await callback.message.answer(placement_text(session), parse_mode="HTML", reply_markup=placement_grid_kb(session))
        sel = await callback.message.answer("Выберите корабль:", reply_markup=ship_selector_kb(session))
        session["grid_msg_id"] = grid.message_id
        session["sel_msg_id"] = sel.message_id
    await callback.answer()

@dp.callback_query(F.data.startswith("sh"))
async def cb_ship_select(callback: types.CallbackQuery):
    uid = callback.from_user.id
    session = placement_sessions.get(uid)
    if not session:
        await callback.answer("Сессия расстановки не найдена. Начните заново: /start", show_alert=True)
        return
    size = int(callback.data[2:])
    if session["remaining"][size] == 0:
        await callback.answer("Корабли этого типа уже расставлены.", show_alert=True)
        return
    session["sel"] = size
    session["start"] = None
    await callback.message.edit_reply_markup(reply_markup=ship_selector_kb(session))
    try:
        await bot.edit_message_text(chat_id=uid, message_id=session["grid_msg_id"],
                                    text=placement_text(session), parse_mode="HTML",
                                    reply_markup=placement_grid_kb(session))
    except Exception:
        pass
    await callback.answer(f"Выбран корабль на {size} {'клетку' if size == 1 else 'клетки'}. Кликайте по полю!")

@dp.callback_query(F.data.startswith("pl"))
async def cb_place_cell(callback: types.CallbackQuery):
    uid = callback.from_user.id
    session = placement_sessions.get(uid)
    if not session:
        await callback.answer("Сессия расстановки не найдена. Начните заново: /start", show_alert=True)
        return
    idx = int(callback.data[2:])
    r, c = idx // 10, idx % 10
    cell = (r, c)
    if session["done"]:
        await callback.answer("Флот уже расставлен — жмите «В бой!»", show_alert=True)
        return
    if session["remaining"].get(session["sel"], 0) == 0:
        await callback.answer("❌ Сначала выберите корабль внизу списка!", show_alert=True)
        return
    size = session["sel"]
    if size == 1:
        cells = [cell]
    else:
        if session["start"] is None:
            session["start"] = cell
            try:
                await bot.edit_message_text(chat_id=uid, message_id=session["grid_msg_id"],
                                            text=placement_text(session), parse_mode="HTML",
                                            reply_markup=placement_grid_kb(session))
            except Exception:
                pass
            await callback.answer(f"Нос выбран ({LETTERS[c]}{r + 1}). Теперь выберите корму по линии.")
            return
        cells = line_cells(tuple(session["start"]), cell)
        if cells is None:
            await callback.answer("❌ Корабль ставится строго по горизонтали или вертикали!", show_alert=True)
            return
        if len(cells) != size:
            await callback.answer(f"❌ Длина должна быть ровно {size} {'клетка' if size == 1 else f'{size} клетки'}! Выбрано {len(cells)}.", show_alert=True)
            return
    if not can_place(cells, session["placed"]):
        await callback.answer("❌ Здесь нельзя ставить: клетки заняты или корабли соприкасаются (даже углами)!", show_alert=True)
        return
    session["placed"].append(cells)
    session["remaining"][size] -= 1
    session["sel"] = None
    session["start"] = None
    placed_count = 10 - sum(session["remaining"].values())
    try:
        await bot.edit_message_text(chat_id=uid, message_id=session["grid_msg_id"],
                                    text=placement_text(session) + f"\n\n✅ Расставлено кораблей: {placed_count}/10",
                                    parse_mode="HTML", reply_markup=placement_grid_kb(session))
        await bot.edit_message_reply_markup(chat_id=uid, message_id=session["sel_msg_id"],
                                            reply_markup=ship_selector_kb(session))
    except Exception:
        pass
    if placement_complete(session):
        session["done"] = True
        placement_sessions.pop(uid, None)
        # зафиксировать флот
        if session["kind"] == "bot":
            g = get_game(session["game_id"])
            g["state"]["sides"]["p1"] = new_side(session["placed"])
            save_game(g["game_id"], g["state"])
            await bot.edit_message_reply_markup(chat_id=uid, message_id=session["sel_msg_id"],
                                                reply_markup=ship_selector_kb({"done": True}))
        else:
            g = get_game(session["game_id"])
            side = session["side"]
            g["state"]["sides"][side] = new_side(session["placed"])
            g["state"].setdefault("placed", {})[side] = True
            save_game(g["game_id"], g["state"])
            await bot.edit_message_reply_markup(chat_id=uid, message_id=session["sel_msg_id"],
                                                reply_markup=ship_selector_kb({"done": True}))
            if all(g["state"]["placed"].get(s) for s in ("p1", "p2")):
                await try_start_pvp_battle(g["game_id"], bot)
            else:
                await bot.send_message(uid, "✅ Ваш флот готов! Ждём, пока соперник расставит свой...")
    else:
        await callback.answer(f"✅ Корабль поставлен! Осталось: {10 - placed_count}")

@dp.callback_query(F.data == "pcan")
async def cb_cancel_placement(callback: types.CallbackQuery):
    uid = callback.from_user.id
    session = placement_sessions.pop(uid, None)
    if session and session.get("grid_msg_id"):
        try:
            await bot.delete_message(uid, session["grid_msg_id"])
            await bot.delete_message(uid, session["sel_msg_id"])
        except Exception:
            pass
    if session and session["kind"] == "pvp":
        # откладывать игру нельзя — соперник ждёт; вернёмся к выбору расстановки
        g = get_pending_pvp(uid)
        if g:
            await callback.message.answer("Расстановка отменена. Выберите заново:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎲 Случайная расстановка", callback_data="prand")],
                [InlineKeyboardButton(text="✋ Вручную", callback_data="pman")]]))
            return
    await callback.message.answer("Главное меню:", reply_markup=main_menu(uid == ADMIN_ID))
    await callback.answer("Расстановка отменена")

@dp.callback_query(F.data.startswith("bfin"))
async def cb_fight_bot(callback: types.CallbackQuery):
    uid = callback.from_user.id
    gid = int(callback.data[4:])
    g = get_game(gid)
    if not g or g["status"] != "placing":
        await callback.answer("Игра уже неактуальна.", show_alert=True)
        return
    u = get_user(uid)
    if u["coins"] < BET:
        await callback.answer(f"❌ Не хватает монет: нужно {BET}, у вас {u['coins']}.", show_alert=True)
        return
    save_user(uid, coins=u["coins"] - BET)
    state = g["state"]
    state["turn"] = random.choice(["p1", "p2"])
    save_game(gid, state, "active")
    try:
        await callback.message.delete()
    except Exception:
        pass
    msg = await bot.send_message(uid, battle_text(state, "p1"), parse_mode="HTML", reply_markup=SURR_KB)
    state["msgs"]["p1"] = msg.message_id
    save_game(gid, state)
    if state["turn"] == "p2":
        await asyncio.sleep(1.0)
        await bot_moves(gid, state)
    await callback.answer()

@dp.callback_query(F.data == "bcancel")
async def cb_cancel_bot_game(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.message.answer("Главное меню:", reply_markup=main_menu(callback.from_user.id == ADMIN_ID))
    await callback.answer("Отменено")

# ================= СТАРТ PvP-БОЯ =================
async def try_start_pvp_battle(gid, bot_obj):
    g = get_game(gid)
    state = g["state"]
    u1 = get_user(g["p1"])
    u2 = get_user(g["p2"])
    if u1["coins"] < BET or u2["coins"] < BET:
        poor = [u["user_id"] for u in (u1, u2) if u["coins"] < BET]
        for uid in (g["p1"], g["p2"]):
            try:
                await bot_obj.send_message(uid, "❌ Бой не состоялся: у одного из игроков не хватает монет (нужно 100).")
            except Exception:
                pass
        conn = db()
        c = conn.cursor()
        c.execute("UPDATE games SET status='done' WHERE game_id=?", (gid,))
        conn.commit()
        conn.close()
        return
    save_user(u1["user_id"], coins=u1["coins"] - BET)
    save_user(u2["user_id"], coins=u2["coins"] - BET)
    state["turn"] = random.choice(["p1", "p2"])
    state["chats"] = {g["p1"]: g["p1"], g["p2"]: g["p2"]}
    save_game(gid, state, "active")
    for side, uid in (("p1", g["p1"]), ("p2", g["p2"])):
        try:
            msg = await bot_obj.send_message(uid, battle_text(state, side), parse_mode="HTML", reply_markup=SURR_KB)
            state.setdefault("msgs", {})[side] = msg.message_id
        except Exception:
            pass
    save_game(gid, state)

# ================= ХОДЫ =================
def apply_shot(state, shooter, r, c):
    """Выстрел shooter по противнику. Возвращает (результат, игра_завершена)."""
    opp = "p2" if shooter == "p1" else "p1"
    result, idx = process_shot(state["sides"][opp], r, c)
    finished = False
    if result == "dead":
        if state.get("ai") is not None and opp == "p1":
            ai_prune_dead(state["sides"]["p1"], state["ai"], idx)
    if result != "repeat" and is_fleet_dead(state["sides"][opp]):
        finished = True
    return result, finished

async def finish_game(gid, state, winner, loser, bot_obj):
    w_user = get_user(state["p1"] if winner == "p1" else state["p2"])
    l_user = get_user(state["p1"] if loser == "p1" else state["p2"])
    save_user(w_user["user_id"], coins=w_user["coins"] + WIN_REWARD, wins=w_user["wins"] + 1)
    save_user(l_user["user_id"], losses=l_user["losses"] + 1)
    state["winner"] = winner
    save_game(gid, state, "done")
    placement_sessions.pop(w_user["user_id"], None)
    placement_sessions.pop(l_user["user_id"], None)
    win_txt = f"🏆 <b>ПОБЕДА!</b> Флот противника уничтожен. Награда: +{WIN_REWARD} монет."
    lose_txt = f"💀 <b>Поражение.</b> Ваш флот уничтожен. Ставка ({BET}) потеряна."
    for side, uid, text in (("p1", state["p1"], win_txt if winner == "p1" else lose_txt),
                            ("p2", state["p2"], win_txt if winner == "p2" else lose_txt)):
        if uid:
            try:
                await bot_obj.edit_message_text(chat_id=state["chats"].get(uid, uid),
                                                message_id=state["msgs"].get(side),
                                                text=battle_text(state, side) + "\n\n" + text,
                                                parse_mode="HTML", reply_markup=None)
            except Exception:
                pass

async def bot_moves(gid, state):
    ai_state = state.setdefault("ai", {"targets": []})
    while True:
        cell = ai_choose(state["sides"]["p1"], ai_state)
        result, idx = process_shot(state["sides"]["p1"], *cell)
        if result == "dead":
            ai_prune_dead(state["sides"]["p1"], ai_state, idx)
        elif result == "hit":
            ai_after_hit(state["sides"]["p1"], ai_state, *cell)
        if is_fleet_dead(state["sides"]["p1"]):
            await finish_game(gid, state, "p2", "p1", bot)
            return
        if result in ("miss", "repeat"):
            break
        await asyncio.sleep(1.2)
    state["turn"] = "p1"
    save_game(gid, state)
    try:
        await bot.edit_message_text(chat_id=state["chats"][state["p1"]],
                                    message_id=state["msgs"]["p1"],
                                    text=battle_text(state, "p1"), parse_mode="HTML", reply_markup=SURR_KB)
    except Exception:
        pass

@dp.message(Command("сдаться"), F.chat.type == "private")
async def cmd_surrender(message: types.Message):
    await do_surrender(message.from_user.id, message)

async def do_surrender(uid, target):
    g = get_active_game(uid)
    if not g:
        await target.answer("У вас нет активного боя.")
        return
    state = g["state"]
    loser = "p1" if g["p1"] == uid else "p2"
    winner = "p2" if loser == "p1" else "p1"
    await finish_game(g["game_id"], state, winner, loser, bot)

@dp.message(AdminStates.amount, F.chat.type == "private")
async def adm_give_amount(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    txt = message.text.strip()
    if txt.lower() == "отмена":
        await state.clear()
        await message.answer("Отменено.")
        return
    if not txt.isdigit():
        await message.answer("❌ Нужно целое число, например: 5000")
        return
    amount = int(txt)
    if amount <= 0:
        await message.answer("❌ Сумма должна быть больше нуля.")
        return
    u = get_user(ADMIN_ID)
    save_user(ADMIN_ID, coins=u["coins"] + amount)
    await state.clear()
    await message.answer(f"✅ Начислено {amount} монет. Новый баланс: {u['coins'] + amount} 🪙")


@dp.message(F.chat.type == "private", F.text)
async def text_handler(message: types.Message):
    # сначала админ-ввод суммы
    # затем координаты активного боя
    coord = parse_coord(message.text)
    if coord:
        g = get_active_game(message.from_user.id)
        if g:
            await handle_shot(message, g, coord)
            return
    await message.answer(
        "🤔 Не понял команду. Нажмите кнопку меню или отправьте координату вида «Б5», если идёт бой.")

async def handle_shot(message: types.Message, g, coord):
    uid = message.from_user.id
    state = g["state"]
    shooter = "p1" if g["p1"] == uid else "p2"
    if state["turn"] != shooter:
        await message.answer("⏳ Сейчас не ваш ход — ждите.")
        return
    r, c = coord
    result, finished = apply_shot(state, shooter, r, c)
    if result == "repeat":
        await message.answer("Вы уже стреляли в эту клетку. Выберите другую.")
        return
    if result == "miss":
        state["turn"] = "p2" if shooter == "p1" else "p1"
    save_game(g["game_id"], state)
    # обновить сообщение стреляющего
    try:
        await bot.edit_message_text(chat_id=uid, message_id=state["msgs"][shooter],
                                    text=battle_text(state, shooter), parse_mode="HTML", reply_markup=SURR_KB)
    except Exception:
        pass
    if finished:
        await finish_game(g["game_id"], state, shooter, "p2" if shooter == "p1" else "p1", bot)
        return
    if result in ("hit", "dead"):
        await message.answer("🔥 Есть попадание! Стреляйте ещё.")
        # в PvP обновить и сообщение соперника
        if state["mode"] == "pvp":
            opp_uid = g["p2"] if shooter == "p1" else g["p1"]
            try:
                await bot.edit_message_text(chat_id=opp_uid, message_id=state["msgs"]["p2" if shooter == "p1" else "p1"],
                                            text=battle_text(state, "p2" if shooter == "p1" else "p1"),
                                            parse_mode="HTML", reply_markup=SURR_KB)
            except Exception:
                pass
        return
    # промах: ход переходит
    if state["mode"] == "pvp":
        opp_uid = g["p2"] if shooter == "p1" else g["p1"]
        try:
            await bot.edit_message_text(chat_id=opp_uid, message_id=state["msgs"]["p2" if shooter == "p1" else "p1"],
                                        text=battle_text(state, "p2" if shooter == "p1" else "p1"),
                                        parse_mode="HTML", reply_markup=SURR_KB)
        except Exception:
            pass
        await message.answer("🌊 Промах! Ход переходит сопернику.")
    else:
        await message.answer("🌊 Промах! Ход бота...")
        await asyncio.sleep(1.0)
        await bot_moves(g["game_id"], state)

@dp.callback_query(F.data == "sur")
async def cb_surrender(callback: types.CallbackQuery):
    await do_surrender(callback.from_user.id, callback.message)
    await callback.answer("Вы сдались")

# ================= PvP: ВЫЗОВ В ГРУППЕ =================
@dp.message(Command("duel"), F.chat.type.in_({"group", "supergroup"}))
async def group_duel_cmd(message: types.Message):
    await make_challenge(message)

@dp.message(F.chat.type.in_({"group", "supergroup"}), F.text)
async def group_text(message: types.Message):
    t = (message.text or "").strip().lower()
    if t in ("вызов", "⚔️ вызов", "duel"):
        await make_challenge(message)

async def make_challenge(message: types.Message):
    uid = message.from_user.id
    get_user(uid, message.from_user.username)
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM challenges WHERE chat_id=? AND p1=? AND status='open'", (message.chat.id, uid))
    if c.fetchone():
        conn.close()
        await message.answer("Вы уже создали вызов — дождитесь соперника.")
        return
    conn.close()
    if get_active_game(uid) or get_pending_pvp(uid):
        await message.answer("Сначала завершите текущий бой!")
        return
    name = "@" + message.from_user.username if message.from_user.username else message.from_user.first_name
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🤝 Принять вызов", callback_data="acc")]])
    msg = await message.answer(
        f"⚓ <b>Морской бой!</b>\n{name} вызывает соперника!\n"
        f"💰 Ставка: {BET} монет • Победа: +{WIN_REWARD}\n"
        f"Жмите «Принять вызов» — и бой продолжится в личке с ботом.",
        parse_mode="HTML", reply_markup=kb)
    conn = db()
    c = conn.cursor()
    c.execute("INSERT INTO challenges (chat_id, message_id, p1, status) VALUES (?, ?, ?, 'open')",
              (message.chat.id, msg.message_id, uid))
    conn.commit()
    conn.close()

@dp.callback_query(F.data == "acc", F.message.chat.type.in_({"group", "supergroup"}))
async def cb_accept(callback: types.CallbackQuery):
    ch = get_challenge(callback.message.chat.id, callback.message.message_id)
    if not ch or ch["status"] != "open":
        await callback.answer("Этот вызов уже неактуален.", show_alert=True)
        return
    uid = callback.from_user.id
    if uid == ch["p1"]:
        await callback.answer("Нельзя принимать свой вызов 🙂", show_alert=True)
        return
    if get_active_game(uid) or get_pending_pvp(uid):
        await callback.answer("Сначала завершите текущий бой!", show_alert=True)
        return
    if get_user(uid)["coins"] < BET:
        await callback.answer(f"Не хватает монет: нужно {BET}. Пополните счёт — сыграем!", show_alert=True)
        return
    p1_name = get_user(ch["p1"])
    p1_name = ("@" + p1_name["username"]) if p1_name["username"] else f"id{ch['p1']}"
    p2_name = ("@" + callback.from_user.username) if callback.from_user.username else callback.from_user.first_name
    set_challenge_status(callback.message.chat.id, callback.message.message_id, "used")
    gid = create_game("pvp", ch["p1"], uid, BET)
    state = {"mode": "pvp", "p1": ch["p1"], "p2": uid, "bet": BET,
             "names": {"p1": p1_name, "p2": p2_name},
             "sides": {"p1": new_side([]), "p2": new_side([])},
             "placed": {}, "turn": "p1", "msgs": {}, "chats": {}}
    save_game(gid, state)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Случайная расстановка", callback_data="prand")],
        [InlineKeyboardButton(text="✋ Вручную", callback_data="pman")]])
    ok1 = ok2 = False
    try:
        await bot.send_message(ch["p1"], f"🤝 {p2_name} принял ваш вызов!\nРасставьте флот:", reply_markup=kb)
        ok1 = True
    except Exception:
        pass
    try:
        await bot.send_message(uid, f"⚓ Вы приняли вызов {p1_name}!\nРасставьте флот:", reply_markup=kb)
        ok2 = True
    except Exception:
        pass
    if not (ok1 and ok2):
        conn = db()
        c = conn.cursor()
        c.execute("UPDATE games SET status='done' WHERE game_id=?", (gid,))
        set_challenge_status(callback.message.chat.id, callback.message.message_id, "open")
        conn.commit()
        conn.close()
        who = p1_name if not ok1 else p2_name
        await callback.message.edit_text(f"❌ {who} не может получить сообщение от бота — пусть откроет бота и нажмёт /start, потом создайте вызов заново.")
        await callback.answer()
        return
    await callback.message.edit_text(
        f"🤝 {p2_name} принял вызов {p1_name}!\nОбоим игрокам написали в личку — расставляйте флоты и бойтесь торпед 🌊")
    await callback.answer("Вызов принят! Проверьте личку с ботом.")

@dp.callback_query(F.data == "no")
async def cb_no(callback: types.CallbackQuery):
    await callback.answer("Эти корабли уже расставлены.", show_alert=True)

# ================= АДМИН-ПАНЕЛЬ =================
@dp.message(F.text == "⚙️ Админ-панель", F.chat.type == "private")
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ запрещён! Вы не администратор.")
        return
    b = InlineKeyboardBuilder()
    b.button(text="📊 Статистика", callback_data="adm_stats")
    b.button(text="💵 Начислить монеты себе", callback_data="adm_give")
    b.button(text="📢 Рассылка (в разработке)", callback_data="no")
    b.button(text="🔙 Назад", callback_data="back_main")
    b.adjust(1)
    await message.answer("⚙️ <b>Панель администратора</b>", parse_mode="HTML", reply_markup=b.as_markup())

@dp.callback_query(F.data == "adm_stats")
async def adm_stats(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    conn = db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*), COALESCE(SUM(coins),0), COALESCE(SUM(wins),0) FROM users")
    n, coins, wins = c.fetchone()
    conn.close()
    await callback.answer(f"📊 Игроков: {n} • Монет в экономике: {coins} • Побед всего: {wins}", show_alert=True)

@dp.callback_query(F.data == "adm_give")
async def adm_give_start(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminStates.amount)
    await callback.message.answer("💵 Введите сумму монет для начисления себе (например: 5000). Для отмены напишите «отмена».")
    await callback.answer()

@dp.callback_query(F.data == "back_main")
async def back_to_main(callback: types.CallbackQuery):
    await callback.message.answer("Главное меню:", reply_markup=main_menu(callback.from_user.id == ADMIN_ID))
    await callback.answer()

# ================= ЗАПУСК =================
async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

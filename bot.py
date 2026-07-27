import os
import asyncio
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone, date

from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext


# =========================
# ADMIN (видит статистику)
# =========================
ADMIN_ID = 862407613

# =========================
# CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN or " " in BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set correctly (empty or contains spaces)")

# Example: ADMIN_IDS="123,456"
ADMIN_IDS = {ADMIN_ID}
_raw_admins = os.getenv("ADMIN_IDS", "").strip()
if _raw_admins:
    for part in _raw_admins.split(","):
        part = part.strip()
        if part.isdigit():
            ADMIN_IDS.add(int(part))

# timezone for reminders
TZ_OFFSET_HOURS = int(os.getenv("TZ_OFFSET_HOURS", "3"))
TZ = timezone(timedelta(hours=TZ_OFFSET_HOURS))

# Reminder time (local TZ)
REMINDER_HOUR = int(os.getenv("REMINDER_HOUR", "10"))
REMINDER_MIN = int(os.getenv("REMINDER_MIN", "0"))

DB_PATH = os.getenv("DB_PATH", "bot.db")


# =========================
# CARDS (10)
# =========================
CARDS = [
    {
        "id": "quiet_forest",
        "title": "Тихий лес",
        "text": "Иногда самый верный шаг, это замедлиться и почувствовать то, что долго оставалось без внимания.",
        "question": "Где в твоей жизни сейчас больше всего нужна тишина?",
    },
    {
        "id": "warm_light",
        "title": "Тёплый свет",
        "text": "Даже маленький свет — это знак: тебе есть куда вернуться.",
        "question": "Где твоя точка света сегодня?",
    },
    {
        "id": "pocket_key",
        "title": "Ключ в кармане",
        "text": "Ресурс часто ближе, чем кажется. Он не громкий. Он твой.",
        "question": "Какой ресурс ты недооцениваешь?",
    },
    {
        "id": "road_step",
        "title": "Дорога",
        "text": "Не обязательно знать весь путь. Достаточно следующего шага.",
        "question": "Какой следующий шаг возможен сегодня?",
    },
    {
        "id": "tea_pause",
        "title": "Чашка чая",
        "text": "Не обязательно нужно что-то большое. Небольшая пауза может стать заботой о себе.",
        "question": "Что ты можешь сделать для себя сегодня?",
    },
    {
        "id": "soft_fog",
        "title": "Лёгкий туман",
        "text": "Когда всё размыто — это не ошибка и не провал. Это пространство, где появляется новый ориентир.",
        "question": "Какое направление может быть твоим ориентиром?",
    },
    {
        "id": "soft_blanket",
        "title": "Мягкий плед",
        "text": "Тепло помогает почувствовать безопасность. Возможно, нужно дать себе немного покоя и бережно отнестись к своим границам.",
        "question": "Где тебе сейчас нужна граница, чтобы сохранить внутреннее равновесие?",
    },
    {
        "id": "lantern",
        "title": "Фонарь",
        "text": "Тебе не нужно освещать весь путь. Достаточно подсветить один шаг — и тело выдохнет.",
        "question": "Какой шаг ты готов(а) подсветить прямо сейчас?",
    },
    {
        "id": "quiet_garden",
        "title": "Сад",
        "text": "Рост бывает тихим. Ты уже делаешь больше, чем иногда замечаешь.",
        "question": "Что в тебе уже изменилось — даже если это пока почти незаметно?",
    },
    {
        "id": "letter_self",
        "title": "Письмо себе",
        "text": "То, как ты говоришь с собой, — это твоя внутренняя атмосфера. Её можно менять.",
        "question": "Какая одна фраза поддержки тебе нужна сегодня?",
    },
]
CARD_BY_ID = {c["id"]: c for c in CARDS}


# =========================
# DEEPER QUESTIONS
# =========================
PRE_QUESTION_TEXTS = [
    "🫧 Обрати внимание на то, что в этой карте откликается тебе больше всего.",
    "🫧 Позволь себе заметить первое, что откликнулось в этой карте.",
    "🫧 Посмотри на карту и отметь то, что привлекает твоё внимание.",
    "🫧 Заметь, что в этой карте откликается тебе сильнее всего.",
]

OBSERVATION_QUESTIONS = [
    "Что на этой карте притягивает твоё внимание?",
    "Какая деталь на карте кажется самой важной?",
    "Что в этом образе первым привлекло твоё внимание?",
    "Какая часть изображения откликается тебе сильнее всего?",
    "Есть ли на карте место, где тебе хотелось бы оказаться?",
    "Что на этой карте кажется самым спокойным?",
    "Какая деталь на карте вызывает у тебя эмоциональный отклик?",
    "Что в этом образе кажется самым близким для тебя?",
    "Какой посыл этой карты кажется тебе самым значимым?",
    "Есть ли на карте что-то, что напоминает о твоей жизни?",
]

RESOURCE_QUESTIONS = [
    "Что на этой карте может быть для тебя ресурсом?",
    "Что на карте кажется поддерживающим?",
    "Что в этом образе можно взять с собой в сегодняшний день?",
    "Где на карте есть ощущение спокойствия?",
    "Какая часть этого образа может дать тебе опору?",
    "Что в этой карте напоминает о твоей силе?",
    "Есть ли на карте что-то, что помогает тебе выдохнуть?",
    "Какая деталь в этом образе кажется тебе самой близкой?",
    "Что на этой карте может поддержать тебя сегодня?",
    "Где в этом образе ощущается устойчивость?",
]

LIFE_TRANSFER_QUESTIONS = [
    "Что эта карта может подсказать тебе о сегодняшнем дне?",
    "Как этот образ может быть связан с твоим запросом?",
    "Есть ли в твоей жизни что-то похожее на этот образ?",
    "О чём эта карта напоминает тебе сегодня?",
    "Какое небольшое действие может появиться после этой карты?",
    "Что в твоей жизни сейчас откликается этому образу?",
    "Если бы эта карта могла дать тебе совет — каким бы он был?",
    "Что ты возьмёшь с собой из этого образа?",
    "На что этот образ помогает тебе посмотреть по-новому?",
    "Как эта карта может поддержать тебя сегодня?",
]

PRACTICES = [
    "🌿 *Небольшая практика*\n\nСделай медленный вдох.\nЕщё раз посмотри на карту и заметь деталь, которая кажется самой спокойной.\nПобудь с этим ощущением несколько секунд.",
    "🌿 *Небольшая практика*\n\nПредставь, что ты находишься внутри этого образа.\nГде на карте тебе было бы спокойнее всего?\nПобудь там мысленно несколько секунд.",
    "🌿 *Небольшая практика*\n\nПосмотри на карту ещё раз.\nНайди на ней то, что кажется поддерживающим.\nПредставь, что это ощущение можно взять с собой в сегодняшний день.",
    "🌿 *Небольшая практика*\n\nПосмотри на карту и задай себе вопрос:\n*Какой маленький шаг сегодня может поддержать меня?*\nЗаметь первую мысль, которая приходит.",
    "🌿 *Небольшая практика*\n\nСделай медленный вдох и выдох.\nПосмотри на карту ещё раз.\nЗаметь, какое чувство появляется внутри.",
]

FINAL_REFLECTION_TEXT = (
    "🌿\n\n"
    "То, что откликается, часто и есть нужное направление.\n\n"
    "Можно вернуться к этой карте позже\n"
    "или посмотреть, что откроется дальше."
)


# =========================
# DB
# =========================
def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def db_init():
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        first_seen INTEGER NOT NULL,
        last_seen INTEGER NOT NULL,
        visits INTEGER NOT NULL DEFAULT 0,
        returns INTEGER NOT NULL DEFAULT 0,
        subscribed INTEGER NOT NULL DEFAULT 0,
        last_daily_sent TEXT,
        last_weekly_state_week TEXT,
        last_card_id TEXT,
        daily_card_date TEXT
    )
    """)
    try:
        cur.execute("ALTER TABLE users ADD COLUMN daily_card_date TEXT")
    except sqlite3.OperationalError:
        pass

    cur.execute("""
    CREATE TABLE IF NOT EXISTS favorites (
        user_id INTEGER NOT NULL,
        card_id TEXT NOT NULL,
        added_at INTEGER NOT NULL,
        PRIMARY KEY (user_id, card_id, added_at)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS weekly_state (
        user_id INTEGER NOT NULL,
        week_key TEXT NOT NULL,
        state_code TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY (user_id, week_key)
    )
    """)

    conn.commit()
    conn.close()


def now_ts() -> int:
    return int(time.time())


def today_key_local() -> str:
    return datetime.now(TZ).date().isoformat()


def week_key_local(d: date | None = None) -> str:
    d = d or datetime.now(TZ).date()
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def ensure_user(user_id: int):
    conn = db_connect()
    cur = conn.cursor()
    ts = now_ts()

    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()

    if row is None:
        cur.execute("""
            INSERT INTO users (user_id, first_seen, last_seen, visits, returns, subscribed)
            VALUES (?, ?, ?, 1, 0, 0)
        """, (user_id, ts, ts))
    else:
        last_seen = int(row["last_seen"])
        is_return = (ts - last_seen) >= 24 * 3600
        if is_return:
            cur.execute("""
                UPDATE users
                SET last_seen=?, visits=visits+1, returns=returns+1
                WHERE user_id=?
            """, (ts, user_id))
        else:
            cur.execute("""
                UPDATE users
                SET last_seen=?, visits=visits+1
                WHERE user_id=?
            """, (ts, user_id))

    conn.commit()
    conn.close()


def set_last_card(user_id: int, card_id: str):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_card_id=? WHERE user_id=?", (card_id, user_id))
    conn.commit()
    conn.close()


def get_last_card(user_id: int) -> str | None:
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT last_card_id FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return row["last_card_id"]
    
def get_daily_card_date(user_id: int) -> str | None:
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT daily_card_date FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return row["daily_card_date"]


def set_daily_card_date(user_id: int, day_key: str):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("UPDATE users SET daily_card_date=? WHERE user_id=?", (day_key, user_id))
    conn.commit()
    conn.close()


def add_favorite(user_id: int, card_id: str):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO favorites (user_id, card_id, added_at)
        VALUES (?, ?, ?)
    """, (user_id, card_id, now_ts()))
    conn.commit()
    conn.close()


def favorites_last_7_days(user_id: int):
    since = now_ts() - 7 * 24 * 3600
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT card_id, added_at
        FROM favorites
        WHERE user_id=? AND added_at>=?
        ORDER BY added_at DESC
    """, (user_id, since))
    rows = cur.fetchall()
    conn.close()
    return rows


def toggle_subscribe(user_id: int) -> bool:
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT subscribed FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    new_val = 0 if int(row["subscribed"]) == 1 else 1
    cur.execute("UPDATE users SET subscribed=? WHERE user_id=?", (new_val, user_id))
    conn.commit()
    conn.close()
    return new_val == 1


def mark_daily_sent(user_id: int, day_key: str):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_daily_sent=? WHERE user_id=?", (day_key, user_id))
    conn.commit()
    conn.close()


def was_daily_sent(user_id: int, day_key: str) -> bool:
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT last_daily_sent FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row and row["last_daily_sent"] == day_key)


def save_weekly_state(user_id: int, week_key: str, state_code: str):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO weekly_state (user_id, week_key, state_code, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, week_key) DO UPDATE SET
            state_code=excluded.state_code,
            created_at=excluded.created_at
    """, (user_id, week_key, state_code, now_ts()))
    cur.execute("UPDATE users SET last_weekly_state_week=? WHERE user_id=?", (week_key, user_id))
    conn.commit()
    conn.close()


def get_weekly_state(user_id: int, week_key_: str):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT state_code, created_at
        FROM weekly_state
        WHERE user_id=? AND week_key=?
    """, (user_id, week_key_))
    row = cur.fetchone()
    conn.close()
    return row


def admin_stats_text():
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as n FROM users")
    total_users = int(cur.fetchone()["n"])

    since = now_ts() - 7 * 24 * 3600
    cur.execute("SELECT COUNT(*) as n FROM users WHERE first_seen>=?", (since,))
    new_7 = int(cur.fetchone()["n"])

    cur.execute("SELECT COALESCE(SUM(visits),0) as v, COALESCE(SUM(returns),0) as r FROM users")
    sums = cur.fetchone()
    total_visits = int(sums["v"])
    total_returns = int(sums["r"])

    cur.execute("SELECT COUNT(*) as n FROM users WHERE subscribed=1")
    reminders_enabled = int(cur.fetchone()["n"])

    conn.close()

    return (
        "📊 *Статистика*\n\n"
        f"👤 Пользователей всего: *{total_users}*\n"
        f"🆕 Новых за 7 дней: *{new_7}*\n"
        f"👣 Посещений (суммарно): *{total_visits}*\n"
        f"🔁 Возвратов (24ч+): *{total_returns}*\n"
        f"🔔 Напоминание включено: *{reminders_enabled}*\n"
    )


# =========================
# UI
# =========================
def main_menu_kb():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🌿 Карта дня")
    kb.button(text="🌿 Выбрать карту")
    kb.button(text="🫧 Мой вопрос")
    kb.button(text="⭐ В избранное")
    kb.button(text="📌 Мои избранные")
    kb.button(text="📅 Состояние недели")
    kb.button(text="🔔 Напоминание о карте")
    kb.button(text="🌿 О боте")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup(
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Выбери действие…"
    )


def weekly_state_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🌿 Ясность", callback_data="weekstate:clear")
    kb.button(text="🌫 Туманность", callback_data="weekstate:fog")
    kb.button(text="🌊 Перегруз", callback_data="weekstate:overload")
    kb.button(text="🫧 Хрупкость", callback_data="weekstate:fragile")
    kb.button(text="🔥 Напряжение", callback_data="weekstate:tension")
    kb.button(text="🌤 Тепло", callback_data="weekstate:warm")
    kb.adjust(2, 2, 2)
    return kb.as_markup()


def card_actions_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔍 Разобрать состояние", callback_data="card:deeper")
    kb.button(text="⭐ Сохранить", callback_data="card:save")
    kb.button(text="🌿 Новая карта", callback_data="card:new")
    kb.adjust(1, 2)
    return kb.as_markup()


WEEKSTATE_TEXT = {
    "clear": (
        "🌿 *Ясность*\n"
        "Как будто сегодня можно вдохнуть свободнее. Не ускоряйся — просто иди ровно.\n\n"
        "Вопрос: *что ты хочешь сохранить в этом состоянии?*"
    ),
    "fog": (
        "🌫 *Туманность*\n"
        "Когда всё размыто — это не откат назад и не потеря опоры. Это знак: тебе нужен ориентир, а не скорость.\n\n"
        "Вопрос: *что будет твоим самым простым ориентиром на эту неделю?*"
    ),
    "overload": (
        "🌊 *Перегруз*\n"
        "Твоё «слишком много» — не слабость. Это честная информация.\n\n"
        "Вопрос: *что-то можно уменьшить на 10% уже сегодня?*"
    ),
    "fragile": (
        "🫧 *Хрупкость*\n"
        "Тут важно не «собраться», а бережно удержать себя.\n\n"
        "Вопрос: *какая поддержка тебе нужна сейчас?*"
    ),
    "tension": (
        "🔥 *Напряжение*\n"
        "Тело, как зеркало, отражает то, что ум пытается контролировать.\n\n"
        "Вопрос: *где ты можешь чуть ослабить хватку?*"
    ),
    "warm": (
        "🌤 *Тепло*\n"
        "В тебе есть ресурс. Пусть он не уходит в доказательства.\n\n"
        "Вопрос: *какое позитивное подкрепление ты выбираешь на этой неделе?*"
    ),
}


# =========================
# FSM
# =========================
class AskCardState(StatesGroup):
    waiting_for_question = State()


# =========================
# CARD FORMAT / SEND
# =========================
def card_image_path(card_id: str) -> str:
    return os.path.join("images", f"{card_id}.jpg")


async def send_card(message: Message, card: dict):
    # Небольшая пауза перед появлением карты
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    await asyncio.sleep(1.2)

    img_path = card_image_path(card["id"])
    caption = f"🌿 *{card['title']}*\n\n{card['text']}"

    if os.path.exists(img_path):
        await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
        await asyncio.sleep(0.4)
        await message.answer_photo(
            photo=FSInputFile(img_path),
            caption=caption
        )
    else:
        await message.answer(caption)

    # Время посмотреть карту
    await asyncio.sleep(2.5)
    await message.answer("🌿")

    # Пауза перед вопросом
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    await asyncio.sleep(2.0)

    preface = random.choice(PRE_QUESTION_TEXTS)
    await message.answer(
        f"{preface}\n\n"
        f"🔎 *Вопрос:*\n\n{card['question']}",
        reply_markup=card_actions_kb()
    )


async def send_deeper_reflection(message: Message, card: dict):
    await asyncio.sleep(2.0)

    await message.answer(
        "🌿\n\n"
        "Можно посмотреть на эту карту чуть глубже\n"
        "и задать ей ещё несколько вопросов."
    )
    
    # листик перед вопросом
    await asyncio.sleep(1.2)
    await message.answer("🌿")

    await asyncio.sleep(1.5)
    await message.answer(f"🫧 {random.choice(OBSERVATION_QUESTIONS)}")

    await asyncio.sleep(1.6)
    await message.answer(f"🍃 {random.choice(RESOURCE_QUESTIONS)}")

    # листик перед практикой
    await asyncio.sleep(1.5)
    await message.answer("🌿")

    await asyncio.sleep(2.0)
    await message.answer(random.choice(PRACTICES))

    await asyncio.sleep(2.2)
    await message.answer(
    FINAL_REFLECTION_TEXT,
    reply_markup=main_menu_kb()
    )


# =========================
# Card logic
# =========================
def card_of_day(local_day: date | None = None) -> dict:
    local_day = local_day or datetime.now(TZ).date()
    seed = int(local_day.strftime("%Y%m%d"))
    idx = seed % len(CARDS)
    return CARDS[idx]


def random_card() -> dict:
    return random.choice(CARDS)


# =========================
# HTTP server (health)
# =========================
async def handle_root(request):
    return web.Response(text="OK")


async def handle_health(request):
    return web.Response(text="OK")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()

# =========================
# TEXTS
# =========================

WELCOME_TEXT = (
    "🌿\n\n"
    "Добро пожаловать.\n\n"
    "Это пространство паузы и внутреннего диалога.\n"
    "Здесь можно на мгновение остановиться, заметить своё состояние и получить ориентир для размышления.\n\n"
    "Метафорическая карта — это образы и вопросы, которые помогают лучше слышать себя,\n"
    "замечать новые смыслы.\n\n"
    "Иногда образ или фраза открывают новую мысль, иногда просто помогают сделать паузу.\n"
    "А иногда — стать точкой опоры на день.\n\n"
    "Здесь нет правильных или неправильных ответов.\n"
    "Есть только то, что откликается именно тебе.\n\n"
    "🌿\n\n"
    "Этот бот — результат творческой работы команды.\n\n"
    "Психологическая концепция и тексты\n"
    "Светлана\n" 
    "https://t.me/teplaya_psihologiya.\n\n"
    "Программная разработка\n"
    "Михаил\n"
    "@mishaguber.\n\n"
    "Визуальный стиль и дизайн\n"
    "Софья\n"
    "@https://readymag.website/archive.ah23/4084372/.\n\n" 
    "🌿\n\n"
    "Нажми «🌿 Карта дня» или «🌿 Выбрать карту»\n"
    "и посмотри, какой образ откликнется сегодня."  
)


# =========================
# BOT
# =========================
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    ensure_user(message.from_user.id)

    await message.answer(
        WELCOME_TEXT,
        reply_markup=main_menu_kb()
    )
    
    
@dp.message(Command("admin_stats"))
async def cmd_admin_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Эта команда доступна только администратору.")
        return
    ensure_user(message.from_user.id)
    await message.answer(admin_stats_text())


@dp.message(F.text == "🌿 Карта дня")
async def on_card_day(message: Message):
    ensure_user(message.from_user.id)

    today = today_key_local()
    already_opened = get_daily_card_date(message.from_user.id)

    # если карта дня уже открыта
    if already_opened == today:

        await message.answer(
            "🌿 Сегодняшняя карта уже с тобой.\n\n"
            "Можно вернуться к ней ещё раз."
        )

        card = card_of_day()
        set_last_card(message.from_user.id, card["id"])
        await send_card(message, card)
        return

    # первый раз за день
    await message.answer(
        "Сделай небольшой вдох.\n"
        "Можно на секунду остановиться."
    )

    await asyncio.sleep(1.5)

    await message.answer(
        "Образ дня."
    )

    await asyncio.sleep(1.2)

    card = card_of_day()

    set_last_card(message.from_user.id, card["id"])
    set_daily_card_date(message.from_user.id, today)

    await send_card(message, card)


@dp.message(F.text == "🌿 Выбрать карту")
async def on_pick_card(message: Message):
    ensure_user(message.from_user.id)

    await message.bot.send_chat_action(message.chat.id, "typing")
    await asyncio.sleep(1.2)

    card = random_card()
    set_last_card(message.from_user.id, card["id"])
    await send_card(message, card)


@dp.message(F.text == "🫧 Мой вопрос")
async def on_my_question(message: Message, state: FSMContext):
    ensure_user(message.from_user.id)
    await state.set_state(AskCardState.waiting_for_question)

    await message.answer(
        "🌿 Может быть полезно прийти к карте со своим вопросом.\n\n"
        "Напиши, о чём тебе хочется спросить сегодня.\n\n"
        "Можно коротко, одним предложением."
    )


@dp.message(AskCardState.waiting_for_question, F.text)
async def process_my_question(message: Message, state: FSMContext):
    ensure_user(message.from_user.id)

    user_question = message.text.strip()
    await state.clear()

    card = random_card()
    set_last_card(message.from_user.id, card["id"])

    await message.answer(
        "🌿 Спасибо.\n"
        "Сохрани этот вопрос в уме и посмотри, какой образ приходит в ответ."
    )

    await send_card(message, card)

    await asyncio.sleep(0.7)
    await message.answer(
        f"🔎 *Связь с вопросом:*\n\n"
        f"Как этот образ может быть связан с твоим вопросом:\n"
        f"_{user_question}_",
        reply_markup=main_menu_kb()
    )


@dp.message(
    AskCardState.waiting_for_question,
    F.text.in_({
        "🌿 Карта дня",
        "🌿 Выбрать карту",
        "⭐ В избранное",
        "📌 Мои избранные",
        "📅 Состояние недели",
        "🔔 Напоминание о карте",
    })
)
async def cancel_my_question_mode(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Ок, выходим из режима вопроса 🙂",
        reply_markup=main_menu_kb()
    )


@dp.message(F.text == "⭐ В избранное")
async def on_add_fav(message: Message):
    ensure_user(message.from_user.id)
    last = get_last_card(message.from_user.id)
    if not last:
        await message.answer("Сначала выбери карту: нажми «🌿 Выбрать карту» или «🌿 Карта дня».")
        return

    try:
        add_favorite(message.from_user.id, last)
    except Exception:
        pass

    title = CARD_BY_ID.get(last, {}).get("title", last)
    await message.answer(f"Добавила в избранное: «{title}»", reply_markup=main_menu_kb())


@dp.message(F.text == "📌 Мои избранные")
async def on_list_fav(message: Message):
    ensure_user(message.from_user.id)
    rows = favorites_last_7_days(message.from_user.id)
    if not rows:
        await message.answer("📌 Избранных за 7 дней пока нет.\n\nВыбери карту и нажми «⭐ В избранное».")
        return

    lines = ["📌 *Избранные за 7 дней:*", ""]
    for r in rows[:20]:
        card_id = r["card_id"]
        title = CARD_BY_ID.get(card_id, {}).get("title", card_id)
        dt = datetime.fromtimestamp(int(r["added_at"]), TZ).strftime("%d.%m %H:%M")
        lines.append(f"• {title} _(добавлено {dt})_")

    await message.answer("\n".join(lines), reply_markup=main_menu_kb())


@dp.message(F.text == "📅 Состояние недели")
async def on_week_state(message: Message):
    ensure_user(message.from_user.id)

    wk = week_key_local()
    existing = get_weekly_state(message.from_user.id, wk)
    if existing:
        code = existing["state_code"]
        preview = WEEKSTATE_TEXT.get(code, "")
        await message.answer(
            "📅 На этой неделе у тебя уже отмечено состояние.\n\n"
            "Хочешь обновить?\n\n"
            f"{preview}",
            reply_markup=weekly_state_kb()
        )
        return

    await message.answer(
        "📅 *Состояние недели*\n\n"
        "Выбери, что ближе всего сейчас. Это пауза, чтобы заметить себя.",
        reply_markup=weekly_state_kb()
    )


@dp.callback_query(F.data.startswith("weekstate:"))
async def on_week_state_pick(call: CallbackQuery):
    ensure_user(call.from_user.id)
    code = call.data.split(":", 1)[1].strip()
    if code not in WEEKSTATE_TEXT:
        await call.answer("Не поняла выбор 🙈", show_alert=False)
        return

    wk = week_key_local()
    save_weekly_state(call.from_user.id, wk, code)
    await call.message.answer(WEEKSTATE_TEXT[code], reply_markup=main_menu_kb())
    await call.answer("Сохранено ✅", show_alert=False)


@dp.message(F.text == "🔔 Напоминание о карте")
async def on_subscribe_toggle(message: Message):
    ensure_user(message.from_user.id)
    enabled = toggle_subscribe(message.from_user.id)
    if enabled:
        await message.answer(
            "🔔 Напоминание включено.\n\n"
            "Я буду присылать мягкое напоминание с картой дня.\n"
            "Если захочешь выключить — нажми «🔔 Напоминание о карте» ещё раз.",
            reply_markup=main_menu_kb()
        )
    else:
        await message.answer(
            "🔕 Напоминание выключено.\n\n"
            "Я рядом, когда ты решишь вернуться.",
            reply_markup=main_menu_kb()
        )
@dp.message(F.text == "🌿 О боте")
async def about_bot(message: Message):
    await asyncio.sleep(0.8)

    await message.answer(
        WELCOME_TEXT,
        reply_markup=main_menu_kb()
    )


# =========================
# CALLBACKS UNDER CARD
# =========================
@dp.callback_query(F.data == "card:save")
async def on_card_save(call: CallbackQuery):
    ensure_user(call.from_user.id)
    last = get_last_card(call.from_user.id)
    if not last:
        await call.answer("Сначала вытяни карту", show_alert=False)
        return

    try:
        add_favorite(call.from_user.id, last)
    except Exception:
        pass

    title = CARD_BY_ID.get(last, {}).get("title", last)
    await call.message.answer(f"⭐ Карта «{title}» сохранена.", reply_markup=main_menu_kb())
    await call.answer("Сохранено ✅", show_alert=False)


@dp.callback_query(F.data == "card:new")
async def on_card_new(call: CallbackQuery):
    ensure_user(call.from_user.id)
    await call.answer()
    await call.message.bot.send_chat_action(call.message.chat.id, "typing")
    await asyncio.sleep(1.2)
    card = random_card()
    set_last_card(call.from_user.id, card["id"])
    await send_card(call.message, card)


@dp.callback_query(F.data == "card:deeper")
async def on_card_deeper(call: CallbackQuery):
    ensure_user(call.from_user.id)
    last = get_last_card(call.from_user.id)
    if not last:
        await call.answer("Сначала вытяни карту", show_alert=False)
        return

    card = CARD_BY_ID.get(last)
    if not card:
        await call.answer("Не нашла карту 🙈", show_alert=False)
        return

    await call.answer()
    await send_deeper_reflection(call.message, card)


# =========================
# Soft reminders scheduler
# =========================
async def reminders_loop(bot: Bot):
    while True:
        try:
            now_local = datetime.now(TZ)
            if now_local.hour == REMINDER_HOUR and now_local.minute == REMINDER_MIN:
                day_key = now_local.date().isoformat()

                conn = db_connect()
                cur = conn.cursor()
                cur.execute("SELECT user_id FROM users WHERE subscribed=1")
                users = [int(r["user_id"]) for r in cur.fetchall()]
                conn.close()

                for uid in users:
                    if was_daily_sent(uid, day_key):
                        continue

                    cd = card_of_day(now_local.date())
                    text = (
                        "🔔 *Мягкое напоминание*\n\n"
                        "Сделай одну маленькую паузу.\n"
                        "Не чтобы «успеть», а чтобы вернуться к себе.\n\n"
                        f"Сегодняшняя карта дня: *{cd['title']}*\n"
                        "Нажми «🌿 Карта дня», если хочешь её открыть."
                    )
                    try:
                        await bot.send_message(uid, text, reply_markup=main_menu_kb())
                        mark_daily_sent(uid, day_key)
                    except Exception:
                        pass

            await asyncio.sleep(60)

        except Exception:
            await asyncio.sleep(10)


# =========================
# MAIN
# =========================
async def main():
    db_init()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="Markdown")
    )

    await start_web_server()
    asyncio.create_task(reminders_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

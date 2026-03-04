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

# timezone for “soft reminders”
# Default: GMT+3 (as you have)
TZ_OFFSET_HOURS = int(os.getenv("TZ_OFFSET_HOURS", "3"))
TZ = timezone(timedelta(hours=TZ_OFFSET_HOURS))

# Reminder time (local TZ)
REMINDER_HOUR = int(os.getenv("REMINDER_HOUR", "10"))   # 10:00
REMINDER_MIN  = int(os.getenv("REMINDER_MIN", "0"))

DB_PATH = os.getenv("DB_PATH", "bot.db")


# =========================
# CARDS (10)
# =========================
CARDS = [
    {
        "id": "quiet_forest",
        "title": "Тихий лес",
        "text": "Иногда самый верный шаг — замедлиться и услышать, что ты давно игнорируешь.",
        "question": "Что внутри тебя просит тишины прямо сейчас?",
    },
    {
        "id": "warm_light",
        "title": "Тёплый свет",
        "text": "Даже маленький свет — это знак: тебе есть куда вернуться.",
        "question": "Где твоя «точка света» сегодня?",
    },
    {
        "id": "pocket_key",
        "title": "Ключ в кармане",
        "text": "Ресурс часто ближе, чем кажется. Он не громкий — он твой.",
        "question": "Какой ресурс ты недооцениваешь?",
    },
    {
        "id": "road_step",
        "title": "Дорога",
        "text": "Не обязательно знать весь путь. Достаточно следующего шага.",
        "question": "Какой самый маленький следующий шаг возможен сегодня?",
    },
    {
        "id": "tea_pause",
        "title": "Чашка чая",
        "text": "Иногда забота — не подвиг. Это простая пауза, которая возвращает тебя к себе.",
        "question": "Что ты можешь сделать для себя за 2 минуты — без чувства вины?",
    },
    {
        "id": "soft_fog",
        "title": "Лёгкий туман",
        "text": "Когда всё размыто — это не ошибка и не провал. Это расфокус: пространство, где появляется новый ориентир.",
        "question": "Какая одна вещь сегодня может быть твоим ориентиром?",
    },
    {
        "id": "soft_blanket",
        "title": "Мягкий плед",
        "text": "Тепло — это граница. Ты можешь укрыть себя, не объясняясь и не оправдываясь.",
        "question": "Где тебе нужна граница, чтобы стало теплее внутри?",
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
        "text": "Рост бывает тихим. Ты уже делаешь больше, чем замечаешь.",
        "question": "Что в тебе растёт — даже если пока незаметно?",
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
        last_card_id TEXT
    )
    """)

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
    return monday.isoformat()  # week key = Monday date


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
        # visit/return logic: if last_seen more than 24h ago => count return
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


def is_subscribed(user_id: int) -> bool:
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT subscribed FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row and int(row["subscribed"]) == 1)


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

    # total users
    cur.execute("SELECT COUNT(*) as n FROM users")
    total_users = int(cur.fetchone()["n"])

    # last 7 days new users
    since = now_ts() - 7 * 24 * 3600
    cur.execute("SELECT COUNT(*) as n FROM users WHERE first_seen>=?", (since,))
    new_7 = int(cur.fetchone()["n"])

    # visits / returns sums
    cur.execute("SELECT COALESCE(SUM(visits),0) as v, COALESCE(SUM(returns),0) as r FROM users")
    sums = cur.fetchone()
    total_visits = int(sums["v"])
    total_returns = int(sums["r"])

    # subscriptions
    cur.execute("SELECT COUNT(*) as n FROM users WHERE subscribed=1")
    subs = int(cur.fetchone()["n"])

    conn.close()

    return (
        "📊 *Статистика*\n\n"
        f"👤 Пользователей всего: *{total_users}*\n"
        f"🆕 Новых за 7 дней: *{new_7}*\n"
        f"👣 Посещений (суммарно): *{total_visits}*\n"
        f"🔁 Возвратов (24ч+): *{total_returns}*\n"
        f"🔔 Подписка включена: *{subs}*\n"
    )


# =========================
# UI
# =========================
def main_menu_kb():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🌿 Карта дня")
    kb.button(text="🌿 Выбрать карту")
    kb.button(text="⭐ В избранное")
    kb.button(text="📌 Мои избранные")
    kb.button(text="📅 Состояние недели")
    kb.button(text="🔔 Подписка")
    kb.adjust(2, 2, 2)
    return kb.as_markup(resize_keyboard=True, one_time_keyboard=False, input_field_placeholder="Выбери действие…")


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


WEEKSTATE_TEXT = {
    "clear": (
        "🌿 *Ясность*\n"
        "Как будто сегодня можно вдохнуть свободнее. Не ускоряйся — просто иди ровно. Наслаждайся\n\n"
        "Вопрос: *что ты хочешь сохранить в этом состоянии?*"
    ),
    "fog": (
        "🌫 *Туманность*\n"
        "Когда всё размыто — это не откат назад, не потеря опоры. Это знак: тебе нужен ориентир, а не скорость.\n\n"
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
        "Тело,как зеркало,отражает то, что ум пытается контролировать.\n\n"
        "Вопрос: *где ты можешь чуть ослабить хватку?*"
    ),
    "warm": (
        "🌤 *Тепло*\n"
        "В тебе есть ресурс. Пусть он не уходит в доказательства.\n\n"
        "Вопрос: *какое позитивное подкрепление ты выбираешь на этой неделе?*"
    ),
}


# =========================
# CARD FORMAT
# =========================

IMPORTANT_BEFORE_QUESTION = "🫧 Просто отметь первое, что откликнулось."


def format_card_title(card: dict) -> str:
    return f"🌿 *{card['title']}*"


def format_card_text(card: dict) -> str:
    return f"{card['text']}"


def format_card_question(card: dict) -> str:
    return (
        f"{IMPORTANT_BEFORE_QUESTION}\n\n"
        f"🔎 *Вопрос:* {card['question']}"
    )


def card_image_path(card_id: str) -> str:
    return os.path.join("images", f"{card_id}.jpg")


async def send_card(message: Message, card: dict):

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    await asyncio.sleep(0.4)

    img_path = card_image_path(card["id"])

    caption = f"{format_card_title(card)}\n\n{format_card_text(card)}"

    if os.path.exists(img_path):

        await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
        await asyncio.sleep(0.2)

        await message.answer_photo(
            photo=FSInputFile(img_path),
            caption=caption
        )

    else:
        await message.answer(caption)

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    await asyncio.sleep(0.4)

    await message.answer(
        format_card_question(card),
        reply_markup=main_menu_kb()
    )


# =========================
# Card logic
# =========================
def card_of_day(local_day: date | None = None) -> dict:
    local_day = local_day or datetime.now(TZ).date()
    # deterministic index from date
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
# BOT
# =========================
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    ensure_user(message.from_user.id)

    await message.answer(
        "🌿 Добро пожаловать.\n\n"
        "Это небольшое пространство паузы.\n"
        "Здесь можно остановиться на мгновение, заметить своё состояние и получить небольшой ориентир для размышления.\n\n"
        "Метафорическая карта — это образ и вопрос, которые помогают в понимании своего состояния.\n\n"
        "Иногда картинка или фраза открывают новую мысль, иногда просто помогают сделать паузу и услышать себя.\n\n"
        "Одна карта может стать маленькой точкой опоры на день.\n\n"
        "Здесь нет правильных или неправильных ответов.\n"
        "Есть только то, что откликается именно тебе.\n\n"
        "Нажми «🌿 Карта дня».\n"
        "или «🌿 Выбрать карту» и посмотри, что откликнется сегодня.",
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
    card = card_of_day()
    set_last_card(message.from_user.id, card["id"])
    await send_card(message, card)


@dp.message(F.text == "🌿 Выбрать карту")
async def on_pick_card(message: Message):
    ensure_user(message.from_user.id)
    card = random_card()
    set_last_card(message.from_user.id, card["id"])
    await send_card(message, card)

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
        # If user taps many times quickly, ignore duplicates with same timestamp collision chance
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


@dp.message(F.text == "🔔 Подписка")
async def on_subscribe_toggle(message: Message):
    ensure_user(message.from_user.id)
    enabled = toggle_subscribe(message.from_user.id)
    if enabled:
        await message.answer(
            "🔔 Подписка включена.\n\n"
            "Я буду присылать напоминание раз в день — без давления.\n"
            "Если захочешь выключить, нажми «🔔 Подписка» ещё раз.",
            reply_markup=main_menu_kb()
        )
    else:
        await message.answer(
            "🔕 Подписка выключена.\n\n"
            "Я рядом, когда ты решишь вернуться.",
            reply_markup=main_menu_kb()
        )


# =========================
# Soft reminders scheduler
# =========================
async def reminders_loop(bot: Bot):
    """
    Once a minute checks if it's time to send a daily gentle reminder.
    Sends only to subscribed users, once per day.
    """
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

                    # gentle message + card of day suggestion
                    cd = card_of_day(now_local.date())
                    text = (
                        "🔔 *Мягкое напоминание*\n\n"
                        "Сделай одну маленькую паузу. Не чтобы «успеть», а чтобы вернуться к себе.\n\n"
                        f"Сегодняшняя карта дня: *{cd['title']}*\n"
                        "Нажми «🌿 Карта дня», если хочешь её открыть."
                    )
                    try:
                        await bot.send_message(uid, text, reply_markup=main_menu_kb())
                        mark_daily_sent(uid, day_key)
                    except Exception:
                        # user blocked bot or other send error — ignore
                        pass

            # sleep 60s
            await asyncio.sleep(60)

        except Exception:
            # if something goes wrong, don't kill the loop
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

    # Start HTTP server for health checks
    await start_web_server()

    # Start reminders loop
    asyncio.create_task(reminders_loop(bot))

    # Start polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

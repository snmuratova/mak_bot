import os
import asyncio
import random
import sqlite3
from datetime import datetime, timedelta, timezone, date

from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, LabeledPrice,
    PreCheckoutQuery
)
from aiogram.filters import CommandStart, Command

# =========================
# CONFIG (Railway Variables)
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")  # твой TG ID
PORT = int(os.getenv("PORT", "8080"))

# timezone offset (minutes). Moscow = 180
TZ_OFFSET_MIN = int(os.getenv("TZ_OFFSET_MIN", "180"))
TZ = timezone(timedelta(minutes=TZ_OFFSET_MIN))

# Card of Day schedule
CARD_OF_DAY_HOUR = int(os.getenv("CARD_OF_DAY_HOUR", "9"))  # 09:00 local TZ

# Reminders
REMINDER_AFTER_HOURS = int(os.getenv("REMINDER_AFTER_HOURS", "48"))  # inactivity threshold
REMINDER_CHECK_EVERY_MIN = int(os.getenv("REMINDER_CHECK_EVERY_MIN", "60"))  # scheduler tick
REMINDERS_ENABLED_DEFAULT = os.getenv("REMINDERS_ENABLED_DEFAULT", "1") == "1"

# Subscription (Telegram Payments). If PROVIDER_TOKEN empty -> subscription works as "info only"
PROVIDER_TOKEN = (os.getenv("PROVIDER_TOKEN", "") or "").strip()
SUB_PRICE_RUB = int(os.getenv("SUB_PRICE_RUB", "199"))  # 199 RUB by default
SUB_DAYS = int(os.getenv("SUB_DAYS", "30"))  # subscription duration in days

# Database
DB_PATH = os.getenv("DB_PATH", "bot.db")


# =========================
# DB
# =========================
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        first_seen TEXT NOT NULL,
        last_active TEXT NOT NULL,
        visits INTEGER NOT NULL DEFAULT 1,
        reminders_enabled INTEGER NOT NULL DEFAULT 1,
        sub_until TEXT,
        last_daily_sent TEXT,
        last_reminder_sent TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        ts TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload TEXT,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        card_id TEXT NOT NULL,
        card_title TEXT NOT NULL,
        added_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS weekly_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        log_date TEXT NOT NULL,
        card_id TEXT,
        card_title TEXT,
        mood INTEGER,   -- optional 0..10
        note TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )
    """)

    conn.commit()
    conn.close()


def now_tz() -> datetime:
    return datetime.now(TZ)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def get_user(user_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row


def upsert_user_on_start(user_id: int):
    conn = db()
    cur = conn.cursor()
    n = now_tz()
    row = get_user(user_id)
    if row is None:
        cur.execute("""
            INSERT INTO users (user_id, first_seen, last_active, visits, reminders_enabled)
            VALUES (?, ?, ?, 1, ?)
        """, (user_id, iso(n), iso(n), 1 if REMINDERS_ENABLED_DEFAULT else 0))
        cur.execute("INSERT INTO events (user_id, ts, event_type) VALUES (?, ?, ?)",
                    (user_id, iso(n), "start_new"))
    else:
        cur.execute("""
            UPDATE users
            SET last_active=?, visits=visits+1
            WHERE user_id=?
        """, (iso(n), user_id))
        cur.execute("INSERT INTO events (user_id, ts, event_type) VALUES (?, ?, ?)",
                    (user_id, iso(n), "start_return"))
    conn.commit()
    conn.close()


def touch_active(user_id: int, event_type: str, payload: str | None = None):
    conn = db()
    cur = conn.cursor()
    n = now_tz()
    cur.execute("UPDATE users SET last_active=? WHERE user_id=?", (iso(n), user_id))
    cur.execute("INSERT INTO events (user_id, ts, event_type, payload) VALUES (?, ?, ?, ?)",
                (user_id, iso(n), event_type, payload))
    conn.commit()
    conn.close()


def is_subscribed(user_id: int) -> bool:
    row = get_user(user_id)
    if not row:
        return False
    sub_until = row["sub_until"]
    if not sub_until:
        return False
    try:
        dt = datetime.fromisoformat(sub_until)
        return dt >= now_tz()
    except Exception:
        return False


def set_subscription(user_id: int, days: int):
    conn = db()
    cur = conn.cursor()
    n = now_tz()
    row = get_user(user_id)
    base = n
    if row and row["sub_until"]:
        try:
            current = datetime.fromisoformat(row["sub_until"])
            if current > n:
                base = current
        except Exception:
            pass
    new_until = base + timedelta(days=days)
    cur.execute("UPDATE users SET sub_until=? WHERE user_id=?", (iso(new_until), user_id))
    cur.execute("INSERT INTO events (user_id, ts, event_type, payload) VALUES (?, ?, ?, ?)",
                (user_id, iso(n), "subscription_set", f"{days}"))
    conn.commit()
    conn.close()


def set_reminders(user_id: int, enabled: bool):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET reminders_enabled=? WHERE user_id=?",
                (1 if enabled else 0, user_id))
    conn.commit()
    conn.close()


def add_favorite(user_id: int, card_id: str, card_title: str):
    conn = db()
    cur = conn.cursor()
    n = now_tz()
    cur.execute("""
        INSERT INTO favorites (user_id, card_id, card_title, added_at)
        VALUES (?, ?, ?, ?)
    """, (user_id, card_id, card_title, iso(n)))
    cur.execute("INSERT INTO events (user_id, ts, event_type, payload) VALUES (?, ?, ?, ?)",
                (user_id, iso(n), "favorite_add", card_id))
    conn.commit()
    conn.close()


def get_favorites_last_7_days(user_id: int):
    conn = db()
    cur = conn.cursor()
    cutoff = now_tz() - timedelta(days=7)
    cur.execute("""
        SELECT card_title, added_at
        FROM favorites
        WHERE user_id=? AND added_at>=?
        ORDER BY added_at DESC
    """, (user_id, iso(cutoff)))
    rows = cur.fetchall()
    conn.close()
    return rows


def cleanup_old_favorites():
    conn = db()
    cur = conn.cursor()
    cutoff = now_tz() - timedelta(days=7)
    cur.execute("DELETE FROM favorites WHERE added_at < ?", (iso(cutoff),))
    conn.commit()
    conn.close()


def log_weekly(user_id: int, card_id: str | None, card_title: str | None, mood: int | None, note: str | None):
    conn = db()
    cur = conn.cursor()
    n = now_tz()
    cur.execute("""
        INSERT INTO weekly_log (user_id, log_date, card_id, card_title, mood, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, date.today().isoformat(), card_id, card_title, mood, note, iso(n)))
    conn.commit()
    conn.close()


def get_week_summary(user_id: int):
    conn = db()
    cur = conn.cursor()
    cutoff = (date.today() - timedelta(days=6)).isoformat()
    cur.execute("""
        SELECT log_date, card_title, mood, note
        FROM weekly_log
        WHERE user_id=? AND log_date>=?
        ORDER BY log_date ASC, created_at ASC
    """, (user_id, cutoff))
    rows = cur.fetchall()
    conn.close()
    return rows


def admin_stats():
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as c FROM users")
    total_users = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) as c FROM users WHERE visits>1")
    returned = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) as c FROM events WHERE event_type='card_shown'")
    cards_shown = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) as c FROM events WHERE event_type='favorite_add'")
    fav_added = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) as c FROM events WHERE event_type='daily_sent'")
    daily_sent = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) as c FROM events WHERE event_type='reminder_sent'")
    reminders_sent = cur.fetchone()["c"]

    conn.close()
    return total_users, returned, cards_shown, fav_added, daily_sent, reminders_sent


def iter_users():
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users")
    rows = cur.fetchall()
    conn.close()
    return rows


def set_last_daily_sent(user_id: int, day: date):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_daily_sent=? WHERE user_id=?", (day.isoformat(), user_id))
    conn.commit()
    conn.close()


def set_last_reminder_sent(user_id: int, dt: datetime):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_reminder_sent=? WHERE user_id=?", (iso(dt), user_id))
    conn.commit()
    conn.close()


# =========================
# CARDS (10 cards)
# =========================
# card_id should be stable strings
CARDS = [
    {
        "id": "quiet_forest",
        "title": "Тихий лес",
        "text": (
            "🌲 **Тихий лес**\n\n"
            "Иногда внутри становится слишком шумно — и тогда психика просит не решения, а паузы.\n"
            "Эта карта про мягкое замедление: не «бросить всё», а **перестать давить на себя**.\n\n"
            "🔎 **Смысл:** тишина — это тоже действие.\n"
            "🫧 **Точка опоры:** один маленький шаг в сторону бережности.\n\n"
            "🔍 **Вопрос:** Что внутри тебя просит тишины прямо сейчас?"
        )
    },
    {
        "id": "warm_window",
        "title": "Тёплое окно",
        "text": (
            "🪟 **Тёплое окно**\n\n"
            "Там, где страшно — важно увидеть свет. Даже если он маленький.\n"
            "Эта карта про **ориентир**: что в твоей жизни уже работает и поддерживает.\n\n"
            "🔎 **Смысл:** не нужно сразу «всё исправить». Достаточно найти окно.\n"
            "🫧 **Точка опоры:** один источник тепла рядом.\n\n"
            "🔍 **Вопрос:** Где в твоей реальности сейчас есть «свет», который ты недооцениваешь?"
        )
    },
    {
        "id": "stone_in_pocket",
        "title": "Камень в кармане",
        "text": (
            "🪨 **Камень в кармане**\n\n"
            "Когда уносит тревога — полезно вернуться в тело.\n"
            "Эта карта про заземление: контакт с фактом, весом, границей.\n\n"
            "🔎 **Смысл:** сейчас достаточно быть здесь.\n"
            "🫧 **Точка опоры:** ощущение «я держусь».\n\n"
            "🔍 **Вопрос:** Что ты можешь почувствовать телом прямо сейчас (опора ног, спина, ладони)?"
        )
    },
    {
        "id": "calm_lake",
        "title": "Спокойное озеро",
        "text": (
            "🌊 **Спокойное озеро**\n\n"
            "Не всё внутри должно быть «понято» немедленно.\n"
            "Иногда чувства отстаиваются, как вода.\n\n"
            "🔎 **Смысл:** ясность приходит, когда перестаёшь мешать.\n"
            "🫧 **Точка опоры:** дать себе время.\n\n"
            "🔍 **Вопрос:** Что будет, если ты позволишь этой эмоции просто быть 10 минут — без анализа?"
        )
    },
    {
        "id": "soft_lantern",
        "title": "Мягкий фонарь",
        "text": (
            "🏮 **Мягкий фонарь**\n\n"
            "Когда всё кажется размытым — это не «провал». Это **туман**.\n"
            "В тумане не бегут — в тумане светят ближе.\n\n"
            "🔎 **Смысл:** уменьшить дальность, увеличить заботу.\n"
            "🫧 **Точка опоры:** один следующий шаг, который видно.\n\n"
            "🔍 **Вопрос:** Какой шаг ты видишь на расстоянии одного метра от себя?"
        )
    },
    # +5 новых карт
    {
        "id": "home_key",
        "title": "Ключ от дома",
        "text": (
            "🔑 **Ключ от дома**\n\n"
            "Дом — это не место. Это состояние, где можно быть собой.\n"
            "Эта карта про границы и безопасность: где ты «закрываешь дверь» от лишнего.\n\n"
            "🔎 **Смысл:** право на «нет» — это тоже забота.\n"
            "🫧 **Точка опоры:** один выбор в пользу себя.\n\n"
            "🔍 **Вопрос:** Что ты сегодня готов(а) не впускать в свой внутренний дом?"
        )
    },
    {
        "id": "gentle_breath",
        "title": "Тихий вдох",
        "text": (
            "🌬 **Тихий вдох**\n\n"
            "Ты не обязан(а) быть сильным(ой) каждую минуту.\n"
            "Эта карта про восстановление: микро-пауза, которая возвращает тебя тебе.\n\n"
            "🔎 **Смысл:** иногда достаточно выдохнуть.\n"
            "🫧 **Точка опоры:** 3 дыхательных цикла.\n\n"
            "🔍 **Вопрос:** Что меняется в твоём теле после 3 медленных выдохов?"
        )
    },
    {
        "id": "paper_boat",
        "title": "Бумажный кораблик",
        "text": (
            "⛵ **Бумажный кораблик**\n\n"
            "Не всё нужно контролировать, чтобы двигаться.\n"
            "Иногда достаточно пустить ситуацию по течению на 15 минут и посмотреть, что будет.\n\n"
            "🔎 **Смысл:** доверие — это навык.\n"
            "🫧 **Точка опоры:** маленький эксперимент.\n\n"
            "🔍 **Вопрос:** Что ты можешь отпустить «на время», не навсегда?"
        )
    },
    {
        "id": "mountain_path",
        "title": "Тропа в горах",
        "text": (
            "⛰ **Тропа в горах**\n\n"
            "Большие изменения не делаются рывком.\n"
            "Они делаются шагами — иногда очень маленькими.\n\n"
            "🔎 **Смысл:** движение важнее скорости.\n"
            "🫧 **Точка опоры:** один шаг сегодня.\n\n"
            "🔍 **Вопрос:** Какой шаг на 5 минут приблизит тебя к твоей тропе?"
        )
    },
    {
        "id": "safe_blanket",
        "title": "Тёплый плед",
        "text": (
            "🧣 **Тёплый плед**\n\n"
            "Когда мир слишком острый — тебе нужен слой мягкости.\n"
            "Эта карта про самосострадание: говорить с собой так, как говоришь с теми, кого любишь.\n\n"
            "🔎 **Смысл:** бережность — не слабость.\n"
            "🫧 **Точка опоры:** одна фраза поддержки себе.\n\n"
            "🔍 **Вопрос:** Что бы ты сказал(а) другу в твоей ситуации — и можешь ли ты сказать это себе?"
        )
    }
]

CARD_BY_ID = {c["id"]: c for c in CARDS}


# =========================
# UI (simple text-buttons)
# =========================
MAIN_MENU = (
    "Выбери действие 👇\n\n"
    "🌿 Выбрать карту\n"
    "⭐ В избранное (к последней карте)\n"
    "📌 Мои избранные\n"
    "🧠 Состояние недели\n"
    "🔔 Напоминания\n"
    "💳 Подписка\n"
)

def main_keyboard_text() -> str:
    # ReplyKeyboard “buttons” via text (works everywhere, no extra deps)
    return (
        "🌿 Выбрать карту\n"
        "⭐ В избранное\n"
        "📌 Мои избранные\n"
        "🧠 Состояние недели\n"
        "🔔 Напоминания\n"
        "💳 Подписка\n"
        "🏠 Меню"
    )


def pick_card() -> dict:
    return random.choice(CARDS)


# last card cache in memory (per process) for “add to favorites last shown”
LAST_CARD = {}  # user_id -> card_id


# =========================
# HTTP server (Railway likes open port for services)
# =========================
async def handle_root(request):
    return web.Response(text="OK")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()


# =========================
# BOT
# =========================
bot = Bot(token=BOT_TOKEN, parse_mode="Markdown")
dp = Dispatcher()


async def send_menu(message: Message):
    await message.answer("🏠 Меню\n\n" + MAIN_MENU)
    await message.answer(main_keyboard_text())


@dp.message(CommandStart())
async def cmd_start(message: Message):
    upsert_user_on_start(message.from_user.id)
    await message.answer("Бот запущен ✅")
    await send_menu(message)


@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    total_users, returned, cards_shown, fav_added, daily_sent, reminders_sent = admin_stats()
    txt = (
        "📊 **Админ-статистика**\n\n"
        f"👥 Всего пользователей: **{total_users}**\n"
        f"🔁 Вернулись повторно: **{returned}**\n"
        f"🃏 Выдано карт: **{cards_shown}**\n"
        f"⭐ Добавлено в избранное: **{fav_added}**\n"
        f"☀️ Отправлено «Карта дня»: **{daily_sent}**\n"
        f"🔔 Отправлено напоминаний: **{reminders_sent}**\n"
    )
    await message.answer(txt)


@dp.message(F.text == "🏠 Меню")
async def menu_btn(message: Message):
    touch_active(message.from_user.id, "menu")
    await send_menu(message)


@dp.message(F.text == "🌿 Выбрать карту")
async def choose_card(message: Message):
    user_id = message.from_user.id
    touch_active(user_id, "choose_card")

    card = pick_card()
    LAST_CARD[user_id] = card["id"]
    touch_active(user_id, "card_shown", card["id"])
    log_weekly(user_id, card["id"], card["title"], mood=None, note=None)

    await message.answer(card["text"])
    await message.answer("Если хочешь — нажми ⭐ *В избранное* или открой *Состояние недели*.")


@dp.message(F.text == "⭐ В избранное")
async def add_to_fav(message: Message):
    user_id = message.from_user.id
    touch_active(user_id, "fav_click")

    card_id = LAST_CARD.get(user_id)
    if not card_id:
        await message.answer("Сначала выбери карту 🌿")
        return

    card = CARD_BY_ID[card_id]
    add_favorite(user_id, card_id, card["title"])
    await message.answer(f"Добавила в избранное: «{card['title']}» ⭐")


@dp.message(F.text == "📌 Мои избранные")
async def my_favs(message: Message):
    user_id = message.from_user.id
    touch_active(user_id, "fav_list")

    cleanup_old_favorites()
    rows = get_favorites_last_7_days(user_id)
    if not rows:
        await message.answer("📌 Избранное за 7 дней пусто.\nВыбери карту 🌿 и добавь ⭐")
        return

    lines = ["📌 **Избранные за 7 дней:**\n"]
    for r in rows[:20]:
        dt = datetime.fromisoformat(r["added_at"])
        lines.append(f"• {r['card_title']}  _(добавлено {dt.strftime('%d.%m %H:%M')})_")
    await message.answer("\n".join(lines))


# -------------------------
# WEEK STATE
# -------------------------
@dp.message(F.text == "🧠 Состояние недели")
async def week_state(message: Message):
    user_id = message.from_user.id
    touch_active(user_id, "week_state")

    rows = get_week_summary(user_id)
    if not rows:
        await message.answer(
            "🧠 **Состояние недели**\n\n"
            "Пока пусто. Давай начнём: выбери карту 🌿 — она появится в недельной ленте."
        )
        return

    lines = ["🧠 **Состояние недели (последние 7 дней):**\n"]
    current_day = None
    for r in rows:
        day = r["log_date"]
        if day != current_day:
            current_day = day
            d = datetime.fromisoformat(day)
            lines.append(f"\n**{d.strftime('%d.%m')}**")
        title = r["card_title"] or "—"
        mood = r["mood"]
        note = r["note"]
        tail = []
        if mood is not None:
            tail.append(f"настроение: {mood}/10")
        if note:
            tail.append(f"заметка: {note}")
        tail_txt = f"  _({', '.join(tail)})_" if tail else ""
        lines.append(f"• {title}{tail_txt}")

    lines.append("\nХочешь добавить настроение? Напиши: **мood 7** (от 0 до 10).")
    lines.append("Или заметку: **note текст**")
    await message.answer("\n".join(lines))


@dp.message(F.text.regexp(r"^(mood|муд|мудд|настроение)\s+(\d{1,2})$", flags=0))
async def set_mood(message: Message):
    user_id = message.from_user.id
    parts = message.text.strip().split()
    val = int(parts[-1])
    if val < 0 or val > 10:
        await message.answer("Поставь число от 0 до 10 🙂")
        return
    touch_active(user_id, "mood_set", str(val))
    log_weekly(user_id, card_id=None, card_title=None, mood=val, note=None)
    await message.answer(f"Принято ✅ Настроение: {val}/10")


@dp.message(F.text.regexp(r"^(note|заметка)\s+(.+)$", flags=0))
async def set_note(message: Message):
    user_id = message.from_user.id
    txt = message.text.strip()
    note = txt.split(" ", 1)[1].strip()
    if len(note) > 800:
        note = note[:800]
    touch_active(user_id, "note_set")
    log_weekly(user_id, card_id=None, card_title=None, mood=None, note=note)
    await message.answer("Заметка сохранена ✅")


# -------------------------
# REMINDERS toggle
# -------------------------
@dp.message(F.text == "🔔 Напоминания")
async def reminders_menu(message: Message):
    user_id = message.from_user.id
    touch_active(user_id, "reminders_menu")

    row = get_user(user_id)
    enabled = bool(row["reminders_enabled"]) if row else REMINDERS_ENABLED_DEFAULT
    state = "включены ✅" if enabled else "выключены ⛔️"
    await message.answer(
        f"🔔 Напоминания сейчас: **{state}**\n\n"
        "Напиши:\n"
        "• **rem on** — включить\n"
        "• **rem off** — выключить\n\n"
        f"Мягкое напоминание приходит после ~{REMINDER_AFTER_HOURS} часов без активности."
    )


@dp.message(F.text.in_({"rem on", "rem off"}))
async def reminders_toggle(message: Message):
    user_id = message.from_user.id
    enable = (message.text == "rem on")
    set_reminders(user_id, enable)
    touch_active(user_id, "reminders_toggle", "on" if enable else "off")
    await message.answer("Готово ✅" if enable else "Ок, выключила ⛔️")


# -------------------------
# SUBSCRIPTION
# -------------------------
def sub_status_text(user_id: int) -> str:
    row = get_user(user_id)
    if not row or not row["sub_until"]:
        return "Подписка: **нет**"
    try:
        dt = datetime.fromisoformat(row["sub_until"])
        if dt < now_tz():
            return "Подписка: **истекла**"
        return f"Подписка активна до: **{dt.strftime('%d.%m.%Y %H:%M')}**"
    except Exception:
        return "Подписка: **неизвестно**"


@dp.message(F.text == "💳 Подписка")
async def subscription_menu(message: Message):
    user_id = message.from_user.id
    touch_active(user_id, "subscription_menu")

    info = sub_status_text(user_id)
    if PROVIDER_TOKEN:
        await message.answer(
            "💳 **Подписка**\n\n"
            f"{info}\n\n"
            f"Стоимость: **{SUB_PRICE_RUB} ₽ / {SUB_DAYS} дней**\n"
            "Чтобы оформить — напиши: **pay**\n"
            "Если оплата не нужна (тест) — админ может включить: **/sub30** (только себе)."
        )
    else:
        await message.answer(
            "💳 **Подписка**\n\n"
            f"{info}\n\n"
            "Оплата пока не подключена (нет PROVIDER_TOKEN).\n"
            "Но функционал подписки уже готов: как только добавим PROVIDER_TOKEN — заработает команда **pay**."
        )


@dp.message(Command("sub30"))
async def admin_gift_sub(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    set_subscription(message.from_user.id, 30)
    await message.answer("Ок ✅ Подписка активирована на 30 дней (только тебе).")


@dp.message(F.text == "pay")
async def pay_subscription(message: Message):
    user_id = message.from_user.id
    touch_active(user_id, "pay_click")

    if not PROVIDER_TOKEN:
        await message.answer("Оплата не подключена: добавь PROVIDER_TOKEN в Railway Variables.")
        return

    prices = [LabeledPrice(label=f"Подписка {SUB_DAYS} дней", amount=SUB_PRICE_RUB * 100)]
    await bot.send_invoice(
        chat_id=message.chat.id,
        title="Подписка MAK Online",
        description=f"Доступ к расширенным функциям на {SUB_DAYS} дней",
        payload=f"sub:{user_id}:{SUB_DAYS}",
        provider_token=PROVIDER_TOKEN,
        currency="RUB",
        prices=prices,
    )


@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery):
    # Telegram requires OK within 10 seconds
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    user_id = message.from_user.id
    touch_active(user_id, "payment_success")

    payload = message.successful_payment.invoice_payload or ""
    # expecting "sub:user_id:days"
    days = SUB_DAYS
    try:
        parts = payload.split(":")
        if len(parts) == 3 and parts[0] == "sub":
            days = int(parts[2])
    except Exception:
        pass

    set_subscription(user_id, days)
    await message.answer(f"Оплата прошла ✅ Подписка активирована на **{days} дней**.")


# =========================
# SCHEDULERS
# =========================
async def scheduler_card_of_day():
    """
    Sends daily card at CARD_OF_DAY_HOUR local TZ to all users.
    """
    while True:
        try:
            today = now_tz().date()
            now_local = now_tz()

            # run close to every minute boundary
            if now_local.hour == CARD_OF_DAY_HOUR and now_local.minute in (0, 1):
                for u in iter_users():
                    user_id = int(u["user_id"])
                    last_sent = u["last_daily_sent"]
                    if last_sent == today.isoformat():
                        continue

                    # Optional: require subscription for daily card (toggle here)
                    # If you want daily card only for subscribers -> uncomment:
                    # if not is_subscribed(user_id):
                    #     continue

                    card = pick_card()
                    LAST_CARD[user_id] = card["id"]

                    try:
                        await bot.send_message(
                            user_id,
                            "☀️ **Карта дня**\n\n" + card["text"]
                        )
                        touch_active(user_id, "daily_sent", card["id"])
                        log_weekly(user_id, card["id"], card["title"], mood=None, note=None)
                        set_last_daily_sent(user_id, today)
                    except Exception:
                        # user blocked bot or unreachable
                        pass

            await asyncio.sleep(45)
        except Exception:
            await asyncio.sleep(5)


async def scheduler_reminders():
    """
    Sends a gentle reminder if inactive for REMINDER_AFTER_HOURS.
    """
    while True:
        try:
            cutoff = now_tz() - timedelta(hours=REMINDER_AFTER_HOURS)
            now_local = now_tz()

            for u in iter_users():
                user_id = int(u["user_id"])
                if int(u["reminders_enabled"]) != 1:
                    continue

                # check inactive
                try:
                    last_active = datetime.fromisoformat(u["last_active"])
                except Exception:
                    continue

                if last_active > cutoff:
                    continue

                # avoid spamming: at most once per 24h
                last_rem = u["last_reminder_sent"]
                if last_rem:
                    try:
                        dt = datetime.fromisoformat(last_rem)
                        if dt > (now_local - timedelta(hours=24)):
                            continue
                    except Exception:
                        pass

                # gentle reminder text
                text = (
                    "🌿 Я рядом.\n\n"
                    "Если хочешь — можно взять маленькую паузу: выбери карту дня или открой состояние недели.\n\n"
                    "Напиши: **🌿 Выбрать карту** или **🧠 Состояние недели**"
                )

                try:
                    await bot.send_message(user_id, text)
                    touch_active(user_id, "reminder_sent")
                    set_last_reminder_sent(user_id, now_local)
                except Exception:
                    pass

            await asyncio.sleep(REMINDER_CHECK_EVERY_MIN * 60)
        except Exception:
            await asyncio.sleep(10)


# =========================
# MAIN
# =========================
async def main():
    init_db()
    await start_web_server()

    # start schedulers
    asyncio.create_task(scheduler_card_of_day())
    asyncio.create_task(scheduler_reminders())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

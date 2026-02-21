import os
import asyncio
import random
from datetime import datetime, timedelta, timezone
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart


# =========================
# Config
# =========================

UTC = timezone.utc

def now_utc() -> datetime:
    return datetime.now(tz=UTC)

def get_token() -> str:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not set in environment variables")
    return token

def get_admin_id() -> int | None:
    raw = os.getenv("ADMIN_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


# =========================
# Cards (10)
# =========================

CARDS = [
    {
        "title": "Тихий лес",
        "text": "Иногда самый верный шаг — замедлиться и услышать, что ты давно игнорируешь.",
        "question": "Что внутри тебя просит тишины прямо сейчас?",
    },
    {
        "title": "Тёплое окно",
        "text": "Даже маленький свет - это знак: тебе есть куда вернуться.",
        "question": "Где твоя «точка света» сегодня?",
    },
    {
        "title": "Ключ в кармане",
        "text": "Ресурс часто ближе, чем кажется. Он не громкий — он твой.",
        "question": "Какой ресурс ты недооцениваешь?",
    },
    {
        "title": "Дорога",
        "text": "Не обязательно знать весь путь. Достаточно следующего шага.",
        "question": "Какой самый маленький следующий шаг возможен сегодня?",
    },

    # +6 новых (итого 10)
    {
        "title": "Чашка чая",
        "text": "Иногда забота — не подвиг. Это простая пауза, которая возвращает тебя к себе.",
        "question": "Что ты можешь сделать для себя за пару минут — без чувства вины?",
    },
    {
        "title": "Лёгкий туман",
        "text": "Когда всё размыто — это не потеря пути. Это знак, что сейчас нужен ориентир, а не скорость.",
        "question": "Какая одна вещь сегодня может быть твоим ориентиром?",
    },
    {
        "title": "Мягкий плед",
        "text": "Тепло — это граница. Ты можешь укрыть себя, не объясняясь и не оправдываясь.",
        "question": "Где тебе нужна граница, чтобы стало теплее внутри?",
    },
    {
        "title": "Фонарь",
        "text": "Тебе не нужно освещать весь путь. Достаточно подсветить один шаг — и тело выдохнет.",
        "question": "Какой шаг ты готов(а) подсветить прямо сейчас?",
    },
    {
        "title": "Сад",
        "text": "Рост бывает тихим. Ты уже делаешь больше, чем замечаешь.",
        "question": "Что в тебе растёт — даже если пока незаметно?",
    },
    {
        "title": "Письмо себе",
        "text": "То, как ты говоришь с собой, — это твоя внутренняя атмосфера. Её можно менять.",
        "question": "Какая одна фраза поддержки тебе нужна сегодня?",
    },
]


# =========================
# UI (keyboards)
# =========================

def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌿 Выбрать карту")],
            [KeyboardButton(text="⭐ В избранное"), KeyboardButton(text="📌 Мои избранные")],
            [KeyboardButton(text="🌬 Дыхание"), KeyboardButton(text="🧠 Разобрать состояние")],
            [KeyboardButton(text="📒 Дневник"), KeyboardButton(text="🏠 Меню")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери действие…",
    )

def after_card_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌿 Ещё карту")],
            [KeyboardButton(text="⭐ В избранное"), KeyboardButton(text="📌 Мои избранные")],
            [KeyboardButton(text="🏠 Меню")],
        ],
        resize_keyboard=True,
    )

def after_breath_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔁 Повторить дыхание")],
            [KeyboardButton(text="🏠 Меню")],
        ],
        resize_keyboard=True,
    )

def after_questions_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Я ответила"), KeyboardButton(text="🏠 Меню")],
        ],
        resize_keyboard=True,
    )


# =========================
# In-memory state
# =========================

WAITING_DIARY: set[int] = set()

# user_id -> last shown card index
LAST_CARD_IDX: dict[int, int] = {}

# избранное: user_id -> list of {ts, title}
FAVORITES: dict[int, list[dict]] = {}

# stats
ADMIN_ID = get_admin_id()
USER_SEEN: set[int] = set()
STARTS_PER_USER: dict[int, int] = {}
TOTAL_STARTS = 0
TOTAL_CARD_DRAWS = 0
TOTAL_FAVORITES_ADDED = 0


# =========================
# Web server (safe to keep)
# =========================

async def handle_root(request):
    return web.Response(text="OK")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()


# =========================
# Helpers
# =========================

def format_card(card: dict) -> str:
    return (
        f"🃏 *{card['title']}*\n\n"
        f"{card['text']}\n\n"
        f"🔎 _Вопрос:_ {card['question']}"
    )

def cleanup_old_favorites(user_id: int):
    """Keep only last 7 days."""
    week_ago = now_utc() - timedelta(days=7)
    items = FAVORITES.get(user_id, [])
    items = [x for x in items if x["ts"] >= week_ago]
    FAVORITES[user_id] = items

def is_favorited(user_id: int, title: str) -> bool:
    cleanup_old_favorites(user_id)
    return any(x["title"] == title for x in FAVORITES.get(user_id, []))

def add_favorite(user_id: int, title: str):
    global TOTAL_FAVORITES_ADDED
    cleanup_old_favorites(user_id)
    FAVORITES.setdefault(user_id, [])
    if not is_favorited(user_id, title):
        FAVORITES[user_id].append({"ts": now_utc(), "title": title})
        TOTAL_FAVORITES_ADDED += 1

def remove_favorite(user_id: int, title: str):
    cleanup_old_favorites(user_id)
    items = FAVORITES.get(user_id, [])
    FAVORITES[user_id] = [x for x in items if x["title"] != title]


# =========================
# Bot
# =========================

dp = Dispatcher()

@dp.message(CommandStart())
async def start(message: Message):
    global TOTAL_STARTS
    uid = message.from_user.id

    TOTAL_STARTS += 1
    STARTS_PER_USER[uid] = STARTS_PER_USER.get(uid, 0) + 1
    USER_SEEN.add(uid)

    WAITING_DIARY.discard(uid)

    await message.answer(
        "Бот запущен ✅\n\nВыбирай действие кнопками ниже.",
        reply_markup=main_menu_kb(),
    )

@dp.message(F.text == "🏠 Меню")
async def menu(message: Message):
    uid = message.from_user.id
    WAITING_DIARY.discard(uid)
    await message.answer("Меню:", reply_markup=main_menu_kb())


# --- Card ---
@dp.message(F.text.in_({"🌿 Выбрать карту", "🌿 Ещё карту"}))
async def pick_card(message: Message):
    global TOTAL_CARD_DRAWS
    uid = message.from_user.id
    WAITING_DIARY.discard(uid)

    idx = random.randrange(len(CARDS))
    LAST_CARD_IDX[uid] = idx
    TOTAL_CARD_DRAWS += 1

    await message.answer(
        format_card(CARDS[idx]),
        parse_mode="Markdown",
        reply_markup=after_card_kb(),
    )

# --- Favorites toggle (adds/removes last card) ---
@dp.message(F.text == "⭐ В избранное")
async def favorite_toggle(message: Message):
    uid = message.from_user.id
    WAITING_DIARY.discard(uid)

    if uid not in LAST_CARD_IDX:
        await message.answer(
            "Сначала выбери карту: нажми «🌿 Выбрать карту».",
            reply_markup=main_menu_kb(),
        )
        return

    card = CARDS[LAST_CARD_IDX[uid]]
    title = card["title"]

    if is_favorited(uid, title):
        remove_favorite(uid, title)
        await message.answer(f"Убрала из избранного: «{title}»", reply_markup=after_card_kb())
    else:
        add_favorite(uid, title)
        await message.answer(f"Добавила в избранное: «{title}»", reply_markup=after_card_kb())

@dp.message(F.text == "📌 Мои избранные")
async def my_favorites(message: Message):
    uid = message.from_user.id
    WAITING_DIARY.discard(uid)

    cleanup_old_favorites(uid)
    items = FAVORITES.get(uid, [])

    if not items:
        await message.answer(
            "Пока нет избранных карт за последние 7 дней.\n\n"
            "Выбери карту и нажми «⭐ В избранное».",
            reply_markup=main_menu_kb(),
        )
        return

    # newest first
    items_sorted = sorted(items, key=lambda x: x["ts"], reverse=True)
    lines = []
    for x in items_sorted:
        dt = x["ts"].astimezone(UTC).strftime("%d.%m %H:%M")
        lines.append(f"• {x['title']}  _(добавлено {dt} UTC)_")

    text = "📌 *Избранные за 7 дней:*\n\n" + "\n".join(lines)
    await message.answer(text, parse_mode="Markdown", reply_markup=main_menu_kb())


# --- Breathing ---
BREATH_TEXT = (
    "🌬 *Дыхание 4–6 (2 минуты)*\n\n"
    "1) Вдох носом на *4* счёта\n"
    "2) Выдох ртом на *6* счётов\n"
    "3) Повтори *10 циклов*\n\n"
    "Если кружится голова — уменьши счёт до 3–4."
)

@dp.message(F.text.in_({"🌬 Дыхание", "🔁 Повторить дыхание"}))
async def breath(message: Message):
    uid = message.from_user.id
    WAITING_DIARY.discard(uid)
    await message.answer(BREATH_TEXT, parse_mode="Markdown", reply_markup=after_breath_kb())


# --- Questions (self-reflection) ---
QUESTIONS_TEXT = (
    "🧠 *Разобрать состояние (коротко)*\n\n"
    "Ответь (можно одной фразой на каждый пункт):\n"
    "1) Что сейчас происходит? *Факты без оценок.*\n"
    "2) Какая мысль/страх звучит громче всего?\n"
    "3) Какие эмоции (0–10) и где в теле ощущаются?\n"
    "4) Какой самый заботливый шаг на *2 минуты*?\n\n"
    "Напиши ответы одним сообщением."
)

@dp.message(F.text == "🧠 Разобрать состояние")
async def questions(message: Message):
    uid = message.from_user.id
    WAITING_DIARY.discard(uid)
    await message.answer(QUESTIONS_TEXT, parse_mode="Markdown", reply_markup=after_questions_kb())

@dp.message(F.text == "✅ Я ответила")
async def answered(message: Message):
    uid = message.from_user.id
    WAITING_DIARY.discard(uid)
    await message.answer("Принято ✅\n\nХочешь — выбери карту или вернись в меню.", reply_markup=main_menu_kb())


# --- Diary ---
@dp.message(F.text == "📒 Дневник")
async def diary_start(message: Message):
    uid = message.from_user.id
    WAITING_DIARY.add(uid)
    await message.answer(
        "📒 Дневник\n\nНапиши текстом:\n"
        "• что со мной сейчас\n"
        "• что мне нужно\n"
        "• один маленький шаг\n\n"
        "Я подтвержу запись (пока без базы).",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="🏠 Меню")]],
            resize_keyboard=True,
        ),
    )

@dp.message()
async def fallback(message: Message):
    uid = message.from_user.id

    if uid in WAITING_DIARY:
        WAITING_DIARY.discard(uid)
        await message.answer("Записано ✅\n\nВыбирай дальше:", reply_markup=main_menu_kb())
        return

    await message.answer("Я понимаю команды через кнопки 👇", reply_markup=main_menu_kb())


# --- Admin stats ---
@dp.message(F.text == "/stats")
async def stats(message: Message):
    uid = message.from_user.id
    if ADMIN_ID is None:
        await message.answer("ADMIN_ID не задан в переменных окружения. Добавь ADMIN_ID и перезапусти.")
        return
    if uid != ADMIN_ID:
        await message.answer("⛔️ Эта команда доступна только администратору.")
        return

    total_users = len(USER_SEEN)
    returning_users = sum(1 for k, v in STARTS_PER_USER.items() if v >= 2)

    # сколько пользователей имеют избранное за 7 дней
    fav_users = 0
    for user_id in list(FAVORITES.keys()):
        cleanup_old_favorites(user_id)
        if FAVORITES.get(user_id):
            fav_users += 1

    text = (
        "📊 *Статистика (только ты)*\n\n"
        f"👥 Уникальных пользователей: *{total_users}*\n"
        f"🔁 Вернулось (запускали /start ≥ 2): *{returning_users}*\n\n"
        f"▶️ Всего /start: *{TOTAL_STARTS}*\n"
        f"🃏 Выдач карт: *{TOTAL_CARD_DRAWS}*\n"
        f"⭐ Добавлений в избранное: *{TOTAL_FAVORITES_ADDED}*\n"
        f"📌 Пользователей с избранным за 7 дней: *{fav_users}*\n"
    )
    await message.answer(text, parse_mode="Markdown")


async def main():
    bot = Bot(token=get_token())
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

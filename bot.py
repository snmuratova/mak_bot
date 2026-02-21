import os
import asyncio
from aiohttp import web

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import CommandStart


# --- HTTP server (Railway/Render любят открытый порт) ---
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


# --- Telegram bot ---
dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer("Бот запущен ✅")


async def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set in environment variables")

    bot = Bot(token=token)

    # ✅ важно: если раньше был webhook, polling без этого не работает
    await bot.delete_webhook(drop_pending_updates=True)

    await start_web_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

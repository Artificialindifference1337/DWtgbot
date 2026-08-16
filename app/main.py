import asyncio, logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from app.config import get_settings
from app.handlers import router
from app.init_db import init_db
from app.jobs import build_scheduler
from app.middleware import PlaytimeMiddleware

async def main():
    settings=get_settings(); logging.basicConfig(level=settings.log_level)
    if not settings.bot_token: raise RuntimeError("BOT_TOKEN is missing")
    await init_db()
    bot=Bot(settings.bot_token,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp=Dispatcher()
    playtime_middleware = PlaytimeMiddleware()
    dp.message.outer_middleware(playtime_middleware)
    dp.callback_query.outer_middleware(playtime_middleware)
    dp.include_router(router)
    scheduler=build_scheduler(); scheduler.start()
    try: await dp.start_polling(bot)
    finally: scheduler.shutdown(wait=False); await bot.session.close()

if __name__=="__main__": asyncio.run(main())

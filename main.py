import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import settings
from app.db import Base, engine, AsyncSessionLocal
from app.routers import start, admin, shipment, receipt, reports, constants
from app.services.seed import seed_initial_data


async def on_startup() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        await seed_initial_data(session)


async def main() -> None:
    await on_startup()

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(shipment.router)
    dp.include_router(receipt.router)
    dp.include_router(reports.router)
    dp.include_router(constants.router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())



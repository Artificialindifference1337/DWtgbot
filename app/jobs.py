from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.constants import MARKET_UPDATE_SECONDS, PLAYTIME_TICK_SECONDS
from app.database import SessionLocal
from app.playtime import accrue_playtime
from app.services import expire_boosts, process_due_loans, update_prices


async def market_job():
    async with SessionLocal() as session:
        await update_prices(session)
        await session.commit()


async def loan_job():
    async with SessionLocal() as session:
        await process_due_loans(session)
        await session.commit()


async def boost_job():
    async with SessionLocal() as session:
        await expire_boosts(session)
        await session.commit()


async def playtime_job():
    async with SessionLocal() as session:
        await accrue_playtime(session)
        await session.commit()


def build_scheduler():
    scheduler = AsyncIOScheduler(timezone="Europe/Amsterdam")
    scheduler.add_job(
        market_job,
        "cron",
        minute="*/2",
        second=0,
        id="market",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(loan_job, "interval", minutes=30, id="loans", replace_existing=True)
    scheduler.add_job(boost_job, "interval", minutes=10, id="boosts", replace_existing=True)
    scheduler.add_job(
        playtime_job,
        "interval",
        seconds=PLAYTIME_TICK_SECONDS,
        id="playtime",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler

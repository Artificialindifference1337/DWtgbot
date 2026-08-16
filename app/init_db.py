import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import get_settings
from app.constants import DRUGS
from app.database import SessionLocal, engine
from app.models import Base, DailyMarketPrice, Drug, MarketHistory, MarketPrice, MarketState


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    today = datetime.now(timezone.utc).astimezone(ZoneInfo(get_settings().game_timezone)).date()
    async with SessionLocal() as session:
        for code, (name, base, minimum, maximum) in DRUGS.items():
            drug = await session.scalar(select(Drug).where(Drug.code == code))
            if not drug:
                drug = Drug(
                    code=code,
                    display_name=name,
                    base_price=base,
                    minimum_price=minimum,
                    maximum_price=maximum,
                )
                session.add(drug)
                await session.flush()
                session.add(
                    MarketPrice(drug_id=drug.id, current_price=base, previous_price=base)
                )
            else:
                # Keep existing installations in sync with the English UI names.
                drug.display_name = name

            market_price = await session.get(MarketPrice, drug.id)
            state = await session.get(MarketState, drug.id)
            if state is None:
                session.add(MarketState(drug_id=drug.id, regime="SIDEWAYS", momentum=0, regime_until=datetime.now(timezone.utc) + timedelta(minutes=30)))
            history_exists = await session.scalar(select(MarketHistory.id).where(MarketHistory.drug_id == drug.id).limit(1))
            if market_price and history_exists is None:
                session.add(MarketHistory(drug_id=drug.id, price=market_price.current_price, recorded_at=datetime.now(timezone.utc)))
            if market_price:
                snapshot = await session.get(DailyMarketPrice, (drug.id, today))
                if snapshot:
                    snapshot.closing_price = market_price.current_price
                else:
                    session.add(
                        DailyMarketPrice(
                            drug_id=drug.id,
                            price_date=today,
                            closing_price=market_price.current_price,
                        )
                    )
        await session.commit()


if __name__ == "__main__":
    asyncio.run(init_db())

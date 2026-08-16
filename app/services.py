from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.constants import (
    DAILY_BASE,
    DAILY_CAP_DAYS,
    DAILY_INCREMENT,
    LOAN_HOURS,
    LOAN_MAX_PRINCIPAL,
    LOAN_RATE,
    MONEY_BOOST_LIMIT,
    RAID_COOLDOWN_SECONDS,
    RAID_FAILURE_FINE,
    RAID_SUCCESS_RATE,
    STARTING_MONEY,
    TOKEN_DAILY_MAX,
    TOKEN_DAILY_MIN,
    TOKEN_FIND_CHANCE,
    TOKEN_RAID_CHANCE,
    TOKEN_WEEK_STREAK_BONUS,
)
from app.models import (
    Boost,
    DailyLogin,
    DailyMarketPrice,
    Drug,
    Inventory,
    Loan,
    MarketHistory,
    MarketNews,
    MarketPrice,
    MarketState,
    Player,
    PlayerAchievement,
    PlayerProgress,
    PlayerUpgrade,
    ProcessedCallback,
    Raid,
    TokenLedger,
    TradeRecord,
)


CENT = Decimal("0.01")


def money(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Normalize SQLite/PostgreSQL datetimes to timezone-aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class GameError(Exception):
    """Raised when a player action violates a game rule."""


def raid_cooldown_remaining(player: Player) -> timedelta:
    if not player.last_raid_at:
        return timedelta(0)

    available_at = as_utc(player.last_raid_at) + timedelta(
        seconds=RAID_COOLDOWN_SECONDS
    )
    return max(available_at - utcnow(), timedelta(0))


async def get_or_create_player(
    session: AsyncSession,
    user_id: int,
    username: str | None,
    display_name: str,
) -> Player:
    """Return a player and guarantee that its DailyLogin row exists.

    PostgreSQL enforces the foreign key immediately, so the parent Player row
    must be flushed before DailyLogin can reference it.
    """
    player = await session.get(Player, user_id)

    if player is None:
        player = Player(
            user_id=user_id,
            username=username,
            display_name=display_name,
            money=STARTING_MONEY,
        )
        session.add(player)

        # Critical for PostgreSQL: persist the parent row first.
        await session.flush()

        session.add(DailyLogin(user_id=user_id))
        await session.flush()

        return player

    player.username = username
    player.display_name = display_name

    # Repair legacy/incomplete accounts safely.
    daily_login = await session.get(DailyLogin, user_id)
    if daily_login is None:
        session.add(DailyLogin(user_id=user_id))
        await session.flush()

    return player


async def claim_daily(
    session: AsyncSession,
    player: Player,
    today,
) -> Decimal:
    row = await session.get(DailyLogin, player.user_id)

    if row is None:
        row = DailyLogin(user_id=player.user_id)
        session.add(row)
        await session.flush()

    if row.last_claim_date == today:
        return Decimal("0.00")

    yesterday = today - timedelta(days=1)

    if row.last_claim_date == yesterday:
        row.consecutive_days += 1
    else:
        row.consecutive_days = 1

    row.last_claim_date = today

    bonus = DAILY_BASE + DAILY_INCREMENT * min(
        max(row.consecutive_days - 1, 0),
        DAILY_CAP_DAYS - 1,
    )

    player.money = money(player.money + bonus)
    return money(bonus)


async def inventory_total(
    session: AsyncSession,
    user_id: int,
) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(Inventory.quantity), 0)).where(
            Inventory.user_id == user_id
        )
    )
    return int(total or 0)


async def buy(
    session: AsyncSession,
    player: Player,
    drug_code: str,
    quantity: int,
) -> Decimal:
    if quantity < 1 or quantity > 100:
        raise GameError("Invalid quantity.")

    drug = await session.scalar(
        select(Drug).where(Drug.code == drug_code)
    )
    if not drug:
        raise GameError("Product not found.")

    price = await session.get(MarketPrice, drug.id)
    if not price:
        raise GameError("Product not found.")

    current_inventory = await inventory_total(session, player.user_id)
    if current_inventory + quantity > player.inventory_capacity:
        raise GameError("Inventory is full.")

    total = money(Decimal(price.current_price) * quantity)

    if Decimal(player.money) < total:
        raise GameError("Insufficient funds.")

    inv = await session.get(
        Inventory,
        (player.user_id, drug.id),
    )

    if inv is None:
        inv = Inventory(
            user_id=player.user_id,
            drug_id=drug.id,
            quantity=0,
            average_purchase_price=Decimal("0.00"),
        )
        session.add(inv)

    old_cost = Decimal(inv.quantity) * Decimal(inv.average_purchase_price)
    inv.quantity += quantity
    inv.average_purchase_price = money(
        (old_cost + total) / inv.quantity
    )

    player.money = money(Decimal(player.money) - total)

    session.add(
        TradeRecord(
            user_id=player.user_id,
            drug_id=drug.id,
            side="BUY",
            quantity=quantity,
            unit_price=price.current_price,
            total_value=total,
        )
    )

    return total


async def active_boost(
    session: AsyncSession,
    user_id: int,
    boost_type: str,
) -> Boost | None:
    return await session.scalar(
        select(Boost).where(
            Boost.user_id == user_id,
            Boost.boost_type == boost_type,
            Boost.active.is_(True),
            Boost.expires_at > utcnow(),
        )
    )


async def sell(
    session: AsyncSession,
    player: Player,
    drug_code: str,
    quantity: int,
) -> Decimal:
    drug = await session.scalar(
        select(Drug).where(Drug.code == drug_code)
    )
    if not drug:
        raise GameError("Product not found.")

    inv = await session.get(
        Inventory,
        (player.user_id, drug.id),
    )

    if inv is None or quantity < 1 or inv.quantity < quantity:
        raise GameError("Insufficient inventory.")

    price = await session.get(MarketPrice, drug.id)
    if price is None:
        raise GameError("Market price unavailable.")

    proceeds = money(Decimal(price.current_price) * quantity)

    if await active_boost(
        session,
        player.user_id,
        "PRICE_BOOST",
    ):
        proceeds = money(proceeds * Decimal("1.20"))

    money_boost = await active_boost(
        session,
        player.user_id,
        "MONEY_BOOST",
    )

    if money_boost:
        remaining = max(
            Decimal("0.00"),
            MONEY_BOOST_LIMIT - Decimal(money_boost.money_bonus_used),
        )
        extra = min(
            money(proceeds * Decimal("0.50")),
            remaining,
        )
        proceeds = money(proceeds + extra)
        money_boost.money_bonus_used = money(
            Decimal(money_boost.money_bonus_used) + extra
        )

    inv.quantity -= quantity
    player.money = money(Decimal(player.money) + proceeds)

    session.add(
        TradeRecord(
            user_id=player.user_id,
            drug_id=drug.id,
            side="SELL",
            quantity=quantity,
            unit_price=price.current_price,
            total_value=proceeds,
        )
    )

    return proceeds


async def take_loan(
    session: AsyncSession,
    player: Player,
    principal: Decimal,
) -> Loan:
    principal = money(principal)

    if principal < Decimal("100.00"):
        raise GameError("The minimum loan is €100.00.")

    active = await session.scalar(
        select(func.coalesce(func.sum(Loan.principal), 0)).where(
            Loan.user_id == player.user_id,
            Loan.status == "ACTIVE",
        )
    )

    if Decimal(active or 0) + principal > LOAN_MAX_PRINCIPAL:
        raise GameError(
            "The maximum active loan principal is €2,000.00."
        )

    due = money(
        principal * (Decimal("1.00") + LOAN_RATE)
    )

    loan = Loan(
        user_id=player.user_id,
        principal=principal,
        total_due=due,
        due_at=utcnow() + timedelta(hours=LOAN_HOURS),
    )

    session.add(loan)
    player.money = money(Decimal(player.money) + principal)

    return loan


async def buy_boost(
    session: AsyncSession,
    player: Player,
    boost_type: str,
) -> Boost:
    costs = {
        "MONEY_BOOST": 5,
        "RAID_SHIELD": 3,
        "PRICE_BOOST": 4,
    }

    if boost_type not in costs:
        raise GameError("Unknown boost.")

    if await active_boost(
        session,
        player.user_id,
        boost_type,
    ):
        raise GameError("This boost is already active.")

    if player.tokens < costs[boost_type]:
        raise GameError("Insufficient tokens.")

    player.tokens -= costs[boost_type]

    boost = Boost(
        user_id=player.user_id,
        boost_type=boost_type,
        expires_at=utcnow() + timedelta(hours=24),
        remaining_uses=1 if boost_type == "RAID_SHIELD" else None,
    )

    session.add(boost)
    return boost


async def raid(
    session: AsyncSession,
    attacker: Player,
    defender_id: int,
) -> tuple[str, Decimal]:
    defender = await session.get(Player, defender_id)

    if not defender or defender.user_id == attacker.user_id:
        raise GameError("Invalid target.")

    if attacker.money < 100 or defender.money < 100:
        raise GameError(
            "Both players must have at least €100.00."
        )

    cooldown = raid_cooldown_remaining(attacker)

    if cooldown > timedelta(0):
        total_seconds = max(
            0,
            int(cooldown.total_seconds()),
        )
        minutes, seconds = divmod(total_seconds, 60)

        raise GameError(
            f"Raid cooldown active: "
            f"{minutes:02d}:{seconds:02d} remaining."
        )

    attacker.last_raid_at = utcnow()
    attacker.total_raids += 1

    shield = await active_boost(
        session,
        defender.user_id,
        "RAID_SHIELD",
    )

    if shield:
        shield.active = False
        shield.remaining_uses = 0

        session.add(
            Raid(
                attacker_id=attacker.user_id,
                defender_id=defender.user_id,
                outcome="BLOCKED",
            )
        )

        return "BLOCKED", Decimal("0.00")

    if Decimal(str(random.random())) <= RAID_SUCCESS_RATE:
        stolen = money(
            min(
                Decimal(defender.money) * Decimal("0.10"),
                Decimal("500.00"),
            )
        )

        defender.money = money(
            Decimal(defender.money) - stolen
        )
        attacker.money = money(
            Decimal(attacker.money) + stolen
        )

        attacker.successful_raids += 1
        attacker.total_raid_gains = money(
            Decimal(attacker.total_raid_gains) + stolen
        )
        defender.total_raid_losses = money(
            Decimal(defender.total_raid_losses) + stolen
        )

        session.add(
            Raid(
                attacker_id=attacker.user_id,
                defender_id=defender.user_id,
                outcome="SUCCESS",
                money_stolen=stolen,
            )
        )

        return "SUCCESS", stolen

    attacker.money = money(
        Decimal(attacker.money) - RAID_FAILURE_FINE
    )

    session.add(
        Raid(
            attacker_id=attacker.user_id,
            defender_id=defender.user_id,
            outcome="FAILURE",
            fine_paid=RAID_FAILURE_FINE,
        )
    )

    return "FAILURE", RAID_FAILURE_FINE


async def mark_callback(
    session: AsyncSession,
    callback_id: str,
    user_id: int,
    data: str,
) -> bool:
    if await session.get(ProcessedCallback, callback_id):
        return False

    session.add(
        ProcessedCallback(
            callback_query_id=callback_id,
            user_id=user_id,
            callback_data=data,
        )
    )
    return True


def bounded_market_price(
    base: Decimal,
    value: Decimal,
) -> Decimal:
    floor = money(base * Decimal("0.20"))
    ceiling = money(base * Decimal("20.00"))
    return min(
        ceiling,
        max(floor, money(value)),
    )


REGIME_SETTINGS = {
    "BULL": (0.020, 0.075),
    "BEAR": (-0.075, -0.020),
    "SIDEWAYS": (-0.025, 0.025),
    "VOLATILE": (-0.18, 0.18),
}


NEWS_BY_REGIME = {
    "BULL": (
        "Demand surge",
        "Buyers are chasing limited supply.",
    ),
    "BEAR": (
        "Supply glut",
        "Fresh stock is pushing market prices down.",
    ),
    "SIDEWAYS": (
        "Quiet trading",
        "The market is balanced with limited movement.",
    ),
    "VOLATILE": (
        "Market uncertainty",
        "Rumours and disrupted routes are causing sharp moves.",
    ),
}


EVENT_HEADLINES = [
    (
        "Police seize major shipment",
        "A large seizure has created an immediate shortage.",
    ),
    (
        "Port strike blocks supply",
        "Imports are delayed and dealers are bidding aggressively.",
    ),
    (
        "Secret laboratory shut down",
        "Production has abruptly fallen across the region.",
    ),
    (
        "Festival demand explodes",
        "Unexpected demand is draining available stock.",
    ),
]


def choose_regime() -> str:
    roll = random.random()

    if roll < 0.28:
        return "BULL"
    if roll < 0.56:
        return "BEAR"
    if roll < 0.86:
        return "SIDEWAYS"
    return "VOLATILE"


def regime_icon(regime: str) -> str:
    return {
        "BULL": "🔥",
        "BEAR": "❄️",
        "SIDEWAYS": "➖",
        "VOLATILE": "🌪️",
    }.get(regime, "➖")


def market_signal(
    current: Decimal,
    base: Decimal,
    momentum: Decimal,
    regime: str,
) -> str:
    ratio = current / base if base else Decimal("1.00")

    if ratio <= Decimal("0.65") and momentum >= 0:
        return "🟢 Strong buy"

    if (
        ratio <= Decimal("0.90")
        or (
            regime == "BULL"
            and momentum > Decimal("0.015")
        )
    ):
        return "🟢 Buy"

    if ratio >= Decimal("2.50") and momentum <= 0:
        return "🔴 Strong sell"

    if (
        ratio >= Decimal("1.35")
        or (
            regime == "BEAR"
            and momentum < Decimal("-0.015")
        )
    ):
        return "🔴 Sell"

    return "🟡 Hold"


def market_tick(
    current: Decimal,
    base: Decimal,
    regime: str,
    momentum: Decimal,
) -> tuple[Decimal, Decimal]:
    current = max(
        Decimal("0.01"),
        Decimal(current),
    )
    base = max(
        Decimal("0.01"),
        Decimal(base),
    )

    low, high = REGIME_SETTINGS.get(
        regime,
        REGIME_SETTINGS["SIDEWAYS"],
    )

    shock = Decimal(
        str(random.uniform(low, high))
    )

    momentum_component = (
        Decimal(momentum) * Decimal("0.42")
    )

    reversion_strength = (
        Decimal("0.10")
        if regime != "VOLATILE"
        else Decimal("0.06")
    )

    reversion = (
        (base - current) / base
    ) * reversion_strength

    total_return = (
        shock
        + momentum_component
        + reversion
    )

    total_return = max(
        Decimal("-0.35"),
        min(
            Decimal("0.35"),
            total_return,
        ),
    )

    new_price = bounded_market_price(
        base,
        current * (Decimal("1.00") + total_return),
    )

    realized = (
        new_price - current
    ) / current

    next_momentum = (
        Decimal(momentum) * Decimal("0.55")
        + realized * Decimal("0.45")
    )

    return (
        new_price,
        next_momentum.quantize(
            Decimal("0.00001")
        ),
    )


def normal_market_price(
    current: Decimal,
    base: Decimal,
    regime: str = "SIDEWAYS",
    momentum: Decimal = Decimal("0"),
) -> Decimal:
    """Compatibility helper used by tests and external callers."""
    return market_tick(
        current,
        base,
        regime,
        momentum,
    )[0]


def event_market_price(base: Decimal) -> Decimal:
    multiplier = Decimal(
        str(
            round(
                random.uniform(3.0, 12.0),
                2,
            )
        )
    )
    return bounded_market_price(
        base,
        base * multiplier,
    )


def event_market_tick(
    base: Decimal,
) -> tuple[Decimal, Decimal]:
    return (
        event_market_price(base),
        Decimal("0.25"),
    )


async def update_prices(
    session: AsyncSession,
) -> None:
    rows = (
        await session.execute(
            select(MarketPrice, Drug).join(
                Drug,
                Drug.id == MarketPrice.drug_id,
            )
        )
    ).all()

    now = utcnow()
    today = now.astimezone(
        ZoneInfo(get_settings().game_timezone)
    ).date()

    event_drug_id = (
        random.choice(rows)[1].id
        if rows and random.randrange(30) == 0
        else None
    )

    for market_price, drug in rows:
        state = await session.get(
            MarketState,
            drug.id,
        )

        if state is None:
            state = MarketState(
                drug_id=drug.id,
                regime=choose_regime(),
                momentum=Decimal("0"),
                regime_until=now
                + timedelta(
                    minutes=random.randint(20, 90)
                ),
            )
            session.add(state)

        elif as_utc(state.regime_until) <= now:
            state.regime = choose_regime()
            state.regime_until = (
                now
                + timedelta(
                    minutes=random.randint(20, 90)
                )
            )

            headline, details = NEWS_BY_REGIME[
                state.regime
            ]

            session.add(
                MarketNews(
                    drug_id=drug.id,
                    headline=(
                        f"{drug.display_name}: {headline}"
                    ),
                    details=details,
                    impact_percent=Decimal("0"),
                    expires_at=state.regime_until,
                )
            )

        current = Decimal(
            market_price.current_price
        )
        base = Decimal(drug.base_price)

        if drug.id == event_drug_id:
            new_price, next_momentum = (
                event_market_tick(base)
            )

            headline, details = random.choice(
                EVENT_HEADLINES
            )

            impact = (
                (new_price - current)
                / current
                * Decimal("100")
                if current
                else Decimal("0")
            )

            session.add(
                MarketNews(
                    drug_id=drug.id,
                    headline=(
                        f"🚨 {drug.display_name}: "
                        f"{headline}"
                    ),
                    details=details,
                    impact_percent=impact,
                    expires_at=(
                        now + timedelta(minutes=20)
                    ),
                )
            )

            state.regime = "VOLATILE"
            state.regime_until = (
                now + timedelta(minutes=20)
            )

        else:
            new_price, next_momentum = market_tick(
                current,
                base,
                state.regime,
                Decimal(state.momentum),
            )

        state.momentum = next_momentum

        market_price.previous_price = current
        market_price.current_price = new_price
        market_price.last_updated_at = now

        session.add(
            MarketHistory(
                drug_id=drug.id,
                price=new_price,
                recorded_at=now,
            )
        )

        snapshot = await session.get(
            DailyMarketPrice,
            (drug.id, today),
        )

        if snapshot:
            snapshot.closing_price = new_price
            snapshot.updated_at = now
        else:
            session.add(
                DailyMarketPrice(
                    drug_id=drug.id,
                    price_date=today,
                    closing_price=new_price,
                )
            )

    await session.execute(
        delete(MarketHistory).where(
            MarketHistory.recorded_at
            < now - timedelta(days=7)
        )
    )

    await session.execute(
        delete(MarketNews).where(
            MarketNews.expires_at
            < now - timedelta(days=1)
        )
    )


async def process_due_loans(
    session: AsyncSession,
) -> int:
    loans = (
        await session.scalars(
            select(Loan).where(
                Loan.status == "ACTIVE",
                Loan.due_at <= utcnow(),
            )
        )
    ).all()

    count = 0

    for loan in loans:
        player = await session.get(
            Player,
            loan.user_id,
        )

        if player is None:
            continue

        player.money = money(
            Decimal(player.money)
            - Decimal(loan.total_due)
        )

        loan.status = "PAID"
        loan.paid_at = utcnow()
        count += 1

    return count


async def expire_boosts(
    session: AsyncSession,
) -> int:
    boosts = (
        await session.scalars(
            select(Boost).where(
                Boost.active.is_(True),
                Boost.expires_at <= utcnow(),
            )
        )
    ).all()

    for boost in boosts:
        boost.active = False

    return len(boosts)


# ---------------------------------------------------------------------------
# Token economy / progression / Black Market
# ---------------------------------------------------------------------------

BLACK_MARKET_ITEMS = {
    "STORAGE": {
        "name": "Warehouse expansion (+25 slots)",
        "cost": 15,
        "kind": "upgrade",
    },
    "RAID_RESET": {
        "name": "Raid cooldown reset",
        "cost": 3,
        "kind": "instant",
    },
    "CASH_DROP": {
        "name": "Cash crate (€750)",
        "cost": 8,
        "kind": "instant",
    },
    "MONEY_BOOST": {
        "name": "Money Boost (24h)",
        "cost": 5,
        "kind": "boost",
    },
    "RAID_SHIELD": {
        "name": "Raid Shield",
        "cost": 3,
        "kind": "boost",
    },
    "PRICE_BOOST": {
        "name": "Price Boost (24h)",
        "cost": 4,
        "kind": "boost",
    },
}


async def change_tokens(
    session: AsyncSession,
    player: Player,
    amount: int,
    reason: str,
) -> None:
    if player.tokens + amount < 0:
        raise GameError("Insufficient tokens.")

    player.tokens += amount

    session.add(
        TokenLedger(
            user_id=player.user_id,
            amount=amount,
            reason=reason,
        )
    )


async def grant_xp(
    session: AsyncSession,
    player: Player,
    amount: int,
) -> tuple[int, int]:
    progress = await session.get(
        PlayerProgress,
        player.user_id,
    )

    if progress is None:
        progress = PlayerProgress(
            user_id=player.user_id,
            xp=0,
            level=1,
        )
        session.add(progress)

    old_level = progress.level
    progress.xp += max(0, amount)
    progress.level = 1 + progress.xp // 100

    levels_gained = (
        progress.level - old_level
    )

    if levels_gained > 0:
        await change_tokens(
            session,
            player,
            levels_gained * 3,
            "LEVEL_UP",
        )

    return (
        progress.level,
        levels_gained,
    )


async def maybe_trade_token_drop(
    session: AsyncSession,
    player: Player,
) -> bool:
    if random.random() < TOKEN_FIND_CHANCE:
        await change_tokens(
            session,
            player,
            1,
            "TRADE_DROP",
        )
        return True

    return False


async def maybe_raid_token_drop(
    session: AsyncSession,
    player: Player,
) -> bool:
    if random.random() < TOKEN_RAID_CHANCE:
        await change_tokens(
            session,
            player,
            1,
            "RAID_DROP",
        )
        return True

    return False


async def grant_daily_tokens(
    session: AsyncSession,
    player: Player,
    streak: int,
) -> int:
    amount = random.randint(
        TOKEN_DAILY_MIN,
        TOKEN_DAILY_MAX,
    )

    if (
        streak > 0
        and streak % 7 == 0
    ):
        amount += TOKEN_WEEK_STREAK_BONUS

    await change_tokens(
        session,
        player,
        amount,
        "DAILY_LOGIN",
    )

    return amount


async def buy_black_market_item(
    session: AsyncSession,
    player: Player,
    code: str,
) -> str:
    item = BLACK_MARKET_ITEMS.get(code)

    if not item:
        raise GameError(
            "Unknown Black Market item."
        )

    await change_tokens(
        session,
        player,
        -item["cost"],
        f"BLACK_MARKET_{code}",
    )

    if code == "STORAGE":
        player.inventory_capacity += 25

        upgrade = await session.get(
            PlayerUpgrade,
            (player.user_id, code),
        )

        if upgrade:
            upgrade.level += 1
        else:
            session.add(
                PlayerUpgrade(
                    user_id=player.user_id,
                    upgrade_code=code,
                    level=1,
                )
            )

    elif code == "RAID_RESET":
        player.last_raid_at = None

    elif code == "CASH_DROP":
        player.money = money(
            Decimal(player.money)
            + Decimal("750.00")
        )

    else:
        await buy_boost_without_charge(
            session,
            player,
            code,
        )

    return item["name"]


async def buy_boost_without_charge(
    session: AsyncSession,
    player: Player,
    boost_type: str,
) -> Boost:
    if await active_boost(
        session,
        player.user_id,
        boost_type,
    ):
        raise GameError(
            "This boost is already active."
        )

    boost = Boost(
        user_id=player.user_id,
        boost_type=boost_type,
        expires_at=utcnow() + timedelta(hours=24),
        remaining_uses=(
            1
            if boost_type == "RAID_SHIELD"
            else None
        ),
    )

    session.add(boost)
    return boost
from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from html import escape
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from app.config import get_settings
from app.constants import MARKET_UPDATE_SECONDS
from app.database import SessionLocal
from app.keyboards import back, drug_menu, main_menu
from app.models import DailyLogin, Drug, Inventory, MarketHistory, MarketNews, MarketPrice, MarketState, Player, PlayerProgress, TradeRecord
from app.playtime import admin_playtime_report, player_playtime
from app.services import (
    GameError,
    buy,
    BLACK_MARKET_ITEMS,
    buy_black_market_item,
    buy_boost,
    claim_daily,
    grant_daily_tokens,
    grant_xp,
    get_or_create_player,
    mark_callback,
    maybe_raid_token_drop,
    maybe_trade_token_drop,
    raid,
    raid_cooldown_remaining,
    regime_icon,
    market_signal,
    sell,
    take_loan,
    utcnow,
)

router = Router()

class TradeInput(StatesGroup):
    waiting_quantity = State()

_live_tasks: dict[tuple[int, int], asyncio.Task] = {}


def eur(value: Decimal | int) -> str:
    return f"€{Decimal(value):,.2f}"


def price_change(current: Decimal, previous: Decimal) -> str:
    if previous == 0:
        return ""
    percentage = ((current - previous) / previous) * Decimal("100")
    icon = "📈" if percentage > 0 else "📉" if percentage < 0 else "➖"
    return f" {icon} {percentage:+.1f}%"


def countdown_text(delta: timedelta, include_hours: bool = True) -> str:
    total_seconds = max(0, int(delta.total_seconds() + 0.999))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if include_hours else f"{minutes:02d}:{seconds:02d}"


def duration_text(seconds: int) -> str:
    total_minutes = seconds // 60
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def next_market_update_delta() -> timedelta:
    now = utcnow()
    epoch = int(now.timestamp())
    next_epoch = ((epoch // MARKET_UPDATE_SECONDS) + 1) * MARKET_UPDATE_SECONDS
    return timedelta(seconds=max(0, next_epoch - epoch))


def cancel_live_task(message) -> None:
    key = (message.chat.id, message.message_id)
    task = _live_tasks.pop(key, None)
    if task and task is not asyncio.current_task():
        task.cancel()


def register_live_task(message, coroutine) -> None:
    cancel_live_task(message)
    key = (message.chat.id, message.message_id)
    task = asyncio.create_task(coroutine)
    _live_tasks[key] = task

    def cleanup(done_task: asyncio.Task) -> None:
        if _live_tasks.get(key) is done_task:
            _live_tasks.pop(key, None)

    task.add_done_callback(cleanup)


def sparkline(values: list[Decimal]) -> str:
    bars = "▁▂▃▄▅▆▇█"
    if not values:
        return "—"
    low, high = min(values), max(values)
    if high == low:
        return bars[3] * len(values)
    return "".join(bars[min(7, int((value - low) / (high - low) * 7))] for value in values)


async def build_prices_screen() -> tuple[str, InlineKeyboardMarkup]:
    async with SessionLocal() as session:
        rows = (await session.execute(select(Drug, MarketPrice, MarketState).join(MarketPrice).outerjoin(MarketState))).all()
    lines = [
        "📊 <b>Market</b>",
        f"⏳ <b>{countdown_text(next_market_update_delta(), False)}</b> · tap a product for details",
        "",
    ]
    buttons = []
    for drug, price, state in rows:
        current = Decimal(price.current_price)
        previous = Decimal(price.previous_price)
        change = ((current - previous) / previous * Decimal("100")) if previous else Decimal("0")
        arrow = "▲" if change > 0 else "▼" if change < 0 else "•"
        regime = state.regime if state else "SIDEWAYS"
        lines.append(f"{regime_icon(regime)} <b>{drug.display_name}</b> {eur(current)} {arrow}{abs(change):.1f}%")
        buttons.append(InlineKeyboardButton(text=drug.display_name, callback_data=f"market:{drug.code}"))
    keyboard_rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    keyboard_rows.append([InlineKeyboardButton(text="📰 News", callback_data="nav:news"), InlineKeyboardButton(text="🏠 Main menu", callback_data="nav:home")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


async def build_market_detail(code: str) -> tuple[str, InlineKeyboardMarkup]:
    cutoff = utcnow() - timedelta(hours=24)
    async with SessionLocal() as session:
        row = (await session.execute(select(Drug, MarketPrice, MarketState).join(MarketPrice).outerjoin(MarketState).where(Drug.code == code))).first()
        if not row:
            return "Product not found.", back()
        drug, price, state = row
        history = list((await session.scalars(select(MarketHistory.price).where(MarketHistory.drug_id == drug.id, MarketHistory.recorded_at >= cutoff).order_by(MarketHistory.recorded_at.asc()))).all())
        volume = int((await session.scalar(select(func.coalesce(func.sum(TradeRecord.quantity), 0)).where(TradeRecord.drug_id == drug.id, TradeRecord.created_at >= cutoff))) or 0)
        news = (await session.execute(select(MarketNews).where(MarketNews.drug_id == drug.id).order_by(MarketNews.created_at.desc()).limit(2))).scalars().all()
    current = Decimal(price.current_price)
    previous = Decimal(price.previous_price)
    regime = state.regime if state else "SIDEWAYS"
    momentum = Decimal(state.momentum) if state else Decimal("0")
    high = max(history) if history else current
    low = min(history) if history else current
    recent = [Decimal(value) for value in history[-20:]]
    lines = [
        f"{regime_icon(regime)} <b>{drug.display_name}</b>",
        f"Price: <b>{eur(current)}</b>{price_change(current, previous)}",
        f"24h: {eur(low)} – {eur(high)}",
        f"Volume: {volume} units",
        f"Trend: {regime.title()}",
        f"Signal: {market_signal(current, Decimal(drug.base_price), momentum, regime)}",
        f"Chart: <code>{sparkline(recent)}</code>",
        "",
        f"⏳ Next update: <b>{countdown_text(next_market_update_delta(), False)}</b>",
    ]
    if news:
        lines += ["", "📰 <b>Latest news</b>"]
        lines.extend(f"• {escape(item.headline)}" for item in news)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Buy", callback_data=f"buy:{drug.code}"), InlineKeyboardButton(text="💰 Sell", callback_data=f"sell:{drug.code}")],
        [InlineKeyboardButton(text="📊 Back to market", callback_data="nav:prices"), InlineKeyboardButton(text="🏠 Main menu", callback_data="nav:home")],
    ])
    return "\n".join(lines), keyboard


async def live_prices(message) -> None:
    try:
        while True:
            text, keyboard = await build_prices_screen()
            try:
                await message.edit_text(text, reply_markup=keyboard)
            except TelegramBadRequest as error:
                if "message is not modified" not in str(error).lower():
                    break
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        return


async def build_raid_screen(user_id: int) -> tuple[str, InlineKeyboardMarkup, timedelta]:
    async with SessionLocal() as session:
        player = await session.get(Player, user_id)
        if player is None:
            return "Open /start first.", back(), timedelta(0)
        cooldown = raid_cooldown_remaining(player)
        if cooldown > timedelta(0):
            text = (
                "⏳ <b>Raid cooldown active</b>\n\n"
                f"You can raid again in: <b>{countdown_text(cooldown, False)}</b>"
            )
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🏠 Main menu", callback_data="nav:home")]]
            )
            return text, keyboard, cooldown
        targets = (
            await session.scalars(
                select(Player)
                .where(Player.user_id != player.user_id, Player.money >= 100)
                .order_by(Player.money.desc())
                .limit(10)
            )
        ).all()
        rows = [
            [
                InlineKeyboardButton(
                    text=f"🎯 {target.username or target.display_name} ({eur(target.money)})",
                    callback_data=f"raid:{target.user_id}",
                )
            ]
            for target in targets
        ]
        text = (
            "🎯 <b>Choose a target</b>\n60% success chance; failure costs €300.00."
            if targets
            else "🎯 <b>No eligible raid targets are available.</b>"
        )
        rows.append([InlineKeyboardButton(text="🏠 Main menu", callback_data="nav:home")])
        return text, InlineKeyboardMarkup(inline_keyboard=rows), timedelta(0)


async def live_raid(message, user_id: int) -> None:
    try:
        while True:
            text, keyboard, cooldown = await build_raid_screen(user_id)
            try:
                await message.edit_text(text, reply_markup=keyboard)
            except TelegramBadRequest as error:
                if "message is not modified" not in str(error).lower():
                    break
            if cooldown <= timedelta(0):
                break
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        return


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 Minutes per day per player", callback_data="admin:daily_player")],
            [InlineKeyboardButton(text="📊 Total minutes per day", callback_data="admin:daily_total")],
            [InlineKeyboardButton(text="👥 Total minutes per player", callback_data="admin:player_total")],
            [InlineKeyboardButton(text="Σ Grand total", callback_data="admin:grand")],
            [InlineKeyboardButton(text="🏠 Main menu", callback_data="nav:home")],
        ]
    )


def is_admin(user_id: int) -> bool:
    return user_id in get_settings().admins


@router.message(CommandStart())
async def start(message: Message) -> None:
    async with SessionLocal() as session:
        player = await get_or_create_player(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
        today = message.date.astimezone(ZoneInfo(get_settings().game_timezone)).date()
        bonus = await claim_daily(session, player, today)
        token_bonus = 0
        if bonus:
            login = await session.get(DailyLogin, player.user_id)
            token_bonus = await grant_daily_tokens(session, player, login.consecutive_days if login else 1)
        await session.commit()
    text = "🎮 <b>Drug Wars</b>\nBuild your trading empire."
    if bonus:
        text += f"\n\n✅ Daily bonus: {eur(bonus)} + 🔑 {token_bonus}"
    await message.answer(text, reply_markup=main_menu())


@router.message(Command("admin"))
async def admin_command(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("⛔ You do not have access to the admin panel.")
        return
    await message.answer("🛡️ <b>Playtime administration</b>", reply_markup=admin_keyboard())


@router.callback_query(F.data.startswith("nav:"))
async def navigate(callback: CallbackQuery) -> None:
    cancel_live_task(callback.message)
    action = callback.data.split(":")[1]
    async with SessionLocal() as session:
        player = await get_or_create_player(session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
        if action == "home":
            text, keyboard = "🎮 <b>Main menu</b>", main_menu()
        elif action == "wallet":
            inventory = (await session.execute(select(Inventory, Drug).join(Drug).where(Inventory.user_id == player.user_id, Inventory.quantity > 0))).all()
            lines = ["💵 <b>Wallet</b>", "", f"Cash: {eur(player.money)}", f"Tokens: 🔑 {player.tokens}", "", "📦 Inventory:"]
            lines.extend(f"• {drug.display_name}: {item.quantity}" for item, drug in inventory)
            if not inventory:
                lines.append("• Empty")
            text, keyboard = "\n".join(lines), back()
        elif action == "prices":
            await session.commit()
            text, keyboard = await build_prices_screen()
            await callback.message.edit_text(text, reply_markup=keyboard)
            register_live_task(callback.message, live_prices(callback.message))
            await callback.answer()
            return
        elif action in {"buy", "sell"}:
            rows = (await session.execute(select(Drug, MarketPrice).join(MarketPrice))).all()
            text = "🛒 <b>What would you like to buy?</b>" if action == "buy" else "💰 <b>What would you like to sell?</b>"
            items = []
            for drug, price in rows:
                if action == "buy":
                    label = drug.display_name
                    shown_price = price.current_price
                else:
                    inv = await session.get(Inventory, (player.user_id, drug.id))
                    owned = inv.quantity if inv else 0
                    label = f"{drug.display_name} — owned: {owned}"
                    shown_price = price.current_price
                items.append((drug.code, label, shown_price))
            keyboard = drug_menu(action, items)
        elif action == "raid":
            await session.commit()
            text, keyboard, cooldown = await build_raid_screen(player.user_id)
            await callback.message.edit_text(text, reply_markup=keyboard)
            if cooldown > timedelta(0):
                register_live_task(callback.message, live_raid(callback.message, player.user_id))
            await callback.answer()
            return
        elif action == "news":
            news_rows = (await session.execute(select(MarketNews, Drug).outerjoin(Drug, Drug.id == MarketNews.drug_id).order_by(MarketNews.created_at.desc()).limit(12))).all()
            lines = ["📰 <b>Market news</b>", ""]
            if news_rows:
                for item, drug in news_rows:
                    impact = f" ({Decimal(item.impact_percent):+.0f}%)" if Decimal(item.impact_percent) else ""
                    lines.append(f"• <b>{escape(item.headline)}</b>{impact}\n  {escape(item.details)}")
            else:
                lines.append("No major market news yet.")
            text = "\n\n".join(lines)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 Market", callback_data="nav:prices"), InlineKeyboardButton(text="🏠 Main menu", callback_data="nav:home")]])
        elif action == "playtime":
            rows, total = await player_playtime(session, player.user_id)
            recent = rows[:7]
            lines = ["⏱ <b>My playtime</b>", "", f"Total: <b>{duration_text(total)}</b>", "", "Recent days:"]
            lines.extend(f"• {row.play_date.isoformat()}: {duration_text(row.seconds_played)}" for row in recent)
            if not recent:
                lines.append("• No recorded playtime yet")
            text, keyboard = "\n".join(lines), back()
        elif action == "loans":
            text = "💳 <b>Loans</b>\nChoose an amount."
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=eur(amount), callback_data=f"loan:{amount}") for amount in (500, 1000, 2000)], [InlineKeyboardButton(text="🏠 Main menu", callback_data="nav:home")]])
        elif action == "boosts":
            text = "⚡ <b>Boost shop</b>"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💰 Money Boost — 5 🔑", callback_data="boost:MONEY_BOOST")], [InlineKeyboardButton(text="🛡️ Raid Shield — 3 🔑", callback_data="boost:RAID_SHIELD")], [InlineKeyboardButton(text="📈 Price Boost — 4 🔑", callback_data="boost:PRICE_BOOST")], [InlineKeyboardButton(text="🏠 Main menu", callback_data="nav:home")]])
        elif action == "blackmarket":
            lines = ["🗝 <b>Black Market</b>", "", f"Your keys: 🔑 <b>{player.tokens}</b>", "", "Keys are earned from daily logins, level-ups, trades and successful raids."]
            rows = []
            for code, item in BLACK_MARKET_ITEMS.items():
                rows.append([InlineKeyboardButton(text=f"{item['name']} — {item['cost']} 🔑", callback_data=f"black:{code}")])
            rows.append([InlineKeyboardButton(text="🏠 Main menu", callback_data="nav:home")])
            text, keyboard = "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)
        elif action == "leaderboard":
            players = (await session.scalars(select(Player).order_by(Player.money.desc()).limit(10))).all()
            text = "🏆 <b>Leaderboard</b>\n\n" + "\n".join(f"{rank}. {escape(item.username or item.display_name)} — {eur(item.money)}" for rank, item in enumerate(players, 1))
            keyboard = back()
        else:
            text, keyboard = "Unknown action.", main_menu()
        await session.commit()
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:"))
async def admin_reports(callback: CallbackQuery) -> None:
    cancel_live_task(callback.message)
    if not is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    action = callback.data.split(":", 1)[1]
    async with SessionLocal() as session:
        report = await admin_playtime_report(session)
        await session.commit()
    if action == "daily_player":
        lines = ["📅 <b>Minutes per day per player</b>", ""]
        for row, player in report["per_day_player"]:
            lines.append(f"{row.play_date.isoformat()} · {escape(player.username or player.display_name)}: {row.seconds_played // 60}m")
    elif action == "daily_total":
        lines = ["📊 <b>Total minutes of all players per day</b>", ""]
        lines.extend(f"{day.isoformat()}: {int(seconds) // 60}m" for day, seconds in report["per_day_totals"])
    elif action == "player_total":
        lines = ["👥 <b>Total minutes per player</b>", ""]
        lines.extend(f"{escape(player.username or player.display_name)}: {int(seconds) // 60}m" for player, seconds in report["per_player_totals"])
    else:
        lines = ["Σ <b>Total playtime of all players</b>", "", f"{report['grand_total'] // 60} minutes", f"({duration_text(report['grand_total'])})"]
    if len(lines) == 2:
        lines.append("No playtime recorded yet.")
    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3850] + "\n\n… report truncated"
    await callback.message.edit_text(text, reply_markup=admin_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("market:"))
async def market_detail(callback: CallbackQuery) -> None:
    cancel_live_task(callback.message)
    code = callback.data.split(":", 1)[1]
    text, keyboard = await build_market_detail(code)
    await callback.message.edit_text(text, reply_markup=keyboard)
    register_live_task(callback.message, live_market_detail(callback.message, code))
    await callback.answer()


async def live_market_detail(message, code: str) -> None:
    try:
        while True:
            text, keyboard = await build_market_detail(code)
            try:
                await message.edit_text(text, reply_markup=keyboard)
            except TelegramBadRequest as error:
                if "message is not modified" not in str(error).lower():
                    break
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        return


@router.callback_query(F.data.startswith(("buy:", "sell:")))
async def choose_trade_quantity(callback: CallbackQuery, state: FSMContext) -> None:
    cancel_live_task(callback.message)
    await state.clear()
    action, code = callback.data.split(":", 1)
    async with SessionLocal() as session:
        result = (await session.execute(select(Drug, MarketPrice).join(MarketPrice).where(Drug.code == code))).first()
        player = await get_or_create_player(session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
        if not result:
            await callback.answer("Product not found.", show_alert=True)
            return
        drug, price = result
        inv = await session.get(Inventory, (player.user_id, drug.id))
        owned = inv.quantity if inv else 0
        await session.commit()
    rows = [[InlineKeyboardButton(text=str(quantity), callback_data=f"trade:{action}:{code}:{quantity}") for quantity in (1, 5, 10)]]
    rows.append([InlineKeyboardButton(text="✍️ Enter custom amount", callback_data=f"custom:{action}:{code}")])
    if action == "sell" and owned > 0:
        rows.append([InlineKeyboardButton(text=f"Sell all ({owned})", callback_data=f"trade:sell:{code}:{owned}")])
    rows.append([InlineKeyboardButton(text="🏠 Main menu", callback_data="nav:home")])
    info = f"Current price: {eur(price.current_price)} each\nYou own: <b>{owned} units</b>"
    await callback.message.edit_text(f"<b>{drug.display_name}</b>\n{info}\n\nChoose a quantity:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()

@router.callback_query(F.data.startswith("custom:"))
async def request_custom_quantity(callback: CallbackQuery, state: FSMContext) -> None:
    _, action, code = callback.data.split(":")
    await state.set_state(TradeInput.waiting_quantity)
    await state.update_data(action=action, code=code)
    await callback.message.edit_text("✍️ Send the number of units as a message.\n\nUse a whole number. Send /cancel to stop.")
    await callback.answer()

@router.message(Command("cancel"), StateFilter(TradeInput.waiting_quantity))
async def cancel_custom_trade(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Trade cancelled.", reply_markup=main_menu())

@router.message(StateFilter(TradeInput.waiting_quantity))
async def execute_custom_trade(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Enter a whole number between 1 and 100, or use /cancel.")
        return
    quantity = int(raw)
    if quantity < 1 or quantity > 10000:
        await message.answer("Enter a quantity between 1 and 10,000.")
        return
    data = await state.get_data()
    action, code = data["action"], data["code"]
    if action == "buy" and quantity > 100:
        await message.answer("A single purchase is limited to 100 units.")
        return
    async with SessionLocal() as session:
        player = await get_or_create_player(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
        try:
            amount = await buy(session, player, code, quantity) if action == "buy" else await sell(session, player, code, quantity)
            token_found = await maybe_trade_token_drop(session, player)
            level, gained = await grant_xp(session, player, quantity)
            await session.commit()
        except GameError as error:
            await session.rollback()
            await message.answer(f"❌ {escape(str(error))}")
            return
    await state.clear()
    verb = "Bought" if action == "buy" else "Sold"
    extra = ("\n🔑 You found a hidden key!" if token_found else "") + (f"\n⬆️ Level {level}! +{gained * 3} keys" if gained else "")
    await message.answer(f"✅ {verb} {quantity} units for {eur(amount)}.{extra}", reply_markup=main_menu())

@router.callback_query(F.data.startswith("trade:"))
async def execute_trade(callback: CallbackQuery) -> None:
    cancel_live_task(callback.message)
    _, action, code, quantity = callback.data.split(":")
    async with SessionLocal() as session:
        if not await mark_callback(session, callback.id, callback.from_user.id, callback.data):
            await callback.answer("Already processed.")
            return
        player = await get_or_create_player(session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
        try:
            amount = await buy(session, player, code, int(quantity)) if action == "buy" else await sell(session, player, code, int(quantity))
            token_found = await maybe_trade_token_drop(session, player)
            level, gained = await grant_xp(session, player, int(quantity))
            await session.commit()
            extra = ("\n🔑 You found a hidden key!" if token_found else "") + (f"\n⬆️ Level {level}! +{gained * 3} keys" if gained else "")
            message = f"✅ {'Purchased' if action == 'buy' else 'Sold'} {quantity}× {code.title()} for {eur(amount)}.{extra}"
        except GameError as error:
            await session.rollback()
            message = f"❌ {error}"
    await callback.answer()
    await callback.message.edit_text(message, reply_markup=back())


@router.callback_query(F.data.startswith("loan:"))
async def create_loan(callback: CallbackQuery) -> None:
    cancel_live_task(callback.message)
    async with SessionLocal() as session:
        player = await get_or_create_player(session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
        try:
            loan = await take_loan(session, player, Decimal(callback.data.split(":")[1]))
            await session.commit()
            message = f"✅ Loan received: {eur(loan.principal)}. Repayment: {eur(loan.total_due)}."
        except GameError as error:
            await session.rollback()
            message = f"❌ {error}"
    await callback.message.edit_text(message, reply_markup=back())
    await callback.answer()


@router.callback_query(F.data.startswith("black:"))
async def black_market_purchase(callback: CallbackQuery) -> None:
    code = callback.data.split(":", 1)[1]
    async with SessionLocal() as session:
        player = await get_or_create_player(session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
        try:
            name = await buy_black_market_item(session, player, code)
            await session.commit()
        except GameError as error:
            await session.rollback()
            await callback.answer(str(error), show_alert=True)
            return
    await callback.message.edit_text(f"✅ Purchased: <b>{escape(name)}</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🗝 Back to Black Market", callback_data="nav:blackmarket")], [InlineKeyboardButton(text="🏠 Main menu", callback_data="nav:home")]]))
    await callback.answer()

@router.callback_query(F.data.startswith("boost:"))
async def purchase_boost(callback: CallbackQuery) -> None:
    cancel_live_task(callback.message)
    async with SessionLocal() as session:
        player = await get_or_create_player(session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
        try:
            await buy_boost(session, player, callback.data.split(":")[1])
            await session.commit()
            message = "✅ Boost activated for 24 hours."
        except GameError as error:
            await session.rollback()
            message = f"❌ {error}"
    await callback.message.edit_text(message, reply_markup=back())
    await callback.answer()


@router.callback_query(F.data.startswith("raid:"))
async def execute_raid(callback: CallbackQuery) -> None:
    cancel_live_task(callback.message)
    async with SessionLocal() as session:
        player = await get_or_create_player(session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
        try:
            outcome, amount = await raid(session, player, int(callback.data.split(":")[1]))
            token_found = False
            if outcome == "SUCCESS":
                token_found = await maybe_raid_token_drop(session, player)
                await grant_xp(session, player, 20)
            await session.commit()
            message = {"SUCCESS": f"✅ Raid successful. Loot: {eur(amount)}", "FAILURE": f"❌ Raid failed. Fine: {eur(amount)}", "BLOCKED": "🛡️ Raid blocked by a shield."}[outcome]
            if token_found:
                message += "\n🔑 You found a key during the raid!"
        except GameError as error:
            await session.rollback()
            message = f"❌ {error}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎯 Raid menu", callback_data="nav:raid")], [InlineKeyboardButton(text="🏠 Main menu", callback_data="nav:home")]])
    await callback.message.edit_text(message, reply_markup=keyboard)
    await callback.answer()

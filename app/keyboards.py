from decimal import Decimal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def format_eur(value: Decimal | int) -> str:
    return f"€{Decimal(value):,.2f}"


def main_menu() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="💵 Wallet", callback_data="nav:wallet"),
            InlineKeyboardButton(text="📊 Prices", callback_data="nav:prices"),
        ],
        [
            InlineKeyboardButton(text="🛒 Buy", callback_data="nav:buy"),
            InlineKeyboardButton(text="💰 Sell", callback_data="nav:sell"),
        ],
        [
            InlineKeyboardButton(text="🎯 Raid", callback_data="nav:raid"),
            InlineKeyboardButton(text="🏆 Leaderboard", callback_data="nav:leaderboard"),
        ],
        [
            InlineKeyboardButton(text="💳 Loans", callback_data="nav:loans"),
            InlineKeyboardButton(text="⚡ Boosts", callback_data="nav:boosts"),
        ],
        [
            InlineKeyboardButton(text="📰 News", callback_data="nav:news"),
            InlineKeyboardButton(text="⏱ My playtime", callback_data="nav:playtime"),
        ],
        [InlineKeyboardButton(text="🗝 Black Market", callback_data="nav:blackmarket")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 Main menu", callback_data="nav:home")]]
    )


def drug_menu(prefix: str, items: list[tuple[str, str, Decimal | None]]) -> InlineKeyboardMarkup:
    rows = []
    for code, label, price in items:
        button_text = f"{label} — {format_eur(price)}" if price is not None else label
        rows.append([InlineKeyboardButton(text=button_text, callback_data=f"{prefix}:{code}")])
    rows.append([InlineKeyboardButton(text="🏠 Main menu", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

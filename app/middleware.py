from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.database import SessionLocal
from app.models import Player
from app.playtime import touch_player


class PlaytimeMiddleware(BaseMiddleware):
    """Record activity after handlers have had a chance to create the player.

    This middleware must be registered on the message and callback-query
    observers, not on ``Dispatcher.update``. At update level aiogram passes an
    ``Update`` object, so direct Message/CallbackQuery checks never match.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user

        try:
            return await handler(event, data)
        finally:
            if user is None:
                return

            async with SessionLocal() as session:
                # On a player's first /start, the handler creates the Player
                # before this block runs. Ignore unrelated Telegram users for
                # whom no game account exists.
                player = await session.get(Player, user.id)
                if player is None:
                    return
                await touch_player(session, user.id)
                await session.commit()

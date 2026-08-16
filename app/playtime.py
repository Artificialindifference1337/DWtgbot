from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.constants import PLAYTIME_IDLE_TIMEOUT_SECONDS
from app.models import DailyPlaytime, Player, PlayerPresence


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def touch_player(session: AsyncSession, user_id: int, at: datetime | None = None) -> None:
    now = at or utcnow()
    presence = await session.get(PlayerPresence, user_id)
    if presence is None:
        session.add(PlayerPresence(user_id=user_id, last_interaction_at=now, credited_until=now))
        return

    last_interaction = as_utc(presence.last_interaction_at)
    credited_until = as_utc(presence.credited_until)
    previous_active_until = last_interaction + timedelta(seconds=PLAYTIME_IDLE_TIMEOUT_SECONDS)
    credit_end = min(now, previous_active_until)
    if credit_end > credited_until:
        await _credit_range(session, user_id, credited_until, credit_end)
        presence.credited_until = credit_end
    if now > previous_active_until:
        # Do not count the inactive gap between two separate play sessions.
        presence.credited_until = now
    presence.last_interaction_at = now


async def _credit_range(session: AsyncSession, user_id: int, start: datetime, end: datetime) -> int:
    if end <= start:
        return 0
    timezone_name = get_settings().game_timezone
    tz = ZoneInfo(timezone_name)
    cursor = start
    credited = 0
    while cursor < end:
        local_cursor = cursor.astimezone(tz)
        next_midnight_local = datetime.combine(
            local_cursor.date() + timedelta(days=1), datetime.min.time(), tzinfo=tz
        )
        boundary = min(end, next_midnight_local.astimezone(timezone.utc))
        seconds = max(0, int((boundary - cursor).total_seconds()))
        if seconds:
            play_date = local_cursor.date()
            row = await session.get(DailyPlaytime, (user_id, play_date))
            if row is None:
                row = DailyPlaytime(user_id=user_id, play_date=play_date, seconds_played=0)
                session.add(row)
            row.seconds_played += seconds
            credited += seconds
        cursor = boundary
    return credited


async def accrue_playtime(session: AsyncSession, now: datetime | None = None) -> int:
    current = now or utcnow()
    presences = (await session.scalars(select(PlayerPresence))).all()
    total = 0
    for presence in presences:
        last_interaction = as_utc(presence.last_interaction_at)
        credited_until = as_utc(presence.credited_until)
        active_until = last_interaction + timedelta(seconds=PLAYTIME_IDLE_TIMEOUT_SECONDS)
        credit_end = min(current, active_until)
        if credit_end > credited_until:
            total += await _credit_range(session, presence.user_id, credited_until, credit_end)
            presence.credited_until = credit_end
    return total


async def player_playtime(session: AsyncSession, user_id: int) -> tuple[list[DailyPlaytime], int]:
    await accrue_playtime(session)
    rows = (
        await session.scalars(
            select(DailyPlaytime)
            .where(DailyPlaytime.user_id == user_id)
            .order_by(DailyPlaytime.play_date.desc())
        )
    ).all()
    return list(rows), sum(row.seconds_played for row in rows)


async def admin_playtime_report(session: AsyncSession) -> dict:
    await accrue_playtime(session)
    per_day_player = (
        await session.execute(
            select(DailyPlaytime, Player)
            .join(Player, Player.user_id == DailyPlaytime.user_id)
            .order_by(DailyPlaytime.play_date.desc(), Player.display_name.asc())
        )
    ).all()
    per_day_totals = (
        await session.execute(
            select(DailyPlaytime.play_date, func.sum(DailyPlaytime.seconds_played))
            .group_by(DailyPlaytime.play_date)
            .order_by(DailyPlaytime.play_date.desc())
        )
    ).all()
    per_player_totals = (
        await session.execute(
            select(Player, func.coalesce(func.sum(DailyPlaytime.seconds_played), 0))
            .outerjoin(DailyPlaytime, DailyPlaytime.user_id == Player.user_id)
            .group_by(Player.user_id)
            .order_by(func.sum(DailyPlaytime.seconds_played).desc())
        )
    ).all()
    grand_total = int(
        (await session.scalar(select(func.coalesce(func.sum(DailyPlaytime.seconds_played), 0)))) or 0
    )
    return {
        "per_day_player": per_day_player,
        "per_day_totals": per_day_totals,
        "per_player_totals": per_player_totals,
        "grand_total": grand_total,
    }

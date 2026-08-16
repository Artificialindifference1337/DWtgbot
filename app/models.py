from __future__ import annotations
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class Player(Base):
    __tablename__ = "players"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(128), default="Speler")
    money: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("1000.00"))
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    inventory_capacity: Mapped[int] = mapped_column(Integer, default=100)
    last_raid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_raids: Mapped[int] = mapped_column(Integer, default=0)
    successful_raids: Mapped[int] = mapped_column(Integer, default=0)
    total_raid_gains: Mapped[Decimal] = mapped_column(Numeric(14,2), default=0)
    total_raid_losses: Mapped[Decimal] = mapped_column(Numeric(14,2), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    inventory: Mapped[list[Inventory]] = relationship(back_populates="player", cascade="all, delete-orphan")

class Drug(Base):
    __tablename__ = "drugs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    display_name: Mapped[str] = mapped_column(String(64))
    base_price: Mapped[Decimal] = mapped_column(Numeric(12,2))
    minimum_price: Mapped[Decimal] = mapped_column(Numeric(12,2))
    maximum_price: Mapped[Decimal] = mapped_column(Numeric(12,2))

class MarketPrice(Base):
    __tablename__ = "market_prices"
    drug_id: Mapped[int] = mapped_column(ForeignKey("drugs.id", ondelete="CASCADE"), primary_key=True)
    current_price: Mapped[Decimal] = mapped_column(Numeric(12,2))
    previous_price: Mapped[Decimal] = mapped_column(Numeric(12,2))
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    drug: Mapped[Drug] = relationship()

class DailyMarketPrice(Base):
    __tablename__ = "daily_market_prices"
    drug_id: Mapped[int] = mapped_column(ForeignKey("drugs.id", ondelete="CASCADE"), primary_key=True)
    price_date: Mapped[date] = mapped_column(Date, primary_key=True)
    closing_price: Mapped[Decimal] = mapped_column(Numeric(12,2))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    drug: Mapped[Drug] = relationship()

class Inventory(Base):
    __tablename__ = "player_inventory"
    user_id: Mapped[int] = mapped_column(ForeignKey("players.user_id", ondelete="CASCADE"), primary_key=True)
    drug_id: Mapped[int] = mapped_column(ForeignKey("drugs.id", ondelete="CASCADE"), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    average_purchase_price: Mapped[Decimal] = mapped_column(Numeric(12,2), default=0)
    player: Mapped[Player] = relationship(back_populates="inventory")
    drug: Mapped[Drug] = relationship()

class Loan(Base):
    __tablename__ = "loans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("players.user_id", ondelete="CASCADE"), index=True)
    principal: Mapped[Decimal] = mapped_column(Numeric(14,2))
    total_due: Mapped[Decimal] = mapped_column(Numeric(14,2))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class Boost(Base):
    __tablename__ = "boosts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("players.user_id", ondelete="CASCADE"), index=True)
    boost_type: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    remaining_uses: Mapped[int | None] = mapped_column(Integer)
    money_bonus_used: Mapped[Decimal] = mapped_column(Numeric(14,2), default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class DailyLogin(Base):
    __tablename__ = "daily_login"
    user_id: Mapped[int] = mapped_column(ForeignKey("players.user_id", ondelete="CASCADE"), primary_key=True)
    last_claim_date: Mapped[date | None] = mapped_column(Date)
    consecutive_days: Mapped[int] = mapped_column(Integer, default=0)

class Raid(Base):
    __tablename__ = "raids"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attacker_id: Mapped[int] = mapped_column(BigInteger, index=True)
    defender_id: Mapped[int] = mapped_column(BigInteger, index=True)
    outcome: Mapped[str] = mapped_column(String(16))
    money_stolen: Mapped[Decimal] = mapped_column(Numeric(14,2), default=0)
    fine_paid: Mapped[Decimal] = mapped_column(Numeric(14,2), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class ProcessedCallback(Base):
    __tablename__ = "processed_callbacks"
    callback_query_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    callback_data: Mapped[str] = mapped_column(String(256))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlayerPresence(Base):
    __tablename__ = "player_presence"
    user_id: Mapped[int] = mapped_column(ForeignKey("players.user_id", ondelete="CASCADE"), primary_key=True)
    last_interaction_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    credited_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DailyPlaytime(Base):
    __tablename__ = "daily_playtime"
    user_id: Mapped[int] = mapped_column(ForeignKey("players.user_id", ondelete="CASCADE"), primary_key=True)
    play_date: Mapped[date] = mapped_column(Date, primary_key=True)
    seconds_played: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MarketState(Base):
    __tablename__ = "market_states"
    drug_id: Mapped[int] = mapped_column(ForeignKey("drugs.id", ondelete="CASCADE"), primary_key=True)
    regime: Mapped[str] = mapped_column(String(16), default="SIDEWAYS")
    momentum: Mapped[Decimal] = mapped_column(Numeric(8, 5), default=Decimal("0"))
    regime_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MarketHistory(Base):
    __tablename__ = "market_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    drug_id: Mapped[int] = mapped_column(ForeignKey("drugs.id", ondelete="CASCADE"), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MarketNews(Base):
    __tablename__ = "market_news"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    drug_id: Mapped[int | None] = mapped_column(ForeignKey("drugs.id", ondelete="CASCADE"), nullable=True, index=True)
    headline: Mapped[str] = mapped_column(String(180))
    details: Mapped[str] = mapped_column(Text, default="")
    impact_percent: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class TradeRecord(Base):
    __tablename__ = "trade_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("players.user_id", ondelete="CASCADE"), index=True)
    drug_id: Mapped[int] = mapped_column(ForeignKey("drugs.id", ondelete="CASCADE"), index=True)
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    total_value: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

class TokenLedger(Base):
    __tablename__ = "token_ledger"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("players.user_id", ondelete="CASCADE"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class PlayerProgress(Base):
    __tablename__ = "player_progress"
    user_id: Mapped[int] = mapped_column(ForeignKey("players.user_id", ondelete="CASCADE"), primary_key=True)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)


class PlayerAchievement(Base):
    __tablename__ = "player_achievements"
    user_id: Mapped[int] = mapped_column(ForeignKey("players.user_id", ondelete="CASCADE"), primary_key=True)
    achievement_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_reward: Mapped[int] = mapped_column(Integer, default=0)
    unlocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlayerUpgrade(Base):
    __tablename__ = "player_upgrades"
    user_id: Mapped[int] = mapped_column(ForeignKey("players.user_id", ondelete="CASCADE"), primary_key=True)
    upgrade_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

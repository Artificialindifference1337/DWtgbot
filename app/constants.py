from decimal import Decimal

STARTING_MONEY = Decimal("1000.00")
DAILY_BASE = Decimal("100.00")
DAILY_INCREMENT = Decimal("10.00")
DAILY_CAP_DAYS = 16
INVENTORY_CAPACITY = 100
RAID_COOLDOWN_SECONDS = 30
RAID_SUCCESS_RATE = Decimal("0.60")
RAID_FAILURE_FINE = Decimal("300.00")
LOAN_MAX_PRINCIPAL = Decimal("2000.00")
LOAN_RATE = Decimal("0.15")
LOAN_HOURS = 24
MONEY_BOOST_LIMIT = Decimal("5000.00")
MARKET_UPDATE_SECONDS = 120
PLAYTIME_IDLE_TIMEOUT_SECONDS = 120
PLAYTIME_TICK_SECONDS = 10
DRUGS = {
    "WEED": ("Weed", Decimal("50.00"), Decimal("25.00"), Decimal("100.00")),
    "MDMA": ("MDMA", Decimal("120.00"), Decimal("60.00"), Decimal("240.00")),
    "COCAINE": ("Cocaine", Decimal("150.00"), Decimal("75.00"), Decimal("300.00")),
    "HEROIN": ("Heroin", Decimal("200.00"), Decimal("100.00"), Decimal("400.00")),
    "FENTANYL": ("Fentanyl", Decimal("300.00"), Decimal("150.00"), Decimal("600.00")),
}

TOKEN_FIND_CHANCE = 0.01
TOKEN_RAID_CHANCE = 0.10
TOKEN_DAILY_MIN = 1
TOKEN_DAILY_MAX = 3
TOKEN_WEEK_STREAK_BONUS = 5

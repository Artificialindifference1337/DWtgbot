from decimal import Decimal
import random
from app.services import bounded_market_price, event_market_price, money, normal_market_price


def test_money_rounding():
    assert money(Decimal("1.005")) == Decimal("1.01")


def test_money_exact():
    assert money(Decimal("1000")) == Decimal("1000.00")


def test_market_bounds_are_relative_to_base():
    assert bounded_market_price(Decimal("100"), Decimal("0")) == Decimal("20.00")
    assert bounded_market_price(Decimal("100"), Decimal("999999")) == Decimal("2000.00")


def test_normal_market_recovers_from_floor():
    random.seed(5)
    price = Decimal("20.00")
    base = Decimal("100.00")
    for _ in range(120):
        price = normal_market_price(price, base)
    assert price > Decimal("20.00")
    assert Decimal("20.00") <= price <= Decimal("2000.00")


def test_long_simulation_does_not_collapse():
    random.seed(11)
    base = Decimal("100.00")
    price = base
    for _ in range(720):  # 24 hours at one update every two minutes
        price = normal_market_price(price, base)
        assert Decimal("20.00") <= price <= Decimal("2000.00")
    assert price >= Decimal("20.00")


def test_event_is_exciting_but_bounded():
    random.seed(7)
    price = event_market_price(Decimal("100.00"))
    assert Decimal("300.00") <= price <= Decimal("1200.00")

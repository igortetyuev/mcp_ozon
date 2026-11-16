# ruff: noqa: RUF001
from datetime import datetime

import pytest

from mcp_ozon.models import CartItem, ProductTile, to_jsonable
from mcp_ozon.parse import parse_price, parse_ru_date

NOW = datetime(2026, 11, 16, 12, 0)  # Monday


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("сегодня", "2026-11-16"),
        ("завтра", "2026-11-17"),
        ("послезавтра", "2026-11-18"),
        ("Завтра", "2026-11-17"),
        ("18 ноября", "2026-11-18"),
        ("3 декабря", "2026-12-03"),
        ("5 января", "2027-01-05"),
        ("18 ноября 2030", "2030-11-18"),
        ("за 2 часа", "2026-11-16T14:00"),
        ("за 1 час", "2026-11-16T13:00"),
        ("сегодня с 9:00 до 18:00", "2026-11-16T18:00"),
        ("завтра с 10.00 до 14.00", "2026-11-17T14:00"),
        ("когда-нибудь", None),
        ("доставка 29 февраля", None),
    ],
)
def test_parse_ru_date(text: str, expected: str | None) -> None:
    assert parse_ru_date(text, NOW) == expected


def test_parse_ru_date_new_year_rollover() -> None:
    nye = datetime(2026, 12, 30, 10, 0)
    assert parse_ru_date("5 января", nye) == "2027-01-05"
    assert parse_ru_date("29 декабря", nye) == "2027-12-29"
    assert parse_ru_date("завтра", nye) == "2026-12-31"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 299 ₽", 1299),
        ("1 299 ₽", 1299),
        ("12 990", 12990),
        ("99", 99),
        ("", None),
        (None, None),
        (1299, 1299),
    ],
)
def test_parse_price(text, expected) -> None:
    assert parse_price(text) == expected


def test_to_jsonable_drops_empty() -> None:
    tile = ProductTile(sku="1", title="t", url="/p/1", price=100, express=False)
    out = to_jsonable(tile)
    assert out == {"sku": "1", "title": "t", "url": "/p/1", "price": 100, "express": False}


def test_to_jsonable_list() -> None:
    items = [CartItem(sku="1", title="a", quantity=2)]
    assert to_jsonable(items) == [{"sku": "1", "title": "a", "quantity": 2, "available": True}]

# ruff: noqa: RUF001
import json
from datetime import datetime
from pathlib import Path

from mcp_ozon import parse

NOW = datetime(2026, 11, 16, 12, 0)
FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_tiles_search(load_page) -> None:
    tiles = parse.parse_tiles(load_page("search_usbc"), NOW)
    assert len(tiles) == 8
    t = tiles[0]
    assert t.sku == "1393105636"
    assert "Кабель COMMO" in t.title
    assert t.url.startswith("https://www.ozon.ru/product/")
    assert t.price == 999
    assert t.card_price == 999
    assert t.rating == 4.9
    assert t.reviews == 10260
    assert t.delivery_label == "20 ноября"
    assert t.delivery_date == "2026-11-20"
    assert t.in_stock is True
    sale = tiles[1]
    assert sale.price == 380
    assert sale.original_price == 4000
    assert sale.discount_pct == 90
    assert sale.card_price is None
    assert sale.delivery_label == "Завтра"
    assert sale.delivery_date == "2026-11-17"


def test_parse_tiles_favorites(load_page) -> None:
    tiles = parse.parse_tiles(load_page("favorites"), NOW)
    assert tiles
    assert all(t.sku and t.title for t in tiles)


def test_parse_facets(load_page) -> None:
    facets = parse.parse_facets(load_page("search_usbc"))
    by_key = {f.key: f for f in facets}
    assert "brand" in by_key
    assert "delivery" in by_key
    delivery_vals = {v.value: v.title for v in by_key["delivery"].values}
    assert delivery_vals["2"] == "Завтра"
    assert by_key["brand"].values


def test_parse_cart(load_page) -> None:
    cart = parse.parse_cart(load_page("cart"))
    assert len(cart["items"]) == 4
    item = cart["items"][0]
    assert item.sku == "848777750"
    assert "Модуль инфракрасного" in item.title
    assert item.quantity == 1
    assert item.price == 146  # Ozon-card price, not the listed 152
    assert item.card_price == 146
    assert item.original_price == 152
    assert item.available is True
    assert cart["total"] == 286
    assert "2 товара" in cart["summary"]


def test_parse_orders(load_page) -> None:
    orders = parse.parse_orders(load_page("orderlist"), NOW)
    assert len(orders) == 9
    assert orders[0].number == "90000000-0001"
    assert orders[0].status == "В службе доставки"
    assert orders[0].status_group == "in_progress"
    received = [o for o in orders if o.status_group == "received"]
    assert received
    assert received[0].date


def test_parse_order_details(load_page) -> None:
    order = parse.parse_order_details(load_page("orderdetails"), "90000000-0001", NOW)
    assert order.items
    item = order.items[0]
    assert item.title
    assert item.price
    assert item.sku
    assert order.status


def test_parse_product_details(load_page) -> None:
    p = parse.parse_product_details(load_page("product"), "1393105636")
    assert "Кабель COMMO" in p["title"]
    assert p["price"] == 999  # effective price (Ozon card), not the listed 1110
    assert p["card_price"] == 999
    assert p["original_price"] == 1110
    assert p["discount_pct"] == 10
    assert p["rating"] == 4.9
    assert p["reviews"] == 10260
    assert p["in_stock"] is True


def test_parse_delivery_sections() -> None:
    data = json.loads((FIXTURES / "delivery_widget.json").read_text())
    opts = parse.parse_delivery_sections(data, "1393105636", NOW)
    assert opts.address == "***address***"
    assert opts.express.available is False
    assert opts.courier_earliest.eta_text == "4 декабря"
    assert opts.courier_earliest.date == "2026-12-04"
    assert opts.courier_earliest.price == 0
    assert opts.pickup.eta_text == "20 ноября"
    assert opts.pickup.date == "2026-11-20"


def test_find_component(load_page) -> None:
    page = load_page("product")
    comp = parse.find_component(page.raw["layout"], "webDelivery")
    assert comp["stateId"].startswith("webDelivery-")
    assert comp["asyncData"]

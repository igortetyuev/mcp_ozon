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


def test_parse_reviews(load_page) -> None:
    out = parse.parse_reviews(load_page("reviews"))
    assert out["summary"]["rating"] == 4.9
    assert out["summary"]["count"] == 10279
    assert {s["stars"] for s in out["summary"]["scores"]} == {1, 2, 3, 4, 5}
    assert len(out["variants"]) == 9
    assert all(v["sku"] and v["attributes"] for v in out["variants"])
    assert len(out["reviews"]) == 30
    r = out["reviews"][0]
    assert r.score == 5
    assert r.text
    assert r.author
    assert r.useful == 12
    assert len(r.photos) == 3
    assert r.date.startswith("2026")
    assert r.sku  # masked to "***" in the fixture
    assert out["next"].startswith("/product/")
    assert "page=2" in out["next"]


def test_parse_review_media(load_page) -> None:
    media, paging = parse.parse_review_media(load_page("review_media"))
    assert len(media) == 15
    assert paging["total"] == 623
    photo = next(m for m in media if m.type == "photo")
    assert photo.url.startswith("https://ir.ozone.ru/")
    assert photo.rating == 5
    assert photo.author
    assert photo.sku
    video = next(m for m in media if m.type == "video")
    assert video.url.endswith(".mp4?type=review")
    assert video.preview_url
    assert video.duration


def test_find_component(load_page) -> None:
    page = load_page("product")
    comp = parse.find_component(page.raw["layout"], "webDelivery")
    assert comp["stateId"].startswith("webDelivery-")
    assert comp["asyncData"]

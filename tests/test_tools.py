import asyncio
import json
from pathlib import Path
from urllib.parse import unquote

import pytest

from mcp_ozon import ozon, server

FIXTURES = Path(__file__).parent / "fixtures"


def _fake_page_json(monkeypatch):
    async def fake(path: str):
        path = unquote(path)
        if "layout_page_index" in path or "search_page_state" in path:
            return ozon.PageJson.from_raw({"widgetStates": {}})
        if "orderdetails" in path:
            name = "orderdetails"
        elif "orderlist" in path:
            name = "orderlist"
        elif "favorites" in path:
            name = "favorites"
        elif path == "/cart":
            name = "cart"
        elif "/reviews" in path:
            name = "reviews"
        elif "/videos" in path:
            name = "review_media"
        elif path.startswith("/product/"):
            name = "product"
        elif "search" in path:
            name = "search_usbc"
        elif path == "/":
            name = "home"
        else:
            raise AssertionError(f"unexpected path {path}")
        return ozon.PageJson.from_raw(
            json.loads((FIXTURES / f"{name}.json").read_text())
        )

    monkeypatch.setattr(ozon, "page_json", fake)


@pytest.fixture
def stubbed(monkeypatch):
    _fake_page_json(monkeypatch)

    async def fake_widget(state_id, async_data):
        return json.loads((FIXTURES / "delivery_widget.json").read_text())

    monkeypatch.setattr(ozon, "widget_json", fake_widget)


def run(coro):
    return asyncio.run(coro)


def test_get_cart(stubbed) -> None:
    out = run(server.get_cart())
    assert out["total"] == 286
    assert len(out["items"]) == 4


def test_search_products(stubbed) -> None:
    out = run(server.search_products("кабель usb-c", sort="price_asc"))
    assert len(out) == 8
    assert out[0]["sku"] == "1393105636"
    assert out[0]["price"] == 999


def test_search_path_building() -> None:
    path = server._search_path(
        "наушники",
        "rating",
        500,
        5000,
        {"brand": ["87389578", "168785571"]},
        "tomorrow",
    )
    assert "sorting=rating" in path
    assert "currency_price=500.000;5000.000" in path
    assert "delivery=2" in path
    assert "brand=87389578%2C168785571" in path or "brand=87389578,168785571" in path


def test_get_search_filters(stubbed) -> None:
    out = run(server.get_search_filters("кабель"))
    keys = {f["key"] for f in out}
    assert {"brand", "delivery", "seller"} <= keys


def test_get_favorites(stubbed) -> None:
    out = run(server.get_favorites())
    assert out
    assert all("sku" in t for t in out)


def test_list_orders_filter(stubbed) -> None:
    received = run(server.list_orders(status="received"))
    assert received
    assert all(o["status_group"] == "received" for o in received)
    assert "items" not in received[0]
    in_progress = run(server.list_orders(status="all"))
    assert len(in_progress) == 9


def test_get_order(stubbed) -> None:
    out = run(server.get_order("90000000-0001"))
    assert out["number"] == "90000000-0001"
    assert out["items"]


def test_get_product(stubbed) -> None:
    out = run(server.get_product("1393105636"))
    assert out["card_price"] == 999
    assert out["rating"] == 4.9


def test_get_delivery_options(stubbed) -> None:
    out = run(server.get_delivery_options(["1393105636"]))
    assert len(out) == 1
    o = out[0]
    assert o["sku"] == "1393105636"
    assert o["express"]["available"] is False
    assert o["courier_earliest"]["date"] == "2026-12-04"
    assert o["pickup"]["date"] == "2026-11-20"


def test_get_delivery_options_limit(stubbed) -> None:
    with pytest.raises(ValueError, match="20"):
        run(server.get_delivery_options([str(i) for i in range(21)]))


def test_get_reviews(stubbed) -> None:
    out = run(server.get_reviews("1393105636"))
    assert out["rating"] == 4.9
    assert len(out["reviews"]) == 30
    r = out["reviews"][0]
    assert r["score"] == 5
    assert r["text"]
    assert r["photos"]
    assert r["url"].startswith("https://www.ozon.ru/product/")
    assert len(out["variants"]) == 9


def test_get_reviews_text_only(stubbed) -> None:
    out = run(server.get_reviews("1393105636", include_media=False))
    r = out["reviews"][0]
    assert r["score"] == 5
    assert r["text"]
    assert "photos" not in r and "videos" not in r


def test_get_review_media(stubbed) -> None:
    out = run(server.get_review_media("1393105636", limit=15))
    assert out["total"] == 623
    assert len(out["media"]) == 15
    types = {m["type"] for m in out["media"]}
    assert types == {"photo", "video"}
    assert out["media"][0]["url"].startswith("https://")


def test_get_recommendations(stubbed) -> None:
    out = run(server.get_recommendations())
    assert out

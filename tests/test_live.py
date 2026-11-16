"""Live tests driving the user's real Chrome. Run: uv run pytest -m live"""

import asyncio

import pytest

from mcp_ozon import ozon, server

pytestmark = pytest.mark.live


_loop = asyncio.new_event_loop()


def run(coro):
    return _loop.run_until_complete(coro)


@pytest.fixture(scope="module", autouse=True)
def _session_cleanup():
    yield
    run(ozon.reset_session())
    _loop.close()


def test_live_ping() -> None:
    async def _go():
        s = await ozon.get_session()
        return await s.evaluate("location.href")

    assert "ozon.ru" in run(_go())


def test_live_read_tools() -> None:
    tiles = run(server.search_products("кабель usb-c", limit=10))
    assert tiles and tiles[0]["price"]
    sku = tiles[0]["sku"]

    facets = run(server.get_search_filters("кабель"))
    assert any(f["key"] == "brand" for f in facets)

    product = run(server.get_product(sku))
    assert product["title"]

    delivery = run(server.get_delivery_options([sku]))[0]
    assert delivery["sku"] == sku

    run(server.get_cart())
    run(server.get_favorites(limit=10))
    run(server.list_orders(limit=5))
    run(server.get_recommendations(limit=10))

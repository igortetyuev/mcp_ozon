"""FastMCP stdio server exposing Ozon buyer tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote

from fastmcp import FastMCP

from mcp_ozon import ozon, parse
from mcp_ozon.models import to_jsonable

INSTRUCTIONS = """\
Ozon buyer tools backed by the user's logged-in Chrome. Typical flow:
1) Clarify what the user needs and for what purpose (use-case matters for choice).
2) get_search_filters(query) to learn available filter keys/values, then
   search_products(query, sort, filters, delivery) to get candidates.
3) For the best 5-15 candidates call get_delivery_options(skus).
4) Answer with a table: price (Ozon Card / regular / old), Express ETA, earliest
   courier date, pickup date, rating, link — and one line per pick explaining why.
Write tools add_to_cart / add_to_favorites must be called ONLY on the user's
explicit request. Dates are for the delivery address set in the Ozon account.
If a tool fails with a CDP timeout / "No answer from Chrome", Chrome's
"Разрешить удаленную отладку?" prompt is pending — click "Разрешить" via UI
automation (AppleScript/System Events or an OS-automation tool), then retry.
"""

mcp = FastMCP("ozon", instructions=INSTRUCTIONS)

SORT_MAP = {
    "relevance": "score",
    "price_asc": "price",
    "price_desc": "price_desc",
    "rating": "rating",
    "new": "new",
    "discount": "discount",
}
DELIVERY_MAP = {"any": None, "express": "1", "today": "1", "tomorrow": "2"}


async def _paged(first_path: str, page: int, limit: int, collect) -> list:
    """Walk nextPage links; skip `page-1` pages, collect up to `limit` items."""
    out = []
    seen: set = set()
    path: str | None = first_path
    for _ in range(page - 1):
        if path is None:
            return out
        pg = await ozon.page_json(path)
        path = pg.next_page
    while path and len(out) < limit:
        pg = await ozon.page_json(path)
        for item in collect(pg):
            key = getattr(item, "sku", None) or None
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            out.append(item)
        path = pg.next_page
    return out[:limit]


def _search_path(query: str, sort: str, price_min, price_max, filters, delivery) -> str:
    path = f"/search/?text={quote(query)}&from_global=true"
    if sort != "relevance":
        path += f"&sorting={SORT_MAP[sort]}"
    if price_min is not None or price_max is not None:
        lo = float(price_min or 0)
        hi = float(price_max or 999_999_999)
        path += f"&currency_price={lo:.3f};{hi:.3f}"
    d = DELIVERY_MAP.get(delivery)
    if d:
        path += f"&delivery={d}"
    for key, vals in (filters or {}).items():
        if not isinstance(vals, list):
            vals = [vals]
        joined = ",".join(str(v) for v in vals)
        path += f"&{quote(str(key))}={quote(joined, safe=',;.')}"
    return path


@mcp.tool
async def search_products(
    query: str,
    sort: Literal[
        "relevance", "price_asc", "price_desc", "rating", "new", "discount"
    ] = "relevance",
    price_min: float | None = None,
    price_max: float | None = None,
    filters: dict[str, str | list[str]] | None = None,
    delivery: Literal["any", "express", "today", "tomorrow"] = "any",
    page: int = 1,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Search Ozon products.

    filters: {facet_key: value|[values]} — keys/values from get_search_filters.
    delivery: express and today both mean "delivery today" (Ozon has no express
    filter; filter further by delivery_date/express fields if needed).
    """
    now = datetime.now()
    path = _search_path(query, sort, price_min, price_max, filters, delivery)
    tiles = await _paged(path, page, limit, lambda p: parse.parse_tiles(p, now))
    return to_jsonable(tiles)


@mcp.tool
async def get_search_filters(query: str) -> list[dict[str, Any]]:
    """Available search facets (brand, attributes, delivery...) with keys for
    search_products' `filters` argument."""
    page = await ozon.page_json(f"/search/?text={quote(query)}&from_global=true")
    return to_jsonable(parse.parse_facets(page))


@mcp.tool
async def get_recommendations(page: int = 1, limit: int = 30) -> list[dict[str, Any]]:
    """Personal recommendations feed (the Ozon home page tiles)."""
    now = datetime.now()
    tiles = await _paged("/", page, limit, lambda p: parse.parse_tiles(p, now))
    return to_jsonable(tiles)


@mcp.tool
async def get_favorites(limit: int = 100) -> list[dict[str, Any]]:
    """User's favorites list."""
    now = datetime.now()
    tiles = await _paged("/my/favorites", 1, limit, lambda p: parse.parse_tiles(p, now))
    return to_jsonable(tiles)


@mcp.tool
async def get_cart() -> dict[str, Any]:
    """Cart contents: items with quantity and prices, total, unavailable items."""
    page = await ozon.page_json("/cart")
    return to_jsonable(parse.parse_cart(page))


@mcp.tool
async def list_orders(
    status: Literal["received", "returned", "cancelled", "all"] = "all",
    limit: int = 20,
    with_items: bool = False,
) -> list[dict[str, Any]]:
    """User's orders. status groups: received (получен), returned (возврат),
    cancelled (отменён), all = everything including in-progress."""
    now = datetime.now()
    orders = await _paged("/my/orderlist", 1, limit * 3, lambda p: parse.parse_orders(p, now))
    if status != "all":
        orders = [o for o in orders if o.status_group == status]
    orders = orders[:limit]
    if not with_items:
        for o in orders:
            o.items = []
    return to_jsonable(orders)


@mcp.tool
async def get_order(number: str) -> dict[str, Any]:
    """Order details: status, total, items with prices and sellers."""
    page = await ozon.page_json(f"/my/orderdetails/?order={quote(number)}")
    return to_jsonable(parse.parse_order_details(page, number, datetime.now()))


@mcp.tool
async def get_product(sku: str) -> dict[str, Any]:
    """Product card: title, prices, rating, seller, characteristics, availability."""
    page = await ozon.page_json(f"/product/{quote(sku)}/")
    return to_jsonable(parse.parse_product_details(page, sku))


@mcp.tool
async def get_delivery_options(skus: list[str]) -> list[dict[str, Any]]:
    """Delivery options for up to 20 SKUs: express, earliest courier, pickup
    point — with dates and prices for the account's selected address."""
    if len(skus) > 20:
        raise ValueError("не больше 20 SKU за вызов")
    now = datetime.now()
    out = []
    for sku in skus:
        page = await ozon.page_json(f"/product/{quote(str(sku))}/")
        comp = parse.find_component(page.raw.get("layout"), "webDelivery")
        if comp and comp.get("asyncData") and comp.get("stateId"):
            data = await ozon.widget_json(comp["stateId"], comp["asyncData"])
            out.append(parse.parse_delivery_sections(data, str(sku), now))
        else:
            out.append(parse.DeliveryOptions(sku=str(sku)))
    return to_jsonable(out)


@mcp.tool
async def add_to_cart(sku: str, quantity: int = 1) -> dict[str, Any]:
    """Add a product to the cart. Call ONLY on the user's explicit request."""
    await ozon.action("addToCart", {"items": [{"id": int(sku), "quantity": quantity}]})
    cart = parse.parse_cart(await ozon.page_json("/cart"))
    added = [i for i in cart["items"] if i.sku == str(sku)]
    return to_jsonable(
        {
            "added": bool(added),
            "sku": str(sku),
            "quantity": added[0].quantity if added else quantity,
            "cart_total": cart["total"],
            "cart_summary": cart["summary"],
        }
    )


@mcp.tool
async def add_to_favorites(sku: str) -> dict[str, Any]:
    """Add a product to favorites. Call ONLY on the user's explicit request."""
    await ozon.action("favoriteBatchAddItems", {"skus": [int(sku)]})
    now = datetime.now()
    tiles = await _paged("/my/favorites", 1, 100, lambda p: parse.parse_tiles(p, now))
    return to_jsonable({"added": str(sku) in {t.sku for t in tiles}, "sku": str(sku)})


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

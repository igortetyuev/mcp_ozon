"""Parsers turning Ozon widgetStates into models, plus RU date/price helpers."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from mcp_ozon.models import (
    CartItem,
    DeliveryOptions,
    DeliveryVariant,
    Facet,
    FacetValue,
    Order,
    ProductTile,
)

RU_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}

_PRICE_RE = re.compile(r"(\d+)(?:[.,](\d{1,2}))?")
_HOURS_RE = re.compile(r"за\s+(\d+)\s*час")
_DAY_MONTH_RE = re.compile(r"(\d{1,2})\s+(" + "|".join(RU_MONTHS) + r")(?:\s+(\d{4}))?")
_TIME_RANGE_RE = re.compile(r"(\d{1,2})[:.](\d{2})\s*(?:-|–|до)\s*(\d{1,2})[:.](\d{2})")  # noqa: RUF001


def parse_price(text: str | int | float | None) -> int | float | None:
    """'1 299 ₽' (any unicode spaces) -> 1299; '385,50 ₽'/'385.50 ₽' -> 385.5."""
    if text is None:
        return None
    if isinstance(text, int | float):
        return text
    m = _PRICE_RE.search(re.sub(r"\s+", "", text))
    if not m:
        return None
    if m.group(2):
        return float(f"{m.group(1)}.{m.group(2)}")
    return int(m.group(1))


def parse_ru_date(text: str, now: datetime) -> str | None:
    """RU delivery phrases -> ISO date (or datetime for hour-eta / time ranges)."""
    t = text.lower().strip()

    hours = _HOURS_RE.search(t)
    if hours:
        return (now + timedelta(hours=int(hours.group(1)))).isoformat(timespec="minutes")

    if "послезавтра" in t:
        day = now.date() + timedelta(days=2)
    elif "завтра" in t:
        day = now.date() + timedelta(days=1)
    elif "сегодня" in t:
        day = now.date()
    else:
        m = _DAY_MONTH_RE.search(t)
        if not m:
            return None
        d, month, year = int(m.group(1)), RU_MONTHS[m.group(2)], m.group(3)
        year = int(year) if year else now.year
        try:
            day = now.replace(year=year, month=month, day=d).date()
        except ValueError:
            return None
        if not m.group(3) and day < now.date():
            day = day.replace(year=day.year + 1)

    tr = _TIME_RANGE_RE.search(t)
    if tr:
        end_h, end_m = int(tr.group(3)), int(tr.group(4))
        return datetime.combine(day, datetime.min.time()).replace(
            hour=end_h, minute=end_m
        ).isoformat(timespec="minutes")
    return day.isoformat()


OZON = "https://www.ozon.ru"


def find_component(obj: Any, name: str) -> dict | None:
    """Find a layout component by name (e.g. 'webDelivery') in page.raw['layout']."""
    if isinstance(obj, dict):
        if obj.get("component") == name:
            return obj
        for v in obj.values():
            if (found := find_component(v, name)) is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            if (found := find_component(v, name)) is not None:
                return found
    return None


def _atom_texts(inner: dict) -> list[str]:
    return [
        x["text"]["text"]
        for x in inner.get("items", [])
        if x.get("type") == "text" and x.get("text", {}).get("text")
    ]


def _atom_icons(inner: dict) -> list[str]:
    return [
        x["icon"]["icon"]["icon"]
        for x in inner.get("items", [])
        if x.get("type") == "icon" and x.get("icon", {}).get("icon", {}).get("icon")
    ]


def parse_tile(item: dict, now: datetime) -> ProductTile:
    tile = ProductTile(sku="", title="", url="")
    tile.sku = str(item.get("sku") or item.get("id") or "")
    link = (item.get("action") or {}).get("link", "")
    tile.url = OZON + link.split("?")[0] if link else ""

    rating = reviews = None
    for atom in item.get("mainState", []):
        atype = atom.get("type")
        inner = atom.get(atype) or {}
        if atype == "textDS" and (
            atom.get("id") == "name"
            or (inner.get("testInfo") or {}).get("automatizationId") == "tile-name"
            or not tile.title
        ):
            tile.title = inner.get("text") or tile.title
        elif atype == "priceV2":
            for p in inner.get("price", []):
                if p.get("textStyle") == "PRICE":
                    tile.price = parse_price(p.get("text"))
                elif p.get("textStyle") == "ORIGINAL_PRICE":
                    tile.original_price = parse_price(p.get("text"))
            if (inner.get("priceStyle") or {}).get("styleType") == "CARD_PRICE":
                tile.card_price = tile.price
        elif atype == "labelListV2":
            icons = _atom_icons(inner)
            if any("star" in i for i in icons):
                for text in _atom_texts(inner):
                    if rating is None and re.match(r"^\d+[.,]\d+$", text):
                        rating = float(text.replace(",", "."))
                    elif "отзыв" in text:
                        reviews = parse_price(text)
            elif any("express" in i for i in icons):
                tile.express = True
    tile.rating, tile.reviews = rating, reviews
    if tile.original_price and tile.price and tile.original_price > tile.price:
        tile.discount_pct = round((1 - tile.price / tile.original_price) * 100)

    btn = item.get("multiButton", {}).get("ozonButton", {})
    tile.in_stock = "addToCart" in btn
    label = (btn.get("addToCart") or {}).get("actionButton", {}).get("title")
    if label:
        tile.delivery_label = label
        tile.delivery_date = parse_ru_date(label, now)
        if re.search(r"экспресс|за\s*\d+\s*час", label, re.IGNORECASE):
            tile.express = True
    return tile


def parse_tiles(page, now: datetime) -> list[ProductTile]:
    seen: set[str] = set()
    tiles = []
    for widget in page.widgets("tileGrid"):
        for item in widget.get("items", []):
            tile = parse_tile(item, now)
            if tile.sku and tile.sku not in seen:
                seen.add(tile.sku)
                tiles.append(tile)
    return tiles


def parse_facets(page) -> list[Facet]:
    facets = []
    for widget in page.widgets("filtersDesktop"):
        for section in widget.get("sections", []):
            for flt in section.get("filters", []):
                key = flt.get("key")
                ftype = flt.get("type")
                inner = flt.get(ftype) or {}
                if not key or ftype == "categoryFilter":
                    continue
                title = inner.get("title") or key
                values: list[FacetValue] = []
                if ftype == "boolFilter":
                    values.append(FacetValue(value="1", title=title))
                elif "rangeFilter" in inner or ftype == "rangeFilter":
                    rf = inner.get("rangeFilter", inner)
                    lo, hi = rf.get("minValue"), rf.get("maxValue")
                    values.append(
                        FacetValue(
                            value=f"{lo};{hi}", title=f"от {lo} до {hi} (укажите lo;hi)"
                        )
                    )
                for sec in inner.get("sections", []):
                    for v in sec.get("items", []):
                        values.append(
                            FacetValue(
                                value=str(v.get("key", "")),
                                title=(v.get("title") or {}).get("text", ""),
                                count=v.get("count"),
                            )
                        )
                facets.append(Facet(key=key, title=title, values=values))
    return facets


_STATUS_GROUP = [
    (re.compile(r"получен", re.I), "received"),
    (re.compile(r"возвращ|возврат", re.I), "returned"),
    (re.compile(r"отмен", re.I), "cancelled"),
]


def status_group(status_text: str) -> str:
    for pattern, group in _STATUS_GROUP:
        if pattern.search(status_text):
            return group
    return "in_progress"


def parse_orders(page, now: datetime) -> list[Order]:
    orders = []
    for widget in page.widgets("orderList"):
        for o in widget.get("ordersV2", []):
            link = (o.get("common") or {}).get("action", {}).get("link", "")
            number = link.split("order=")[-1].split("&")[0] if "order=" in link else ""
            status = (
                o.get("leftBlock", {}).get("textIcon", {}).get("text", {}).get("text", "")
            ).replace("\xa0", " ")
            date = None
            m = re.match(r"^(Получен|Отменён|Возвращён)\s+(.+)$", status)
            if m:
                date = parse_ru_date(m.group(2), now)
            items = []
            for p in (o.get("rightBlock", {}).get("products", {}) or {}).get("products", []):
                price = parse_price(
                    (p.get("price", {}).get("price") or [{}])[0].get("text")
                )
                items.append(ProductTile(sku="", title="", url="", price=price))
            orders.append(
                Order(
                    number=number,
                    status=status or None,
                    status_group=status_group(status),
                    date=date,
                    items=items,
                )
            )
    return orders


def parse_order_details(page, number: str, now: datetime) -> Order:
    order = Order(number=number)
    for widget in page.widgets("orderDoneTotal"):
        right = (widget.get("total") or {}).get("right") or {}
        price = right.get("price")
        order.total = parse_price(
            price.get("text") if isinstance(price, dict) else price
        )
    for widget in page.widgets("shipmentWidget"):
        for h in widget.get("header", []):
            ti = h.get("textIcon") or {}
            text = (ti.get("text") or {}).get("text", "").replace("\xa0", " ")
            if text and not order.status:
                order.status = text
                order.status_group = status_group(text)
        for group in widget.get("items", []):
            for seller in group.get("sellers", []):
                sname = (seller.get("name") or {}).get("text")
                for p in seller.get("products", []):
                    action = (p.get("title", {}).get("common") or {}).get("action") or {}
                    link = action.get("link", "")
                    sku = str(action.get("id") or "")
                    price = parse_price(
                        (p.get("price", {}).get("price") or [{}])[0].get("text")
                    )
                    order.items.append(
                        ProductTile(
                            sku=sku,
                            title=(p.get("title", {}).get("name") or {}).get("text", ""),
                            url=OZON + link.split("?")[0] if link else "",
                            price=price,
                            seller=sname,
                        )
                    )
    if order.status:
        m = re.match(r"^(Получен|Отменён|Возвращён)\s+(.+)$", order.status)
        if m:
            order.date = parse_ru_date(m.group(2), now)
    return order


def parse_cart(page) -> dict:
    items: list[CartItem] = []
    unavailable_header = re.compile(r"недоступн|нет в наличии", re.I)
    for widget in page.widgets("cartSplit"):
        header_text = json_text(widget.get("header")) or ""
        available = not unavailable_header.search(header_text)
        for ci in widget.get("cartItems", []):
            p = ci.get("product") or {}
            title = ""
            for atom in p.get("titleColumn", []):
                if atom.get("type") == "text" and not title:
                    title = (atom.get("text") or {}).get("text", "")
            card = regular = None
            for atom in p.get("priceColumn", []):
                for el in (atom.get("prices") or {}).get("elements") or []:
                    style = (el.get("priceStyle") or {}).get("styleType")
                    for pe in el.get("price", []):
                        if pe.get("textStyle") != "PRICE":
                            continue
                        v = parse_price(pe.get("text"))
                        if style == "CARD_PRICE":
                            card = card or v
                        else:
                            regular = regular or v
            qty = ((ci.get("controls") or {}).get("quantity") or {}).get("current") or 1
            sku = str(p.get("id") or "")
            items.append(
                CartItem(
                    sku=sku,
                    title=title,
                    quantity=qty,
                    price=card or regular,
                    card_price=card,
                    original_price=regular if card else None,
                    url=f"{OZON}/product/{sku}" if sku else None,
                    available=available,
                )
            )
    total = count = None
    for widget in page.widgets("total"):
        summary = widget.get("summary") or {}
        total = parse_price((summary.get("footer") or {}).get("price"))
        header = summary.get("header") or {}
        count = header.get("info") or header.get("title")
    return {"items": items, "total": total, "summary": count}


def json_text(obj: Any) -> str:
    """Concatenate all 'text' fields in a widget fragment."""
    if isinstance(obj, dict):
        parts = []
        for k, v in obj.items():
            if k == "text" and isinstance(v, str):
                parts.append(v)
            else:
                parts.append(json_text(v))
        return " ".join(p for p in parts if p)
    if isinstance(obj, list):
        return " ".join(json_text(v) for v in obj)
    return ""


def parse_product_details(page, sku: str) -> dict:
    out: dict[str, Any] = {"sku": sku, "url": f"{OZON}/product/{sku}"}
    for key, widget in page.widget_items("web"):
        if key.startswith("webProductHeading"):
            out["title"] = widget.get("title")
        elif key.startswith("webPrice-"):
            out["in_stock"] = widget.get("isAvailable")
            price = parse_price(widget.get("price"))
            card = parse_price(widget.get("cardPrice"))
            if card and (price is None or card <= price):
                out["price"] = card
                if price and card < price:
                    out["original_price"] = price
                    out["discount_pct"] = round((1 - card / price) * 100)
            else:
                out["price"] = price
            out["card_price"] = card
        elif key.startswith("webSingleProductScore"):
            m = re.match(r"([\d.,]+)\s*•\s*(\d[\d\s]*)", widget.get("text", ""))
            if m:
                out["rating"] = float(m.group(1).replace(",", "."))
                out["reviews"] = parse_price(m.group(2))
        elif key.startswith("webCurrentSeller"):
            seller = ((widget.get("sellerCell") or {}).get("rightBlock") or {})
            out["seller"] = (seller.get("title") or {}).get("text") or json_text(seller) or None
        elif key.startswith("webShortCharacteristics"):
            chars = []
            for ch in widget.get("characteristics", widget.get("items", [])):
                name = json_text(ch.get("title") or ch.get("name"))
                val = json_text(ch.get("value") or ch.get("values"))
                if name or val:
                    chars.append({"name": name, "value": val})
            if chars:
                out["characteristics"] = chars
        elif key.startswith("textBlock"):
            text = json_text(widget)
            if text and "description" not in out:
                out["description"] = text[:2000]
    return out


def parse_delivery_sections(state: dict, sku: str, now: datetime) -> DeliveryOptions:
    opts = DeliveryOptions(sku=sku)
    for sec in state.get("state", {}).get("sections", []):
        texts = [
            c.get("content", "")
            for c in sec.get("descriptionRs", [])
            if c.get("type") == "text"
        ]
        if sec.get("type") == "addressSelect":
            opts.address = texts[0] if texts else None
            continue
        if not texts:
            continue
        name, eta = texts[0], texts[-1] if len(texts) > 1 else None
        badge = (sec.get("priceBadge") or {}).get("text", "")
        price = parse_price(badge)
        if price is None and re.search(r"входит в заказ|бесплат", badge, re.I):
            price = 0
        variant = DeliveryVariant(
            available=True,
            eta_text=eta,
            date=parse_ru_date(eta, now) if eta else None,
            price=price,
        )
        low = name.lower()
        if "экспресс" in low or "час" in low:
            opts.express = opts.express or variant
        elif "курьер" in low:
            opts.courier_earliest = opts.courier_earliest or variant
        elif "пункт" in low or "постамат" in low or "пвз" in low:
            opts.pickup = opts.pickup or variant
    if opts.express is None:
        opts.express = DeliveryVariant(available=False)
    return opts

"""Bulk export of the user's Ozon orders + favorites to exports/*.json.

Runs a dedicated CDP session via mcp_ozon.ozon (no MCP server needed).
Tables produced:
  ozon_orders        one row per order number (placed date, totals breakdown)
  ozon_shipments     one row per orderlist entry (per-posting status/dates)
  ozon_order_items   one row per purchased product line (from orderdetails)
  ozon_favorites     one row per favorites tile

Usage: uv run python scripts/export.py [--no-orders] [--no-favorites]
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from mcp_ozon import ozon, parse
from mcp_ozon.ozon import PageJson
from mcp_ozon.parse import RU_MONTHS, parse_price

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "exports"
APPLESCRIPT = ROOT / "scripts" / "allow_debugging.applescript"

STOP = asyncio.Event()


async def _click_allow() -> None:
    """Click Chrome's 'Разрешить удаленную отладку?' prompt whenever it appears."""
    while not STOP.is_set():
        await asyncio.to_thread(
            subprocess.run,
            ["osascript", str(APPLESCRIPT)],
            capture_output=True,
            timeout=15,
        )
        await asyncio.sleep(3)


async def fetch_page(path: str) -> PageJson:
    """page_json with reconnect+retry; the allow-clicker handles new-WS prompts."""
    last: Exception | None = None
    for attempt in range(5):
        try:
            return await ozon.page_json(path)
        except ozon.OzonError:
            raise  # auth/anti-bot/api errors are not retryable here
        except Exception as exc:
            last = exc
            print(f"  retry {attempt + 1} on {path}: {exc}", file=sys.stderr)
            await ozon.reset_session()
            await asyncio.sleep(2)
    raise ozon.OzonError(f"cannot fetch {path}: {last}")


def _list_next_page(pg: PageJson) -> str | None:
    """Next page of the MAIN list only: never follow recommendation paginators
    (their links always contain 'recom'); stop instead of falling back."""
    if isinstance(pg.raw.get("nextPage"), str) and "recom" not in pg.raw["nextPage"]:
        return pg.raw["nextPage"]
    for k, w in pg.widget_states.items():
        if "aginator" in k and isinstance(w, dict):
            nxt = w.get("nextPage")
            if isinstance(nxt, str) and nxt and "recom" not in nxt:
                return nxt
    return None


async def walk_pages(first_path: str, label: str, max_pages: int = 400):
    """Yield PageJson objects following main-list nextPage links until the end."""
    path: str | None = first_path
    seen: set[str] = set()
    n = 0
    while path and path not in seen and n < max_pages:
        seen.add(path)
        pg = await fetch_page(path)
        n += 1
        n_widgets = len(pg.widgets("orderList") or pg.widgets("tileGrid"))
        print(f"[{label}] page {n}: {n_widgets} widget(s)", file=sys.stderr)
        yield pg
        path = _list_next_page(pg)
    print(f"[{label}] {n} page(s)", file=sys.stderr)


_DAY_MONTH_RE = re.compile(r"(\d{1,2})\s+(" + "|".join(RU_MONTHS) + r")(?:\s+(\d{4}))?")
_ETA_RANGE_RE = re.compile(
    r"(\d{1,2})\s*[–—-]\s*(\d{1,2})\s+(" + "|".join(RU_MONTHS) + r")"  # noqa: RUF001
)
_STATUS_DATE_RE = re.compile(r"^(Получен|Отменён|Возвращён|Возврат)\s+(.+)$", re.I)
_PLACED_RE = re.compile(r"Заказ от\s+(.+)$", re.I)
_ORDERNUM_RE = re.compile(r"№\s*([\d-]+)")
_NITEMS_RE = re.compile(r"(\d+)\s*товар")


_DMY_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")


def past_ru_date(text: str, now: datetime) -> str | None:
    """'15 ноября'/'15.09.2014' -> most recent matching date (rolls back a year)."""
    dmy = _DMY_RE.search(text)
    if dmy:
        try:
            return f"{int(dmy.group(3)):04d}-{int(dmy.group(2)):02d}-{int(dmy.group(1)):02d}"
        except ValueError:
            return None
    m = _DAY_MONTH_RE.search(text.lower())
    if not m:
        return None
    d, month, year = int(m.group(1)), RU_MONTHS[m.group(2)], m.group(3)
    try:
        day = now.replace(year=int(year) if year else now.year, month=month, day=d).date()
    except ValueError:
        return None
    if not year and day > now.date():
        day = day.replace(year=day.year - 1)
    return day.isoformat()


def parse_eta(text: str, now: datetime) -> dict:
    """'Ожидаем 24 – 27 ноября' -> {eta_text, eta_from, eta_to} (future dates)."""  # noqa: RUF002
    t = text.replace("\xa0", " ").replace("\u202f", " ")
    out: dict = {"eta_text": t}
    m = _ETA_RANGE_RE.search(t.lower())
    if m:
        d1, d2, month = int(m.group(1)), int(m.group(2)), RU_MONTHS[m.group(3)]
        year = now.year + (1 if (month, d2) < (now.month, now.day) else 0)
        try:
            out["eta_from"] = f"{year}-{month:02d}-{d1:02d}"
            out["eta_to"] = f"{year}-{month:02d}-{d2:02d}"
        except ValueError:
            pass
        return out
    single = parse.parse_ru_date(t, now)
    if single:
        out["eta_to"] = single
    return out


def _track_ts(text: str | None) -> str | None:
    """'04.11.2026 10:19' -> '2026-11-04T10:19'."""
    if not text:
        return None
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})", text.strip())
    if not m:
        return None
    d, mo, y, h, mi = (int(g) for g in m.groups())
    return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}"


def _signed_price(text: str | None) -> int | None:
    if text is None:
        return None
    v = parse_price(text)
    if v is not None and re.match(r"^\s*[–—−-]", text):  # noqa: RUF001
        v = -v
    return v


def parse_shipment_entry(o: dict, idx: int, now: datetime) -> dict:
    lb = o.get("leftBlock") or {}
    ti = lb.get("textIcon") or {}
    status = (ti.get("text") or {}).get("text", "").replace("\xa0", " ").replace("\u202f", " ")
    link = (o.get("common") or {}).get("action", {}).get("link", "")
    number = link.split("order=")[-1].split("&")[0] if "order=" in link else ""
    steps = (ti.get("timeline") or {}).get("steps") or []
    track_first = steps[0].get("subtitle") if steps else None
    track_last = steps[-1] if steps else None
    row = {
        "order_number": number,
        "entry_index": idx,
        "status": status or None,
        "status_group": parse.status_group(status),
        "delivery_type": ((lb.get("title") or {}).get("text") or "").replace("\xa0", " ") or None,
        "track_first_at": _track_ts(track_first),
        "track_last_text": (track_last or {}).get("title", {}).get("text"),
        "track_last_at": _track_ts((track_last or {}).get("subtitle")),
        "order_url": "https://www.ozon.ru" + link if link else None,
    }
    m = _STATUS_DATE_RE.match(status)
    if m:
        row["status_date"] = past_ru_date(m.group(2), now)
    subtitle = (lb.get("subtitle") or {}).get("text") or ""
    subtitle = subtitle.replace("\xa0", " ").replace("\u202f", " ")
    if subtitle:
        eta = parse_eta(subtitle, now)
        if eta.get("eta_to") or "ожида" in subtitle.lower():
            row.update(eta)
        else:
            row["delivery_type"] = row["delivery_type"] or subtitle
    products = ((o.get("rightBlock") or {}).get("products") or {}).get("products") or []
    prices = [
        _signed_price((p.get("price", {}).get("price") or [{}])[0].get("text"))
        for p in products
    ]
    row["n_products_preview"] = len(products)
    row["preview_total"] = sum(p for p in prices if p is not None) or None
    return row


def parse_order_details(pg: PageJson, number: str, now: datetime) -> tuple[dict, list[dict]]:
    order = {"order_number": number, "order_url": f"https://www.ozon.ru/my/orderdetails/?order={number}"}
    items: list[dict] = []
    for w in pg.widgets("titleWithTimer"):
        m = _PLACED_RE.search((w.get("title") or {}).get("text") or "")
        if m:
            order["placed_date"] = past_ru_date(m.group(1), now)
    for w in pg.widgets("orderDoneTotal"):
        right = (w.get("total") or {}).get("right") or {}
        price = right.get("price")
        order["total"] = _signed_price(price.get("text") if isinstance(price, dict) else price)
        breakdown = {}
        for el in (w.get("prices") or {}).get("elements") or []:
            name = ((el.get("left") or {}).get("title") or {}).get("text") or "?"
            r = el.get("right") or {}
            text = ((r.get("atomPrice") or {}).get("price") or [{}])[0].get("text") or (
                (r.get("price") or {}) if isinstance(r.get("price"), dict) else {}
            ).get("text")
            breakdown[name] = text
            if name == "Товары":
                order["items_total"] = _signed_price(text)
            elif "Скидк" in name or "доставка" in name.lower():
                order["discount_delivery"] = _signed_price(text)
            else:
                if nm := _ORDERNUM_RE.search(text or ""):
                    order["order_number"] = nm.group(1)
                if ni := _NITEMS_RE.search(text or ""):
                    order["n_items"] = int(ni.group(1))
        order["totals_breakdown"] = breakdown
    for w in pg.widgets("shipmentWidget"):
        shipment_id = str(w.get("shipmentId") or "") or None
        ship_status = None
        for h in w.get("header") or []:
            text = ((h.get("textIcon") or {}).get("text") or {}).get("text")
            if text:
                ship_status = text.replace("\xa0", " ")
                break
        for group in w.get("items") or []:
            for seller in group.get("sellers") or []:
                sname = (seller.get("name") or {}).get("text")
                for p in seller.get("products") or []:
                    action = (p.get("title", {}).get("common") or {}).get("action") or {}
                    link = action.get("link", "")
                    items.append(
                        {
                            "order_number": order["order_number"],
                            "shipment_id": shipment_id,
                            "shipment_status": ship_status,
                            "sku": str(action.get("id") or ""),
                            "title": (p.get("title", {}).get("name") or {}).get("text", ""),
                            "url": parse.OZON + link.split("?")[0] if link else None,
                            "seller": sname,
                            "price": _signed_price(
                                (p.get("price", {}).get("price") or [{}])[0].get("text")
                            ),
                            "attributes": [
                                a.get("text") for a in p.get("attributes") or [] if a.get("text")
                            ],
                            "is_favorite": p.get("isFavorite"),
                            "item_id": p.get("itemId"),
                        }
                    )
    return order, items


def _save(name: str, rows: list[dict], exported_at: str) -> None:
    for r in rows:
        r.setdefault("exported_at", exported_at)
    (OUT / name).write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print(f"saved {len(rows)} rows -> exports/{name}", file=sys.stderr)


async def main() -> None:
    do_orders = "--no-orders" not in sys.argv
    do_fav = "--no-favorites" not in sys.argv
    OUT.mkdir(exist_ok=True)
    now = datetime.now()
    exported_at = now.isoformat(timespec="seconds")
    clicker = asyncio.create_task(_click_allow())
    try:
        if do_orders:
            shipments: list[dict] = []
            ship_keys: set[tuple] = set()
            for tab, path in (
                ("active", "/my/orderlist"),
                ("archive", "/my/orderlist?selectedTab=archive"),
            ):
                async for pg in walk_pages(path, f"orderlist:{tab}"):
                    for w in pg.widgets("orderList"):
                        for o in w.get("ordersV2") or []:
                            row = parse_shipment_entry(o, len(shipments), now)
                            row["tab"] = tab
                            key = tuple(
                                row.get(f)
                                for f in (
                                    "order_number", "status", "status_date", "eta_to",
                                    "delivery_type", "n_products_preview", "preview_total",
                                )
                            )
                            if key in ship_keys:
                                continue
                            ship_keys.add(key)
                            shipments.append(row)
            _save("ozon_shipments.json", shipments, exported_at)

            numbers = []
            for s in shipments:
                if s["order_number"] and s["order_number"] not in numbers:
                    numbers.append(s["order_number"])
            orders: list[dict] = []
            items: list[dict] = []
            for i, num in enumerate(numbers, 1):
                print(f"[orderdetails] {i}/{len(numbers)} {num}", file=sys.stderr)
                pg = await fetch_page(f"/my/orderdetails/?order={num}")
                order, lines = parse_order_details(pg, num, now)
                orders.append(order)
                items.extend(lines)
                if i % 25 == 0:
                    _save("ozon_orders.json", orders, exported_at)
                    _save("ozon_order_items.json", items, exported_at)
            groups = {}
            for s in shipments:
                groups.setdefault(s["order_number"], []).append(s)
            for order in orders:
                ships = groups.get(order["order_number"], [])
                order["n_shipments"] = len(ships)
                order["status_groups"] = sorted(
                    {s["status_group"] for s in ships if s.get("status_group")}
                )
                order["all_received"] = bool(ships) and all(
                    s["status_group"] in ("received", "cancelled") for s in ships
                )
            _save("ozon_orders.json", orders, exported_at)
            _save("ozon_order_items.json", items, exported_at)

        if do_fav:
            favorites: list[dict] = []
            seen: set[str] = set()
            async for pg in walk_pages("/my/favorites", "favorites"):
                for tile in parse.parse_tiles(pg, now):
                    if tile.sku and tile.sku not in seen:
                        seen.add(tile.sku)
                        favorites.append(asdict(tile))
            _save("ozon_favorites.json", favorites, exported_at)

        meta = {"exported_at": exported_at}
        for name in ("orders", "shipments", "order_items", "favorites"):
            f = OUT / f"ozon_{name}.json"
            meta[name] = len(json.loads(f.read_text())) if f.exists() else 0
        (OUT / "ozon_export_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
        print(json.dumps(meta, ensure_ascii=False), file=sys.stderr)
    finally:
        STOP.set()
        clicker.cancel()
        await ozon.reset_session()


if __name__ == "__main__":
    asyncio.run(main())

"""Recon: dump Ozon page-JSON responses to fixtures, record cart/favorites actions.

Usage:
  uv run python scripts/recon.py                 # fetch default page set
  uv run python scripts/recon.py name=/path ...  # fetch custom pages
  uv run python scripts/recon.py --record-actions [seconds]
  uv run python scripts/recon.py --map           # widget-key table from raw fixtures

Raw responses go to tests/fixtures/raw/ (gitignored), sanitized copies to tests/fixtures/.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mcp_ozon.cdp import CdpSession
from mcp_ozon.ozon import page_json, reset_session

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
RAW = FIXTURES / "raw"

DEFAULT_PAGES = {
    "home": "/",
    "search_usbc": "/search/?text=кабель usb-c",
    "favorites": "/my/favorites",
    "cart": "/cart",
    "orderlist": "/my/orderlist",
}

SENSITIVE_KEY = re.compile(
    r"(first|last|middle|full)?_?name|initials|avatar|phone|address|email|token|"
    r"guid|uuid|user_?id|fio|comment|longitude|latitude|zip|flat|apartment|entrance|"
    r"floor|posting_?id|shipment_?id|start_?page_?id",
    re.IGNORECASE,
)
ORDER_NO = re.compile(r"(?<![\dA-Za-z-])\d{7,}-\d{1,4}(?:-\d+)?(?![\dA-Za-z-])")
PHONE = re.compile(r"\+?[78][\s(]?\d{3}[\s)\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
STREET = re.compile(
    r"(улица|ул\.|проспект|пр-т|пр\.|переулок|пер\.|шоссе|бульвар|микрорайон)"
    r"\s+[^\"\\]{1,80}"
)
URL_PARAM_SECRET = re.compile(
    r"((?:url_?sign|token|auth|sign|item_?ids|shipment|order_?id|"
    r"order_worldwide_id|start_?page_?id)=[^&\"\\\s]{1,})",
    re.IGNORECASE,
)
ID_FIELD = re.compile(
    r"((?:shipment_?id|posting_?id|item_?ids?|order_?id)[\"'\\:=\s-]*)(\d{4,})",
    re.IGNORECASE,
)
# Whole widgets that carry user identity / address; dropped entirely.
WIDGET_DROP = re.compile(
    r"^(profileLogo|userAvatar|profileMenu|accountList|addressBookBarWeb|orderInfo)-"
)

_order_map: dict[str, str] = {}


def _fake_order(match: re.Match) -> str:
    real = match.group(0)
    if real not in _order_map:
        _order_map[real] = f"90000000-{len(_order_map) + 1:04d}"
    return _order_map[real]


def _mask_tracking(obj: Any) -> Any:
    """trackingInfo blocks carry per-user telemetry keys — mask every `key`."""
    if isinstance(obj, dict):
        return {
            k: "***" if k == "key" and isinstance(v, str) else _mask_tracking(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_mask_tracking(v) for v in obj]
    return obj


def sanitize(obj: Any, in_sensitive_key: bool = False) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if WIDGET_DROP.match(k):
                out[k] = "***"
            elif k == "trackingInfo":
                out[k] = _mask_tracking(v)
            else:
                out[k] = sanitize(v, in_sensitive_key=bool(SENSITIVE_KEY.search(k)))
        return out
    if isinstance(obj, list):
        return [sanitize(v, in_sensitive_key=in_sensitive_key) for v in obj]
    if isinstance(obj, str):
        if in_sensitive_key:
            return "***"
        obj = ORDER_NO.sub(_fake_order, obj)
        obj = PHONE.sub("***phone***", obj)
        obj = STREET.sub("***address***", obj)
        obj = URL_PARAM_SECRET.sub("***", obj)
        obj = ID_FIELD.sub(r"\g<1>***", obj)
        return EMAIL.sub("***email***", obj)
    if in_sensitive_key and isinstance(obj, int | float):
        return 0
    return obj


async def fetch_pages(pages: dict[str, str]) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for name, path in pages.items():
        print(f"== {name}: {path}")
        try:
            page = await page_json(path)
        except Exception as exc:
            print(f"   FAILED: {exc}")
            continue
        (RAW / f"{name}.json").write_text(json.dumps(page.raw, ensure_ascii=False))
        clean = sanitize(page.raw)
        (FIXTURES / f"{name}.json").write_text(
            json.dumps(clean, ensure_ascii=False, indent=1)
        )
        keys = list(page.widget_states)
        print(f"   {len(keys)} widgets: {', '.join(keys[:8])}{'…' if len(keys) > 8 else ''}")
    await reset_session()


async def record_actions(seconds: float) -> None:
    """Open a foreground ozon.ru tab and log mutating requests while you click."""
    session = CdpSession()
    await session.connect()
    res = await session.send(
        "Target.createTarget", {"url": "https://www.ozon.ru/", "background": False}
    )
    target_id = res["targetId"]
    res = await session.send(
        "Target.attachToTarget", {"targetId": target_id, "flatten": True}
    )
    sid = res["sessionId"]
    await session.send("Network.enable", session_id=sid)
    captured: list[dict] = []
    interesting = re.compile(r"_action|addToCart|add_to_cart|favorit|cart/add|composer-api")

    def on_request(params: dict, _sid: str | None) -> None:
        req = params.get("request", {})
        url = req.get("url", "")
        if interesting.search(url) or req.get("method") == "POST":
            captured.append(
                {
                    "url": url,
                    "method": req.get("method"),
                    "postData": req.get("postData"),
                    "headers": {
                        k: v
                        for k, v in req.get("headers", {}).items()
                        if k.lower() in ("content-type", "x-o3-app-name", "x-o3-app-version")
                    },
                }
            )

    session.on("Network.requestWillBeSent", on_request)
    print(
        f"Recording for {seconds:.0f}s — in the new Chrome tab open a product "
        "and click 'В корзину' / 'В избранное'"  # noqa: RUF001
    )
    await asyncio.sleep(seconds)
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / "actions.json"
    out.write_text(json.dumps(captured, ensure_ascii=False, indent=1))
    print(f"{len(captured)} requests -> {out}")
    for c in captured:
        print(f"  {c['method']} {c['url'][:120]}")
    await session.close()


def print_map() -> None:
    for f in sorted(RAW.glob("*.json")):
        raw = json.loads(f.read_text())
        states = raw.get("widgetStates") or {}
        print(f"\n== {f.name} ({len(states)} widgets)")
        for key, value in states.items():
            try:
                decoded = json.loads(value) if isinstance(value, str) else value
                if isinstance(decoded, dict):
                    fields = ", ".join(list(decoded)[:12])
                else:
                    fields = type(decoded).__name__
            except json.JSONDecodeError:
                fields = "<unparsed string>"
            print(f"   {key}: {fields}")


def resanitize() -> None:
    for f in RAW.glob("*.json"):
        raw = json.loads(f.read_text())
        (FIXTURES / f.name).write_text(
            json.dumps(sanitize(raw), ensure_ascii=False, indent=1)
        )
        print("resanitized", f.name)


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--resanitize":
        resanitize()
        return
    if args and args[0] == "--map":
        print_map()
        return
    if args and args[0] == "--record-actions":
        seconds = float(args[1]) if len(args) > 1 else 120
        asyncio.run(record_actions(seconds))
        return
    pages = dict(DEFAULT_PAGES)
    for arg in args:
        name, _, path = arg.partition("=")
        if path:
            pages[name] = path
    asyncio.run(fetch_pages(pages))


if __name__ == "__main__":
    main()

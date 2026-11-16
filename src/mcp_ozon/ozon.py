"""Ozon internal page-JSON API, fetched inside the logged-in Chrome worker tab."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from mcp_ozon.cdp import CdpSession

API_PATH = "/api/entrypoint-api.bx/page/json/v2"
MIN_INTERVAL_S = 0.5


class OzonError(RuntimeError):
    pass


class OzonAuthError(OzonError):
    pass


class OzonBlockedError(OzonError):
    pass


@dataclass
class PageJson:
    raw: dict[str, Any]
    widget_states: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> PageJson:
        states = {}
        for key, value in (raw.get("widgetStates") or {}).items():
            if isinstance(value, str):
                with contextlib.suppress(json.JSONDecodeError):
                    value = json.loads(value)
            states[key] = value
        return cls(raw=raw, widget_states=states)

    def widgets(self, prefix: str) -> list[dict]:
        """Decoded widget payloads whose key starts with prefix (e.g. 'tileGrid')."""
        return [v for k, v in self.widget_states.items() if k.startswith(prefix)]

    def widget_items(self, prefix: str) -> list[tuple[str, dict]]:
        return [(k, v) for k, v in self.widget_states.items() if k.startswith(prefix)]

    @property
    def next_page(self) -> str | None:
        nxt = self.raw.get("nextPage")
        if isinstance(nxt, str) and nxt:
            return nxt
        candidates = [
            w["nextPage"]
            for k, w in self.widget_states.items()
            if "aginator" in k and isinstance(w, dict) and w.get("nextPage")
        ]
        # recommendation blocks get their own paginators; prefer the main one
        for c in candidates:
            if "recom" not in c:
                return c
        return candidates[0] if candidates else None


_lock = asyncio.Lock()
_last_request = 0.0
_session: CdpSession | None = None


async def get_session() -> CdpSession:
    global _session
    if _session is None:
        _session = CdpSession()
        await _session.connect()
    return _session


async def reset_session() -> None:
    global _session
    if _session is not None:
        await _session.close()
        _session = None


def _check_response(status: int, url: str, body: str, cookie: str) -> None:
    if "ozonid" in url or "__Secure-user-id=0" in cookie:
        raise OzonAuthError("Войдите в Ozon в Chrome и повторите")
    if status in (401, 403) or "Доступ ограничен" in body:
        raise OzonBlockedError(
            "Ozon антибот: откройте ozon.ru в Chrome, пройдите проверку и повторите"
        )
    if status != 200:
        raise OzonError(f"Ozon API вернул HTTP {status}")


async def _fetch(url: str, method: str = "GET", body: str | None = None) -> dict:
    """Throttled fetch inside the worker tab. Returns {status, url, body, cookie}."""
    global _last_request
    session = await get_session()
    options = {"method": method, "credentials": "include"}
    if body is not None:
        options["headers"] = {"Content-Type": "application/json"}
        options["body"] = body
    js = (
        "(async () => { const r = await fetch("
        + json.dumps(url)
        + ", "
        + json.dumps(options)
        + "); const body = await r.text();"
        " return {status: r.status, url: r.url, body, cookie: document.cookie}; })()"
    )
    async with _lock:
        await asyncio.sleep(max(0.0, MIN_INTERVAL_S - (time.monotonic() - _last_request)))
        result = await session.evaluate(js)
        _last_request = time.monotonic()
    _check_response(result["status"], result["url"], result["body"], result.get("cookie", ""))
    return result


async def page_json(path: str) -> PageJson:
    """Fetch the page-JSON API inside the worker tab. Requests are serialized, >=0.5s apart."""
    url = API_PATH + "?url=" + quote(path, safe="")
    result = await _fetch(url)
    try:
        raw = json.loads(result["body"])
    except json.JSONDecodeError as exc:
        raise OzonError(f"Ozon вернул не-JSON на {path}") from exc
    return PageJson.from_raw(raw)


async def widget_json(state_id: str, async_data: str) -> dict:
    """Fetch a lazy widget (e.g. webDelivery) via the composer widget API."""
    url = f"/api/composer-api.bx/widget/json/v2?widgetStateId={state_id}"
    result = await _fetch(url, "POST", json.dumps({"asyncData": async_data}))
    try:
        return json.loads(result["body"])
    except json.JSONDecodeError as exc:
        raise OzonError(f"Ozon вернул не-JSON на widget {state_id}") from exc


async def action(name: str, body: dict) -> dict:
    """POST a composer action (addToCart, favoriteBatchAddItems, ...)."""
    url = f"/api/composer-api.bx/_action/v2/{name}"
    result = await _fetch(url, "POST", json.dumps(body))
    try:
        data = json.loads(result["body"])
    except json.JSONDecodeError as exc:
        raise OzonError(f"Ozon вернул не-JSON на action {name}") from exc
    err = (data.get("data") or {}).get("exceptionMessage") or data.get("error")
    if err:
        raise OzonError(f"{name}: {err}")
    return data


async def _cli(path: str) -> None:
    page = await page_json(path)
    for key in page.widget_states:
        print(key)
    if page.next_page:
        print(f"nextPage: {page.next_page}")
    await reset_session()


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m mcp_ozon.ozon '<path>'", file=sys.stderr)
        sys.exit(2)
    asyncio.run(_cli(sys.argv[1]))


if __name__ == "__main__":
    main()

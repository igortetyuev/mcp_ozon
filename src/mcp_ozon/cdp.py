"""Minimal CDP client driving the user's Chrome via remote debugging (Chrome 144+ auto-connect)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


def _default_profile_dir() -> Path:
    """Chrome/Chromium profile dir: MCP_OZON_PROFILE_DIR, else platform default."""
    if override := os.environ.get("MCP_OZON_PROFILE_DIR"):
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Google/Chrome"
    for name in ("chromium", "google-chrome"):
        if (d := Path.home() / ".config" / name).is_dir():
            return d
    return Path.home() / ".config" / "chromium"


DEFAULT_PROFILE_DIR = _default_profile_dir()
STATE_FILE = Path.home() / ".cache" / "mcp-ozon" / "worker_tab.json"
WORKER_URL = "https://www.ozon.ru/"
WORKER_ORIGIN = "https://www.ozon.ru"
ALLOW_TIMEOUT_S = 60.0
CMD_TIMEOUT_S = 30.0
LOAD_TIMEOUT_S = 30.0
ALLOW_CLICKER = Path(__file__).resolve().parents[2] / "scripts" / "allow_debugging.applescript"


class CdpError(RuntimeError):
    pass


def _read_active_port(profile_dir: Path) -> tuple[str, str]:
    """Read <profile>/DevToolsActivePort -> (port, browser ws path)."""
    port_file = profile_dir / "DevToolsActivePort"
    try:
        port, path = port_file.read_text().splitlines()[:2]
    except (FileNotFoundError, ValueError) as exc:
        raise CdpError(
            f"Chrome remote debugging is off ({port_file} not found). Enable it "
            "at chrome://inspect/#remote-debugging or launch the browser with "
            "--remote-debugging-port, then retry."
        ) from exc
    return port.strip(), path.strip()


def discover_ws_url(profile_dir: Path = DEFAULT_PROFILE_DIR) -> str:
    """Read <profile>/DevToolsActivePort and build the browser websocket URL."""
    port, path = _read_active_port(profile_dir)
    return f"ws://127.0.0.1:{port}{path}"


def _http_json(port: str, path: str, method: str = "GET") -> Any:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _resolve_page_ws(port: str) -> str:
    """Find or create the ozon.ru worker tab via the /json HTTP endpoints and
    return its page-level websocket URL."""
    try:
        targets = _http_json(port, "/json/list")
    except OSError as exc:
        raise CdpError(f"DevTools HTTP endpoint unreachable: {exc}") from exc
    for t in targets:
        if t.get("type") == "page" and (t.get("url") or "").startswith(WORKER_ORIGIN):
            return t["webSocketDebuggerUrl"]
    try:
        page = _http_json(port, f"/json/new?{WORKER_URL}", method="PUT")
    except OSError:
        page = _http_json(port, f"/json/new?{WORKER_URL}")
    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        raise CdpError("/json/new returned no webSocketDebuggerUrl")
    return ws_url


class CdpSession:
    """Browser-level CDP connection plus one reusable background ozon.ru tab."""

    def __init__(self, ws_url: str | None = None, profile_dir: Path = DEFAULT_PROFILE_DIR) -> None:
        self._ws_url = ws_url
        self._profile_dir = profile_dir
        self._ws: Any = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._listeners: dict[str, list[Callable[[dict, str | None], None]]] = {}
        self._reader: asyncio.Task | None = None
        self._target_id: str | None = None
        self._session_id: str | None = None
        self._page_mode: bool | None = None

    async def connect(self) -> None:
        if self._page_mode is None:
            self._page_mode = self._probe_page_mode()
        if self._page_mode:
            await self._connect_page()
            return
        if self._ws_url is None:
            self._ws_url = discover_ws_url(self._profile_dir)
        self._session_id = None
        if self._target_id is None:
            self._target_id = self._load_persisted_target()
        try:
            async with asyncio.timeout(ALLOW_TIMEOUT_S):
                clicker = asyncio.create_task(self._auto_allow())
                try:
                    await self._open_ws()
                finally:
                    clicker.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await clicker
                await self.send("Browser.getVersion")
        except (TimeoutError, ConnectionClosed, OSError) as exc:
            await self.close()
            raise CdpError(
                "No answer from Chrome within 60s — the remote-debugging Allow prompt "
                "('Разрешить удаленную отладку?') is likely pending on a browser window. "
                "Click 'Разрешить' (on macOS: osascript "
                "scripts/allow_debugging.applescript), then retry. Remote debugging must "
                "stay enabled (chrome://inspect/#remote-debugging or "
                "--remote-debugging-port)."
            ) from exc

    def _probe_page_mode(self) -> bool:
        """Page-WS mode works when the DevTools HTTP endpoint serves /json/*
        (classic --remote-debugging-port). Inspect-mode endpoints (macOS Chrome
        auto-connect) don't expose HTTP, so they stay on the browser-WS flow
        with its per-connection Allow prompt."""
        if self._ws_url is not None:
            return False
        try:
            port = _read_active_port(self._profile_dir)[0]
            version = _http_json(port, "/json/version")
            targets = _http_json(port, "/json/list")
        except (CdpError, OSError, json.JSONDecodeError):
            return False
        return bool(version.get("Browser")) and isinstance(targets, list)

    async def _connect_page(self) -> None:
        port = _read_active_port(self._profile_dir)[0]
        self._ws_url = _resolve_page_ws(port)
        try:
            async with asyncio.timeout(ALLOW_TIMEOUT_S):
                await self._open_ws()
        except (TimeoutError, ConnectionClosed, OSError) as exc:
            await self.close()
            raise CdpError(f"Page-level CDP connect failed: {exc}") from exc
        await self._wait_loaded()

    async def _auto_allow(self) -> None:
        """Auto-click the macOS 'Разрешить удаленную отладку?' sheet while the
        browser-ws handshake is pending. A click can land on an orphan sheet
        left by a dead connection while a fresh one is still pending, so keep
        polling for the whole connect window instead of stopping after one."""
        if sys.platform != "darwin" or not ALLOW_CLICKER.exists():
            return
        with contextlib.suppress(asyncio.CancelledError):
            while True:
                proc = await asyncio.create_subprocess_exec(
                    "osascript",
                    str(ALLOW_CLICKER),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
                await asyncio.sleep(1.0)

    async def _open_ws(self) -> None:
        self._ws = await connect(self._ws_url, max_size=64 * 2**20, open_timeout=30)
        self._reader = asyncio.create_task(self._read_loop())

    async def _wait_loaded(self) -> None:
        async with asyncio.timeout(LOAD_TIMEOUT_S):
            # readyState is "complete" on about:blank before navigation commits,
            # so wait for the real URL too
            while await self._eval(
                "document.readyState + '|' + location.origin"
            ) != "complete|https://www.ozon.ru":
                await asyncio.sleep(0.5)

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None
        if self._ws:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None
        # keep _target_id: the tab may still exist and be reusable after reconnect
        self._session_id = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(CdpError("connection closed"))
        self._pending.clear()

    def on(self, method: str, cb: Callable[[dict, str | None], None]) -> None:
        """Subscribe to CDP events; cb(params, session_id)."""
        self._listeners.setdefault(method, []).append(cb)

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut is not None and not fut.done():
                        if "error" in msg:
                            fut.set_exception(CdpError(msg["error"].get("message", "CDP error")))
                        else:
                            fut.set_result(msg.get("result", {}))
                else:
                    for cb in self._listeners.get(msg.get("method", ""), ()):
                        cb(msg.get("params", {}), msg.get("sessionId"))
        except (ConnectionClosed, asyncio.CancelledError):
            pass
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(CdpError("connection closed"))
            self._pending.clear()

    async def send(
        self, method: str, params: dict | None = None, session_id: str | None = None
    ) -> dict:
        if self._ws is None:
            raise CdpError("not connected")
        self._next_id += 1
        msg_id = self._next_id
        fut = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        payload: dict[str, Any] = {"id": msg_id, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        await self._ws.send(json.dumps(payload))
        async with asyncio.timeout(CMD_TIMEOUT_S):
            return await fut

    def _load_persisted_target(self) -> str | None:
        """Target id saved by a previous server process; valid only for the same
        Chrome instance (matched by the browser ws id)."""
        try:
            data = json.loads(STATE_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not self._ws_url or data.get("browser") != self._ws_url.rpartition("/")[-1]:
            return None
        target_id = data.get("targetId")
        return target_id if isinstance(target_id, str) else None

    def _persist_target(self) -> None:
        if not self._ws_url or not self._target_id:
            return
        with contextlib.suppress(OSError):
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(
                json.dumps(
                    {
                        "browser": self._ws_url.rpartition("/")[-1],
                        "targetId": self._target_id,
                    }
                )
            )

    async def ensure_worker_tab(self) -> str | None:
        """Attach to our background ozon.ru tab, creating it if needed.
        Returns sessionId (browser-WS mode) or None (page-WS mode)."""
        if self._page_mode:
            try:
                if await self._eval("location.origin") == WORKER_ORIGIN:
                    return None
            except (CdpError, ConnectionClosed, TimeoutError):
                pass
            await self.close()
            await self._connect_page()
            return None
        # The inspect-mode endpoint blocks target enumeration, so probe instead.
        if self._session_id:
            try:
                if await self._eval("location.origin") == WORKER_ORIGIN:
                    return self._session_id
            except (CdpError, ConnectionClosed, TimeoutError):
                pass
            self._session_id = None
        if self._target_id:
            try:
                res = await self.send(
                    "Target.attachToTarget",
                    {"targetId": self._target_id, "flatten": True},
                )
                session_id = res["sessionId"]
                if await self._eval("location.origin", session_id) == WORKER_ORIGIN:
                    self._session_id = session_id
                    return session_id
                # our tab was navigated away — leave it alone, make a fresh one
                with contextlib.suppress(CdpError):
                    await self.send("Target.detachTarget", {"sessionId": session_id})
            except (CdpError, ConnectionClosed, TimeoutError):
                pass
            self._target_id = self._session_id = None

        res = await self.send(
            "Target.createTarget", {"url": WORKER_URL, "background": True}
        )
        self._target_id = res["targetId"]
        self._persist_target()
        res = await self.send(
            "Target.attachToTarget", {"targetId": self._target_id, "flatten": True}
        )
        self._session_id = res["sessionId"]
        await self.send("Runtime.enable", session_id=self._session_id)
        await self._wait_loaded()
        return self._session_id

    async def _eval(self, expression: str, session_id: str | None = None) -> Any:
        res = await self.send(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
            session_id=session_id or self._session_id,
        )
        if "exceptionDetails" in res:
            detail = res["exceptionDetails"]
            desc = (detail.get("exception") or {}).get("description") or detail.get("text")
            raise CdpError(f"JS exception: {desc}")
        return res.get("result", {}).get("value")

    async def evaluate(self, expression: str) -> Any:
        """Evaluate JS in the worker tab; auto-reconnect once if tab/connection died."""
        try:
            await self.ensure_worker_tab()
            return await self._eval(expression)
        except (CdpError, ConnectionClosed, TimeoutError):
            await self.close()
            await self.connect()
            await self.ensure_worker_tab()
            return await self._eval(expression)


async def _ping() -> None:
    session = CdpSession()
    await session.connect()
    try:
        print(await session.evaluate("location.href"))
        print(await session.evaluate("document.title"))
    finally:
        await session.close()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "ping":
        asyncio.run(_ping())
    else:
        print("usage: python -m mcp_ozon.cdp ping", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()

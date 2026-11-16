from pathlib import Path

import pytest

from mcp_ozon.cdp import CdpError, discover_ws_url


def test_discover_ws_url(tmp_path: Path) -> None:
    (tmp_path / "DevToolsActivePort").write_text("9222\n/devtools/browser/abc-123\n")
    assert discover_ws_url(tmp_path) == "ws://127.0.0.1:9222/devtools/browser/abc-123"


def test_discover_ws_url_missing(tmp_path: Path) -> None:
    with pytest.raises(CdpError, match="chrome://inspect"):
        discover_ws_url(tmp_path)


def test_discover_ws_url_malformed(tmp_path: Path) -> None:
    (tmp_path / "DevToolsActivePort").write_text("9222\n")
    with pytest.raises(CdpError, match="chrome://inspect"):
        discover_ws_url(tmp_path)

import json
from pathlib import Path

import pytest

from mcp_ozon.ozon import PageJson

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_page():
    def _load(name: str) -> PageJson:
        return PageJson.from_raw(json.loads((FIXTURES / f"{name}.json").read_text()))

    return _load

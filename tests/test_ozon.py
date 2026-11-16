import pytest

from mcp_ozon.ozon import OzonAuthError, OzonBlockedError, PageJson, _check_response


def test_page_json_decodes_widget_states() -> None:
    raw = {
        "widgetStates": {
            "tileGrid-123-default-1": '{"items": [{"sku": 1}]}',
            "searchResultsV2-456-default-1": {"items": []},
            "notJson-1": "plain string",
        },
        "nextPage": "/search/?page=2",
    }
    page = PageJson.from_raw(raw)
    assert page.widget_states["tileGrid-123-default-1"] == {"items": [{"sku": 1}]}
    assert page.widget_states["notJson-1"] == "plain string"
    assert page.next_page == "/search/?page=2"
    assert len(page.widgets("tileGrid")) == 1
    assert page.widgets("tileGrid")[0]["items"][0]["sku"] == 1
    assert page.widgets("missing") == []


def test_no_next_page() -> None:
    assert PageJson.from_raw({"widgetStates": {}}).next_page is None


def test_check_response_auth() -> None:
    with pytest.raises(OzonAuthError):
        _check_response(200, "https://www.ozon.ru/api/ozonid", "{}", "")
    with pytest.raises(OzonAuthError):
        _check_response(200, "https://www.ozon.ru/api/x", "{}", "__Secure-user-id=0; a=b")


def test_check_response_blocked() -> None:
    with pytest.raises(OzonBlockedError):
        _check_response(403, "https://www.ozon.ru/api/x", "forbidden", "")
    with pytest.raises(OzonBlockedError):
        _check_response(200, "https://www.ozon.ru/api/x", "Доступ ограничен", "")

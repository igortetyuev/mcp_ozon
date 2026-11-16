# mcp-ozon

MCP server (stdio, FastMCP) exposing Ozon buyer data through your own
logged-in Chrome session — no credentials, cookies or tokens are stored
anywhere.

It drives a background `ozon.ru` tab in your real Chrome over the DevTools
protocol and calls the site's internal JSON API (`page/json/v2`,
`composer-api`) via `fetch()` inside that tab. All requests inherit your
session, TLS fingerprint and anti-bot tokens, so there is no separate login
flow and nothing secret ever leaves your browser profile.

## Features

- `search_products` — full-text search with sorting, price range, facet
  filters (brand/attributes/…) and delivery-speed filters.
- `get_search_filters` — discover available facet keys/values for a query.
- `get_recommendations` — the personalized home feed.
- `get_product` — title, prices (regular / Ozon Card / old), rating, reviews,
  seller, availability.
- `get_delivery_options` — per-SKU delivery ETAs: express courier, earliest
  regular courier and pickup-point dates for your selected address.
- `get_favorites`, `get_cart` — favorites list, cart contents and totals.
- `list_orders`, `get_order` — order history with statuses, totals, items.
- `add_to_cart`, `add_to_favorites` — write actions (only on explicit
  request; the tool descriptions say so).

## Requirements

- macOS (developed/tested there; Linux/Windows CDP flow likely works but
  profile paths differ)
- Python ≥ 3.12, [uv](https://docs.astral.sh/uv/)
- Google Chrome logged in to ozon.ru
- Chrome remote debugging enabled once:
  open `chrome://inspect/#remote-debugging` and switch it on.

The first connection per run shows Chrome's *"Разрешить удаленную отладку?"*
Allow sheet — approve it (or auto-click via
`osascript scripts/allow_debugging.applescript`).

## Install

```bash
git clone <this repo> && cd mcp_ozon
uv sync
```

## MCP client config

```json
{
  "mcpServers": {
    "ozon": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp_ozon", "mcp-ozon"],
      "transport": "stdio"
    }
  }
}
```

## Development

```bash
uv run pytest                  # unit tests (live tests deselected)
uv run pytest -m live          # drives your real Chrome (~30s)
uv run ruff check
uv run python -m mcp_ozon.cdp ping       # CDP connectivity check
uv run python -m mcp_ozon.ozon '/cart'   # raw widget keys for a page
uv run python scripts/recon.py           # refresh test fixtures (raw→sanitized)
uv run python scripts/recon.py --resanitize
uv run python scripts/export.py          # full order/favorites history -> exports/
```

Fixtures: raw responses land in `tests/fixtures/raw/` (gitignored — contains
personal data); `scripts/recon.py` produces sanitized copies committed under
`tests/fixtures/`. `exports/` is gitignored too.

## Security notes

- The server never sees or stores your cookies/passwords — everything stays
  inside the Chrome profile.
- It does open a debug port bridge into your browser: only run it with MCP
  clients you trust, same as any remote-debugging setup.
- Write tools (`add_to_cart`, `add_to_favorites`) mutate your real account —
  they are documented to require an explicit request in the agent prompt.

## License

MIT — see [LICENSE](LICENSE).

# mcp-ozon

Stdio MCP server (FastMCP) for Ozon buyer data via the user's logged-in Chrome.
Architecture: Chrome remote debugging (chrome://inspect/#remote-debugging) →
CDP over raw websockets → one background ozon.ru worker tab → `fetch()` of the
internal JSON API inside that tab. No cookies/credentials stored.

## Commands

```bash
uv sync
uv run pytest                  # unit tests only (live deselected by default)
uv run pytest -m live          # drives real Chrome (~30s)
uv run ruff check
uv run mcp-ozon                # the MCP server (stdio)
uv run python -m mcp_ozon.cdp ping           # CDP connectivity check
uv run python -m mcp_ozon.ozon '/cart'       # raw page-JSON widget keys
uv run python scripts/recon.py               # capture fixtures (raw+sanitized)
uv run python scripts/recon.py name=/path    # extra pages
uv run python scripts/recon.py --resanitize  # rebuild sanitized fixtures from raw/
uv run python scripts/recon.py --map         # widget-key map of raw fixtures
uv run python scripts/recon.py --record-actions 120
uv run python scripts/export.py             # full history dump -> exports/*.json
```

Fixtures: raw in `tests/fixtures/raw/` (gitignored, personal data), sanitized in
`tests/fixtures/` (committed). Sanitizer masks names/phones/addresses/order and
shipment ids/url_sign tokens and drops identity widgets (profileLogo,
userAvatar, profileMenu, accountList, addressBookBarWeb, orderInfo).

## Chrome/CDP facts — two transport modes

`CdpSession` picks the mode per host (`_probe_page_mode`):

- **page-WS mode** — when `--remote-debugging-port` is set and the DevTools
  HTTP endpoint serves `/json/*` (Linux deployment on `box`). Worker tab is
  found/created via `GET /json/list` / `PUT /json/new`, and the ws connects to
  its `webSocketDebuggerUrl` directly — no browser-level ws, so **no Allow
  prompt at all**. No Target.attach flow; commands go without sessionId.
- **browser-WS mode** — inspect-mode auto-connect endpoint (macOS Chrome
  `chrome://inspect/#remote-debugging`), which does not serve `/json`. The
  original flow below, with the per-connection Allow prompt.

Profile dir: `MCP_OZON_PROFILE_DIR` env override → macOS
`~/Library/Application Support/Google/Chrome` → Linux first existing of
`~/.config/chromium`, `~/.config/google-chrome`.

Deployment on box: `/srv/concierge/mcp/mcp_ozon` (rsync + `uv sync`),
registered in concierge `mcp_servers` as local stdio. Chromium there runs
with flags from `/etc/chromium/default`: `--remote-debugging-port=0
--remote-allow-origins=* --load-extension=/srv/concierge/chrome-mac-fingerprint
--enable-unsafe-swiftshader --use-angle=swiftshader`. The unpacked extension
spoofs the macOS fingerprint (navigator.*/userAgentData/webdriver + WebGL
vendor/renderer; UA header via declarativeNetRequest) — required by Ozon
antibot. Do NOT launch chromium via setsid/ssh — GPU process spawn fails
with FATAL; launch from the desktop session or `systemd-run --user --scope`.

## Chrome/CDP facts (Chrome 157, inspect-mode auto-connect)

- `~/Library/Application Support/Google/Chrome/DevToolsActivePort`:
  line1 port, line2 `/devtools/browser/<id>` → `ws://127.0.0.1:<port><path>`.
- Endpoint allows `Browser.getVersion`, `Target.createTarget`,
  `Target.attachToTarget(flatten)`, `Target.closeTarget` — but NOT
  `Target.getVersion`/`getTargets`/`getTargetInfos` (enumeration blocked).
  Worker tab reuse therefore probes `location.origin` / re-attaches by id.
- The worker tab's targetId is persisted to `~/.cache/mcp-ozon/worker_tab.json`
  (keyed by browser ws id) so it survives server restarts and reconnects.
  Stale after Chrome restart — attach fails → fresh tab; if the user navigated
  the tab away, origin check fails → fresh tab (old one left alone).
- Chrome shows an **Allow prompt per new websocket connection** (no persist).
  Pending prompt stalls the WS handshake — a connect/command timeout or a
  CdpError "No answer from Chrome" means the "Разрешить удаленную отладку?"
  sheet is up on some Chrome window. **Handle it yourself, don't ask the
  user**: click "Разрешить" via `osascript scripts/allow_debugging.applescript`
  or the macos-automator MCP (UI-scripting click on the button), then retry
  the failed call. Only escalate to the user if the button isn't found or the
  click doesn't take.
- New tab `document.readyState` is `complete` on about:blank before navigation —
  wait for `location.origin == https://www.ozon.ru` too (done in
  `cdp.ensure_worker_tab`).

## Ozon API map (as of 2026-11-16)

Page JSON: `GET /api/entrypoint-api.bx/page/json/v2?url=<enc(path)>`
→ `{widgetStates: {<widget>-<id>-<variant>-<n>: "<json string>"}, layout, ...}`.
All values are JSON **strings** — decode each (see `PageJson.from_raw`).

| Page | Widget | Fields used |
|---|---|---|
| search/favorites/home | `tileGridDesktop-*` | `items[]`: `sku`, `action.link` (product URL), `mainState[]` atoms — `priceV2.price[]{text,textStyle: PRICE|ORIGINAL_PRICE}` + `priceStyle.styleType` (`CARD_PRICE` ⇒ shown price is Ozon-card), `textDS` `id:"name"` = title, `labelListV2` w/ star icon = rating + "N отзывов"; `multiButton.ozonButton.addToCart.actionButton.title` = delivery ETA ("Завтра", "20 ноября") |
| search | `searchResultsSort-*` | `sortButton.options[].action.link` ⇒ `sorting=` param: `score,new,price,price_desc,rating,discount` |
| search | `filtersDesktop-*` | `sections[].filters[]{key,type}`; `checkboxesFilter.sections[].items[]{key,title.text}`; apply via URL `&<key>=v1,v2` (comma or repeated params both work); `currency_price=lo.000;hi.000`; `delivery` radio keys: `0` any,`1` today,`2` tomorrow,`4` ≤3d,`8` ≤7d,`13` ≤12d |
| search | `searchResultsFiltersActive-*` | `activeFilters[]` = applied filters confirmation |
| all list pages | `paginator-*`, `infiniteVirtualPaginator-*` | `nextPage` = full next link w/ tokens — **follow it** (don't construct `page=N`); several paginators/page → prefer link without `recom` |
| /cart | `cartSplit-*` | `header.title.text` ("Доступны для заказа" vs unavailable), `cartItems[].product{id,priceColumn,titleColumn}`, `controls.quantity.current` |
| /cart | `total-*` | `summary.footer.price` (итого), `summary.header.info` ("N товара") |
| /my/orderlist | `orderList-*` | `ordersV2[]`: `common.action.link` → `?order=<NNNNNNNN-NNNN>`, `leftBlock.textIcon.text.text` = status ("Получен 15 ноября", "В службе доставки") — same order number repeats per posting |
| /my/orderdetails | `shipmentWidget-*` | `header[].textIcon.text` = status; `items[].sellers[].products[]{title.name.text, title.common.action.link→/product/<sku>, price.price[0].text}` |
| /my/orderdetails | `orderDoneTotal-*` | `total.right.price.text` = сумма |
| /product | `webPrice-*`, `webSingleProductScore-*`, `webProductHeading-*`, `webCurrentSeller-*`, `webDetailSKU-*` | `price`,`cardPrice`,`isAvailable`; `"4.9 • N отзывов"`; `title`; `sellerCell` |
| /product | `webDelivery` (lazy) | in `layout`: `{component:"webDelivery",stateId,asyncData}` → `POST /api/composer-api.bx/widget/json/v2?widgetStateId=<stateId>` body `{"asyncData":...}` → `state.sections[]`: `addressSelect` + options `descriptionRs[].content` (name line + ETA line), `priceBadge.text` ("Входит в заказ" = free) |
| /my/orderdetails | `titleWithTimer-*` | `title.text` = "Заказ от 17 октября" (same-year) or "Заказ от 03.12.2025"/"15.09.2014" (older, dd.mm.yyyy) = placed date |
| /my/orderdetails | `orderDoneTotal.prices.elements[]` | `left.title.text` rows: "Ваш заказ"→"№ NNN-NNN • N товаров" (canonical number+count), "Товары"→items subtotal, "Скидка"/"Скидки и доставка"→negative delta; also "Оплата ВАУ-баллами"/"С Баланса средств" payment rows |
| /my/orderdetails | `shipmentWidget-*` (per shipment) | `shipmentId`; multiple per order; product fields add `itemId`, `isFavorite`, `attributes[]`; no purchased quantity field (multiples come as duplicate lines) |
| /my/orderlist | `tabsSelector-*` | tabs: `?selectedTab=active` (default, active+recent) and `?selectedTab=archive` (full history, ~13/page) — archive is REQUIRED for all-time export |
| /my/orderlist | `ordersV2[]` entries | per-POSTING, not per-order: same `?order=N` repeats across postings; `leftBlock.title/subtitle` swap roles by status (in-progress: title=delivery type, subtitle=ETA "Ожидаем D – D месяца"; finished: subtitle=delivery type); `textIcon.timeline.steps[]` = tracking w/ "dd.mm.yyyy HH:MM" |

## Pagination & export

- Every list page has TWO paginators: the real list's and a recommendations one
  (link contains `recom`: `layout_container=recommendations|recoms_pagination_*`).
  `PageJson.next_page` falls back to the recom paginator when the real one is
  exhausted — for full-history walks pick ONLY non-recom links and STOP when
  none (`order-list-desktop` for orderlist, `default` for favorites).
- Bulk export: `uv run python scripts/export.py` → `exports/*.json` (gitignored):
  ozon_orders / ozon_shipments / ozon_order_items / ozon_favorites + meta.
  Handles the Chrome allow-prompt itself (osascript clicker loop).
- Prices can be decimal ("385,50 ₽", "385.50 ₽") → `parse_price` returns
  float when a 1-2 digit fraction is present. "ВАУ-баллы"/points rows are text.
- Dates in history ("Получен 15 ноября", "Заказ от ...") roll BACK a year, not
  forward — `parse_ru_date` is future-ETA oriented; export.py has `past_ru_date`.

## Chrome/CDP — zero-window gotcha

- The "Разрешить удаленную отладку?" sheet can only attach to an EXISTING Chrome
  window. With 0 windows (Chrome running in background) every new ws handshake
  stalls with NO visible prompt — `count windows` via System Events = 0.
  Fix: `osascript -e 'tell app "Google Chrome" to make new window'` then connect.

Actions (write): `POST /api/composer-api.bx/_action/v2/<name>`
`{"Content-Type":"application/json"}` —
- `addToCart` `{"items":[{"id":<int sku>,"quantity":N}]}` → 200 `{data:{cart:{cartItems}}}`
- `favoriteBatchAddItems` `{"skus":[<int sku>]}` → 200 (string sku or `items` → rpc 400)
- `favoriteBatchDeleteItems` — same shape, not implemented (out of scope)

Search URL: `/search/?text=<q>&from_global=true` (`from_global` keeps it global;
without it Ozon may redirect into a predicted `/category/...` URL — still works).

Status→group: `получен`→received, `возврат/возвращ`→returned,
`отмен`→cancelled, else→in_progress.

## Gotchas

- Output `price` = effective price: paid price for order items, Ozon-card
  price (`CARD_PRICE`/`cardPrice`) when present, else the listed price. The
  inflated listed price ("без Ozon Карты", crossed-out) goes to
  `original_price`; `card_price` set when the effective price is a card price.
- `webPricePerStars` also starts with "webPrice" — match `webPrice-` with dash.
- Price strings use NBSP/thin spaces — `parse.parse_price` strips non-digits.
- `parse_ru_date`: "сегодня/завтра/послезавтра", "N месяца" (year rolls over
  NYE), "за N час(а|ов)"→datetime, "с H:MM до H:MM"→datetime at window end.
- Requests are serialized with ≥0.5s pause (`ozon._fetch`); anti-bot →
  `OzonBlockedError` ("пройдите проверку в Chrome"); not-logged-in →
  `OzonAuthError`.
- No Express flag in search tiles; `express` set only when the delivery label
  mentions экспресс/часы. PDP delivery widget is the source of truth.

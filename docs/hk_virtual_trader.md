# Hong Kong Virtual Trader

The HK trader extends the existing live Virtual Trader. It does not use a
second decision engine. Every persisted security is identified by `market +
ticker`; Yahoo's provider symbol is derived only at the market-data boundary.

## Identity and API usage

HK input accepts one to four digits, with an optional `.HK` suffix. Examples:

- `5` -> stored ticker `0005`, provider symbol `0005.HK`
- `700`, `0700`, `0700.HK` -> stored ticker `0700`, provider symbol `0700.HK`
- `9988` -> stored ticker `9988`, provider symbol `9988.HK`

Market-aware endpoints retain `market=US` as their backward-compatible
default. For example:

```text
GET /market-data/live-snapshot?market=HK&ticker=0700
GET /chart-data?market=HK&ticker=0700&period=1y
GET /virtual-trader/live-status?market=HK&user_id=demo-user
POST /virtual-trader/run-now {"user_id":"demo-user","market":"HK","tickers":["0700"]}
GET /model-lifecycle/registry?market=HK&ticker=0700&target_name=target_5d_return
GET /model-lifecycle/feedback?market=HK&ticker=0700&model_period=2y&model_name=linear_regression
```

## Persistence and migration

Startup migration adds `market` (default `US`) to the account ledger, live
decision log, and model-feedback tables. Existing ledger rows therefore remain
US data. A new `live_trader_positions_market` table is populated from legacy US
positions, and a new `market_model_registry` is populated from the legacy US
registry. The original tables are retained for rollback compatibility.

HK artifacts are stored below `data/models/HK/<ticker>/...`. Existing US paths
remain `data/models/<ticker>/...`, so deployment does not move or retrain US
models.

## Training and feedback

If an activated HK ticker has no compatible artifact, one background training
job is queued for that exact `HK + ticker + period`. Duplicate jobs are
suppressed. The trained results use the same model families, feature pipeline,
walk-forward validation, lifecycle gates, and promotion scoring as US models.
No model from another HK ticker or from the US market is accepted as an HK
fallback.

An eligible HK model decision is recorded with `market=HK`. It matures only
when five later rows exist in that ticker's Yahoo daily history. Because rows
represent actual HK trading sessions, weekends and exchange holidays do not
advance the horizon. Feedback summaries and context adjustments filter by both
market and ticker.

## HK account and trading rules

- The HK account holds HKD cash and is separate from the USD account.
- The UI displays `HK$`; US continues to display `$`.
- Normal HK orders require a known security-specific board lot and a quantity
  divisible by it. Board lots are loaded from HKEX's official **Full List of
  Securities** workbook rather than a manually maintained ticker map. The
  workbook also supplies the security name, category, sub-category, CCASS
  admission flag, trading currency, and expiry date where present.
- The normalized cache uses SQLite table `hkex_securities` plus refresh state
  table `hkex_metadata_state`. By default its file is
  `hkex_security_metadata.db` beside `PROFILE_DB_PATH`; set
  `HKEX_METADATA_DB_PATH` only when a different location is required.
- The first HK metadata lookup populates the cache. A successful cache is
  refreshed at most once every 24 hours (`HKEX_METADATA_REFRESH_HOURS`). A
  temporary failure is logged and retried no more than once per hour while the
  most recent valid cache continues to serve trades. A failed or malformed
  refresh never clears the valid rows and no board-lot value is fabricated.
- Only HK buys require board-lot metadata. Automated buys are rounded down to
  a complete lot; a buy is blocked when the ticker is invalid or HKEX has no
  reliable board lot after the cache/source check.
- The shared trader rounds automated buy and partial-sell quantities down to a
  complete board lot.
- Market orders remain the current execution type. The isolated HK spread
  validator contains the HKEX Phase 2 minimum-spread bands effective 3 August
  2026 for future limit-order support.
- HK market-hours helpers include the 09:30-12:00 and 13:00-16:00 Asia/Hong_Kong
  sessions. Feedback maturity relies on observed candles, not a hand-maintained
  holiday list.

## NanoPi deployment

No new Python dependency or required environment variable is needed. The XLSX
reader uses the Python standard library and the existing `requests` package.

```bash
cd /srv/stock-assistant-PiPi
git pull --ff-only
/srv/stock-assistant-PiPi/.venv/bin/pip install -r requirements.txt
cd /srv/stock-assistant-PiPi/frontend
npm ci
npm run build
sudo systemctl restart stock-assistant-api
sudo systemctl status stock-assistant-api --no-pager -l
curl -sS "http://127.0.0.1:8000/market-data/hk-security-metadata?ticker=1810" | python3 -m json.tool
curl -sS "http://127.0.0.1:8000/market-data/hkex-metadata-status" | python3 -m json.tool
```

The metadata database and tables are created automatically on the first status
or lookup request. Back up the production profile database before deployment as
normal; do not delete `data/user_profiles.db`, the HKEX cache, or the existing
model directory.

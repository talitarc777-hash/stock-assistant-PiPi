# Dashboard score ranking

The Dashboard's HK ranking uses the user's effective `hk_watchlist` as its
active universe. It calculates the existing deterministic technical-indicator
score for every active ticker and stores the latest successful result in the
same SQLite database configured by `PROFILE_DB_PATH`.

The cache tables are created automatically:

- `dashboard_ticker_scores`: latest successful score per user, market, ticker,
  and period;
- `dashboard_score_refresh_runs`: the expected/scored/failed ticker set and
  timestamps for each refresh.

Rows missing from the cache, including newly activated tickers, trigger a
refresh. Complete results refresh after 30 minutes. A failed refresh retains
the last successful row and exposes the current failure in diagnostics.
Validated exact-ticker or market `GLOBAL` model identity is recorded for
diagnostics only (`model_applied_to_score` is `false`). Model validation does
not filter the technical-score list.

Endpoints:

- `POST /dashboard/score-ranking/refresh`
- `GET /dashboard/score-ranking/raw`
- `GET /dashboard/top-scores`
- `GET /dashboard/score-ranking/diagnostics`

HK asset type is obtained from the cached official HKEX Full List of Securities
metadata, rather than a manually maintained ticker list. The score formula is
the shared `score_from_indicators` implementation used by the existing US
Dashboard analysis.

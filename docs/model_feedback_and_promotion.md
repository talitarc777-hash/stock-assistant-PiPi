# Model feedback and promotion

The Virtual Trader now keeps an auditable outcome loop for its model predictions.

## Feedback flow

1. One prediction is stored per ticker, model, model version, period, and market date.
2. The stored snapshot includes the model prediction, action, confidence, price,
   context score, news/social/analyst/regulatory factors, valuation, and risk data.
3. After five later trading sessions are available, the lifecycle scheduler records:
   - actual ticker return
   - benchmark return
   - direction correctness
   - estimated strategy return after the HKD 50 entry and exit fees
   - benchmark excess return
4. The observation receives a bounded outcome score from direction accuracy,
   profitability after cost, net return, and benchmark-relative return.

The five-day settlement queue processes least-recently-attempted predictions,
then oldest dates first. Missing provider history must not monopolize its batch.
Production, historical validated-candidate/compatible-saved-model records, and
non-executing challenger/paired-incumbent predictions contribute to model
evaluation; rule-based fallback decisions are excluded. Shadow rows never execute
an order or influence learned context twice. GLOBAL model decisions retain both the traded ticker and the GLOBAL
model origin so their forward evidence is attributed to the correct registry row.

Repeated five-minute scheduler runs do not create repeated feedback for the same
model and trading date.

The five-day result remains the governed promotion target. The same immutable
prediction is also observed after 1, 5, 10, and 20 later market rows for diagnostic
direction accuracy, return error, signal decay, maximum favourable excursion, and
maximum adverse excursion. These extra outcomes do not retrospectively retrain or
change the prediction that was stored.

## Immutable incumbent/challenger versions

Scheduled and lazy lifecycle training writes a unique artifact directory under
`data/models/.../versions/<model_version>/`. It no longer overwrites the canonical
runtime artifact before validation. Additive SQLite tables store each version, the
single active pointer for each market/ticker/period/target, and promotion/rollback
events. Existing production artifacts are snapshotted when first adopted by this
versioned registry.

Only the `ACTIVE` incumbent controls the Virtual Trader. At most one validated
`SHADOW`/`ELIGIBLE` challenger is additionally evaluated for a ticker on each live
analysis. A dated rotation samples up to four distinct families/windows, keeping
the oldest unresolved version of each family instead of replacing it whenever a
new version is trained. Its incumbent is inferred on the same input for comparison
even when another period is selected for execution. Both predictions keep exact ticker, model family, period, version, and
training-end attribution. This breaks the circular dependency in which a challenger
needed production traffic before it could collect production evidence.

## Promotion rules

Purged expanding-window validation remains the main promotion evidence. For the
five-day trading target, each fold leaves a five-row gap between its training and
test windows so a training label cannot use a future price from the test window.
Legacy validation flags are not accepted as current promotion evidence.
Existing scheme-4 artifacts that contain purged walk-forward evaluation rows
are re-evaluated through gate 9 at startup, so a sound incumbent is not removed
merely because new challengers use scheme 5. A stored validation flag without
the required evidence remains ineligible.
Five-day return regressors must also declare the current scale-independent
feature schema. Raw price-level regressors are legacy evidence because price
trends can produce unstable extrapolation even when directional results appear
plausible.
Regression candidates also calibrate an abstention threshold on an inner,
time-ordered holdout. The threshold is selected from a small prediction-size
grid using balanced directional accuracy, signed return, and useful coverage;
the outer test fold is never used for calibration. Predictions smaller than that known-in-advance uncertainty
are treated as `no_action`, rather than being counted as trades after the fact.
The evaluation then applies the same fixed market-regime policy used by the live
trader. Caution regimes use half-size exposure; stress regimes block new
positions. Every regime input comes from the prediction date, never its outcome.
For a schema-marked pooled GLOBAL model, live inference applies the same
scale-independent feature transformation used during training. Legacy global
models retain their original raw representation for backward compatibility.

- Before the minimum live sample count, feedback cannot change promotion score.
- After the minimum sample count, live feedback receives at most 35% weight.
- Weak live feedback can trigger retraining.
- Runtime model candidates are ranked using the same blended score.
- A candidate must still meet the minimum production score before promotion.
- Direction accuracy must beat the period's naive majority direction, remain
  stable across folds, and retain its edge across non-overlapping five-day paths.
- Validation gate version 9 also requires at least 55% balanced direction
  accuracy and 20% recall for the harder class. This prevents a rare-event
  target from looking strong merely because the model usually predicts the
  common outcome.
- Simulated returns must remain positive after configured execution costs across
  enough non-overlapping paths without breaching the drawdown limit.

Forward promotion additionally requires the challenger and incumbent to have the
configured absolute minimum, adequate effective sample size, and adequate time
coverage. It requires positive after-cost return, historical-validation
non-inferiority, and either statistically separated direction intervals or a
material composite improvement. Inconclusive ties retain the incumbent. The
minimum sample threshold was not reduced.

In addition, replacement now requires explicit matched forward evidence:
same ticker, decision date, outcome date, benchmark, horizon, price and realized
return. Predictions recorded after their outcome became known are excluded.
Duplicate observations are removed, same-day securities are averaged together,
and overlapping outcome windows are excluded. At least 20 non-overlapping date
blocks are required; the paired 95% Student-t lower bounds for both direction
improvement and long/cash net-return improvement must be positive. The candidate's
long/cash return must also be positive. This is a conservative diagnostic, not a
sequential multiple-testing guarantee or portfolio-profit estimate.

The comparison applies the same cost to both predictions: at least 0.10 percentage
points or the higher recorded transaction-cost estimate. A prediction above
`max(1%, cost)` represents long exposure; otherwise it represents cash. It does
not mistake a bearish forecast for an executable short position. Existing
historical, calibration and forward gates remain in force. Twenty independent
blocks may take months to collect, especially with rotation; five-day maturity
does not mean a model changes every five days.

After promotion, the previous incumbent stays available as a bounded rollback
candidate. It continues receiving shadow outcomes during probation. Automatic
rollback requires both a large feedback-score deterioration and non-overlapping
direction intervals plus worse after-cost return. Rollback also requires matched superiority of the prior
model on observations recorded since the new deployment's probation began.
Retirement for inferiority likewise requires matched evidence. Artifact publication clears the
saved-model scan cache, and Virtual Trader records whether it used the exact version
referenced by the active pointer.

Each scheduled US and HK workflow trains per-ticker challengers plus a pooled
`GLOBAL` challenger over several securities. The pooled model uses only
scale-independent features and must pass both the normal walk-forward gates and
the per-security pass-rate gate. It can provide validated coverage while a
ticker-specific model is still collecting enough evidence; it never borrows one
issuer's fitted model for another issuer.
The HK workflow always includes the centrally configured diversified HK starter
universe and then adds all persisted HK watchlist symbols. This prevents a
single-symbol profile from producing a one-stock-only HK model pipeline.

Successful provider downloads are also cached under
`MARKET_HISTORY_CACHE_DIR` (or `market_history_cache` beside the persistent
profile database). A temporary yfinance failure uses the most recent valid
history without overwriting it. Live trade freshness checks remain in force.

## Broad contextual reasoning

The saved context includes price/technical state, news and public sentiment,
analyst and earnings tone, regulatory context, valuation, company size,
volatility, and benchmark strength.

For context factors seen at least three times, the system measures their later
five-day returns. Matching factors may adjust future context scores, but the
combined adjustment is capped. This lets context improve gradually without
allowing noisy text or one unusual event to control a trade.

### Prospective external-context archive

`CONTEXT_ARCHIVE_ENABLED=true` (default outside tests) records allowlisted numeric
features and source/missingness metadata in `external_context_observations` in the
configured `PROFILE_DB_PATH`. The schema records source-observation time and actual
availability time separately, a schema version and content hash. It does not retain
article bodies or provider exception text. Unavailable feeds' default counts are
missing, not evidence of zero attention.

The existing lifecycle scheduler rotates the US universe and marked HK symbols
plus research cohorts independently of BUY signals, with a durable
`context_collection_cursor`. Live context collection also archives its observations.
The same snapshot is idempotent; corrections become available only when received.
`ContextObservationStore.as_of` rejects future availability and uses a 24-hour
freshness limit. Older snapshots remain stored for historical joins, not live reuse.
This is a prospective aggregate store, not a complete historical news/event store.

Only the prospective research trainer consumes these features; production
models do not. The existing optional Yahoo,
Reddit, Alpha Vantage and SEC adapters determine actual availability; credentials,
coverage and rate limits still apply. Current Yahoo headline tone is an experimental
lexicon aggregate, not a validated bilingual sentiment model. No historical news
is fabricated. `GET /model-lifecycle/context-coverage` reports counts per feature,
so empty snapshots cannot be mistaken for usable news evidence.

### Live monitoring and retraining

Repair triggers now inspect deployed versions' matured forward outcomes, not static
backtest reports. At least 60 distinct market dates are required. The most recent
30 dates are compared with the preceding 30–60 dates, weighting market dates
equally. A deterioration alarm requires both a direction-accuracy drop exceeding
10 percentage points and MAE exceeding `max(0.5 percentage points, 1.5 * prior MAE)`.
These are monitoring heuristics, not validated alpha or promotion thresholds.

Existing feature-distribution checks can also raise alarms once there is sufficient
live coverage. Successful repair consumes a version/outcome-dated alarm; the same
evidence does not repeatedly trigger work. Repairs have a 24-hour per-market
cooldown. Daily/weekly/monthly training remains scheduled separately. Automatic
lifecycle, pooled and lazy-activation fits now share a durable input fingerprint
guard: identical labeled inputs are skipped; changed inputs need five new matured
dates beyond the last successful fit. Manual training functions remain available
without that guard for explicitly requested research. Missing evidence means wait,
not automatic retraining or promotion.

## Automatic prospective price/context experiment

`PROSPECTIVE_RESEARCH_ENABLED=true` by default outside tests. An explicit existing
`false` in the service environment is respected and shown as a website blocker.
Nothing is deployed by editing this repository. Collection requires `CONTEXT_ARCHIVE_ENABLED=true`,
`EXTERNAL_CONTEXT_ENABLED=true`, `MODEL_FEEDBACK_ENABLED=true`, the five-session
feedback horizon, and a running lifecycle scheduler. No new service is required.

The frozen `pit-context-ridge-v1` protocol lives in
`app/services/prospective_model_research.py`. Its code/library/parameter hashes
are persisted on first registration. Changed contracts automatically create a new
hash-suffixed generation and retire incompatible in-flight rounds; old evidence is
preserved and never rewritten or reused under a different contract. A mutation
inside an already constructed service still fails closed. Normal data-driven
rounds keep the same contract and do not require a manual experiment start.

- Fixed US cohort: AAPL, MSFT, JPM, XOM; benchmark VOO.
- Fixed HK cohort: 0005, 0700, 1810, 9988; benchmark 2800.
- Capture after 17:00 market-local time on weekdays, only when the provider has
  the current day's completed close. No stale-bar or historical context backfill.
- Use the canonical stationary technical features, immutable as-of external
  snapshots and canonical five-session feedback settlement.
- Wait for 120 distinct matured dates, at least 90 dates for every cohort ticker,
  and an eligible external feature. Feature admission requires 80% overall and
  50% per-ticker coverage plus at least three distinct values. Admit at most two
  features in fixed priority order: news tone, analyst consensus, public mentions.
  This selection uses coverage, not future outcomes.
- Fit the same canonical ridge regressor (alpha 10) twice on identical rows and
  labels: PRICE ONLY and PRICE + CONTEXT. Imputation/scaling remain fold-local.
  Each market is trained separately. Missingness is not converted into fake zero
  news/attention; the existing training imputer handles admitted sparse values.
- Target is raw close percentage return after five later market rows, matching
  current live feedback. It is not dividend-adjusted total return. This research
  contract does not change the adjusted-close historical production target.
- Freeze both models for 120 future prediction dates, at least 90 paired dates per
  ticker. Wait until all recorded outcomes settle before releasing final results.
  Descriptive checkpoints every 20 matured dates are visible but never enter
  fitting or promotion. This is a monitored fixed-model test, not a blind holdout.
  Do not tune models or claim early success based on repeated interim looks.
  Incomplete matching/coverage produces an inconclusive report, not fake success.
- Compare with cash, always-long, benchmark and positive-20-day-momentum policies.
  The diagnostic buy threshold is greater than 1%; fixed round-trip costs are
  0.25 percentage points US and 0.75 HK. These are predeclared research assumptions,
  not actual broker costs or the account's fixed-fee/board-lot execution model.
- Report direction/balanced accuracy, return MAE, mean net return per opportunity,
  nonoverlapping path diagnostics and the existing paired confidence intervals.
  Brier score remains N/A because these regressors do not issue probabilities.
  The 120-date window is chosen to allow roughly 20 nonoverlapping five-session
  blocks; missing pairs can still leave insufficient evidence. Positive confidence
  interval bounds are also required. No promotion standard is lowered to pass.
- Once a round is released, changed evidence can start the next round on the most
  recent 120 matured dates using the unchanged protocol. No automatic redesign.

Every experimental model is `research_shadow`, with an explicit experimental-only
flag. Registration cannot update the production family registry; activation and
rollback reject it, and runtime selection excludes it. Its feedback is settled by
the canonical service but excluded from production calibration and generic
feedback listings. The dedicated status endpoint separates interim diagnostics
from final results after the locked window. Database administrators can still inspect raw data: this is a
research discipline safeguard, not an access-control boundary.

Do not interpret research returns as executable portfolio profit: inputs are
captured after the close, while the diagnostic label starts at that close. Before
any trading adoption, independently validate next-tradable-price execution,
corporate actions, actual fees/lot sizes, portfolio risk and runtime feature parity.
The new experiment deliberately cannot promote itself into the Virtual Trader.

Additive tables in the configured `PROFILE_DB_PATH` (default
`data/user_profiles.db`): `model_training_evidence`, `research_protocols`,
`research_feature_snapshots`, `research_model_rounds`. No destructive migration or
record replacement. Existing `external_context_observations`, `model_versions`
and `model_decision_feedback` remain canonical. Models use the existing immutable
artifact store under `RESEARCH_MODELS_DIR`.

The training guard fingerprints actual labeled feature values, labels, schema,
configuration, code and library versions. It uses a six-hour durable lease,
one-hour failure backoff and recovers completed-but-unregistered artifacts
without refitting. Five new dates are an evidence-admission heuristic, not proof
that the next model will improve. Missing artifacts can be repaired.

Read-only inspection after a separately approved deployment:

```bash
curl -sS http://127.0.0.1:8000/model-lifecycle/research-status
curl -sS http://127.0.0.1:8000/model-lifecycle/context-coverage
python scripts/audit_prospective_research.py --db data/user_profiles.db
```

The audit script opens SQLite read-only, without creating/migrating tables. Its
source-availability counts are not guarantees of vendor uptime or sentiment
quality. Empty history means there is no prospective accuracy result yet.

## Trading decision layer

### Continuous-learning operations

FastAPI lifespan starts the existing lifecycle scheduler automatically; it runs
immediately and then every 15 minutes after each completed pass. Use one Uvicorn
worker (the supplied NanoPi service does). No additional cron or manual experiment
command is required. Long training/provider calls can delay a pass; the website
reports heartbeats older than two hours rather than silently calling them healthy.

The general collector claims an hourly batch per market with a durable cursor.
Batch size is `min(CONTEXT_COLLECTION_BATCH_MAX, max(2, ceil(universe_size/24)))`;
default ceiling is eight. A 120-ticker universe therefore gets five attempts/hour,
roughly one rotation/day if the process and providers remain available. A larger
universe may take longer. The four-ticker research cohort in each market also gets
priority after 17:00 local time. Research training dates accrue at most once per
completed market day, not once per poll or ticker. General rotation is not a promise
of a complete daily panel for every ticker.

Collection leases survive restarts. Empty/failing requests back off from 15 minutes
up to six hours, successful requests have a one-hour interval, and the one-hour
context cache can be reused from SQLite after restart. Frozen price snapshots retry
at most hourly. General and research collection share symbol admission, while live
trading shares the provider cache. Cached data does not receive a fabricated new
availability timestamp. Global Alpha Vantage requests have a persistent daily
budget (20 by default); only the latest completed-quarter transcript is tried,
not six quarters on every request. Actual vendor entitlement/rate limits still apply.

Yahoo news/analyst APIs use normalized US or `.HK` symbols. Returned entity symbols
are checked when available; news uses associated tickers or verified company names.
Bare HK numbers and short unqualified US symbols are rejected in free-text social
matching. SEC uses its official CIK mapping and requires a real contact User-Agent;
the placeholder in `.env.example` does not enable it. No HK SEC/Alpha Vantage
symbol mapping is fabricated. Reddit availability is not guaranteed. There is no
new dedicated HK regulatory, earnings-transcript or macroeconomic adapter.

The existing 120 + 120 date thresholds stay unchanged. Interim paired comparisons
are descriptive checkpoints, not statistically valid repeated success tests. There
is no automatic context-model promotion even after the final evidence gates pass.
Training failures retry every six hours but retire after seven days. Forward rounds
expire after 365 calendar days if unfinished. Retiring preserves artifacts and
labels. A replacement round needs changed inputs and at least five new matured dates;
the same failed dataset is not fitted forever. Software-contract changes use a new
generation rather than rewriting old snapshots.

`GET /model-lifecycle/learning-health` powers the Model Lifecycle operations panel.
It contains no credentials, contact addresses, raw provider exceptions or database
paths. Collection/job health, source availability and prediction quality are separate
concepts. Lifetime archive counts and last-10,000-snapshot quality statistics are
explicitly distinguished. The panel auto-refreshes every minute while visible and
does not start work on refresh. Optional provider failure is displayed even when
other data sources are usable.

Read-only service check, from the real NanoPi project directory:

```bash
curl -sS http://127.0.0.1:8000/model-lifecycle/learning-health
.venv/bin/python scripts/check_learning_operations.py
# Two small Yahoo/Reddit probes, no archival, training, trading or Discord writes:
.venv/bin/python scripts/check_learning_operations.py --probe
```

The service's actual paths are authoritative; use
`systemctl show stock-assistant-api -p WorkingDirectory -p EnvironmentFiles` if
unsure. Do not create another `.env` in the login home directory. Existing explicit
disable flags need one-time correction in the service environment if learning is
wanted. `ALPHA_VANTAGE_API_KEY` is optional; `SEC_USER_AGENT` needs an identifying
contact for SEC. Neither is needed for the Yahoo-based experiment to collect.

New additive operational tables in `PROFILE_DB_PATH`: `learning_collection_jobs`,
`learning_provider_budget`, `learning_heartbeat`, `research_capture_attempts`, and
`research_monitoring_checkpoints`. No destructive migration, active pointer change,
credential upload or automatic deployment occurs.

The regression model produces an expected five-day return and an uncertainty
estimate. The trading layer independently derives a HOLD band:

- BUY requires confidence plus expected return above transaction cost, the
  calibrated abstention threshold, and the remaining uncertainty buffer.
- Direction confidence is empirically calibrated at the narrowest adequately
  evidenced level: ticker/model/period/version, then model-period, model family,
  then market. Sparse levels fall back instead of presenting noise as precision.
- SELL of an existing position requires meaningful negative edge below the
  separate exit threshold. A tiny negative prediction is HOLD.
- Existing portfolio, market-regime, valuation, volatility, data-quality,
  stop-loss, and board-lot safeguards still apply.
- Opposite BUY/SELL reversals inside the five-trading-day prediction horizon are
  suppressed unless a risk exit or a sufficiently strong calibrated signal applies.
- Cooldown checks the last executed BUY/SELL rather than forgetting it when a later
  `no_action` log row exists.

Every record includes structured thresholds, estimated transaction cost,
uncertainty, active/challenger provenance, and the final decision reason.

## Configuration

```env
MODEL_FEEDBACK_ENABLED=true
MODEL_FEEDBACK_HORIZON_DAYS=5
MODEL_FEEDBACK_MIN_SAMPLES=8
MODEL_FEEDBACK_PROMOTION_WEIGHT=0.35
CONTEXT_FEEDBACK_MAX_ADJUSTMENT=8
CONTEXT_ARCHIVE_ENABLED=true
# Optional; defaults beside the production PROFILE_DB_PATH
MARKET_HISTORY_CACHE_DIR=/home/pi/.local/share/stock-assistant/market_history_cache
```

## API

- `GET /model-lifecycle/feedback`
- `POST /model-lifecycle/feedback/evaluate`
- `GET /model-lifecycle/improvement-status`
- `GET /model-lifecycle/context-coverage`
- `GET /model-lifecycle/funnel?market=US`
- `GET /model-lifecycle/selection-trace?market=US&ticker=AAPL&period=2y`
- `GET /model-lifecycle/model-health?market=US`

The funnel distinguishes promotion from actual runtime adoption and reports
active-model usage, shadow coverage, feedback completion, rollback rate, and the
most common rejection reasons. The selection trace explains the active choice and
why each challenger/rejected version was not selected.

## Foundation audit and deployment interpretation

The executable five-row target is `target_5d_return`, measured in percentage
points. It is formed from adjusted close when the provider supplies a valid
value and falls back to raw close only for an invalid/missing adjusted value.
Raw close remains the executable price used by the Virtual Trader and account
ledger. Saved metadata records `target_price_source`, `target_return_scale`, and
`target_horizon_trading_rows`.

Regression models use scale-independent percentage/ratio features. Linear and
ridge pipelines fit imputation and scaling inside each purged walk-forward
training fold; the test fold is never used to fit the scaler. The automatic
lifecycle excludes sparse historical news features. Current news, public
interest, analyst, earnings, and regulatory context remains a separate,
transparent decision confirmation layer.

An artifact file on disk is not automatically eligible for trading. Runtime
execution accepts only current lifecycle-validated exact-ticker models or an
explicitly pooled `GLOBAL` model. A model fitted for one issuer is never reused
for another issuer. If neither exists, metadata reports `NO_VALID_MODEL` and
`safety_fallback`; backup trend/momentum rules are not reported as ML.

No-trade decisions carry an explicit `decision_outcome`:

- `HOLD`: a valid evaluation completed and did not justify a transaction.
- `SKIP`: evaluation or execution was blocked by missing data, account/cash/lot
  constraints, or another safety control.

The current cost assumptions are deliberately separate:

- Live HK simulation: fixed HKD 50 per executed side.
- Live US simulation: HKD 50 converted with the configured HKD/USD rate per
  executed side.
- Historical promotion proxy: 0.05 percentage points per signal change.

Commission schedules, stamp duty, exchange fees, spread, and slippage are not
currently claimed as modeled. A historical proxy is not a substitute for a full
broker-cost backtest.

Use these read-only diagnostics before deployment:

```bash
python scripts/audit_model_quality.py --target target_5d_return --top 20
curl -sS "http://127.0.0.1:8000/model-lifecycle/model-health?market=US" | python3 -m json.tool
curl -sS "http://127.0.0.1:8000/model-lifecycle/model-health?market=HK" | python3 -m json.tool
```

Audit statuses are distinct:

- `CURRENTLY_VALIDATED`: current provenance and every behavior/economics gate pass.
- `LEGACY_VALIDATION`: behavior passes when replayed, but old validation lacks
  current purge/feature provenance and cannot trade until retrained.
- `NEEDS_REVALIDATION`: legacy provenance and one or more current gates fail.
- `INVALID`: current-format evaluation exists but does not pass every gate.

The audit counterfactual reuses saved out-of-sample timestamps with the current
confidence and cost-aware HOLD band. It is a prediction-layer diagnostic, not
portfolio P&L: account funding, lot size, live context, position sizing, and
fixed market-specific fees are excluded, and alternative candidate models
overlap.

The system is for virtual trading and educational monitoring. Better historical
scores do not guarantee future performance.

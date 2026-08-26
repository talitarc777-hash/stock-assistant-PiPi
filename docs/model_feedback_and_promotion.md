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

The settlement queue processes the oldest eligible prediction first. This is
important because newly recorded predictions have not matured yet and must not
block older five-session outcomes. Production, validated-candidate, compatible
saved-model, and non-executing challenger-shadow predictions contribute to model
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
analysis. Both predictions keep exact ticker, model family, period, version, and
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

After promotion, the previous incumbent stays available as a bounded rollback
candidate. It continues receiving shadow outcomes during probation. Automatic
rollback requires both a large feedback-score deterioration and non-overlapping
direction intervals plus worse after-cost return. Artifact publication clears the
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

## Trading decision layer

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
# Optional; defaults beside the production PROFILE_DB_PATH
MARKET_HISTORY_CACHE_DIR=/home/pi/.local/share/stock-assistant/market_history_cache
```

## API

- `GET /model-lifecycle/feedback`
- `POST /model-lifecycle/feedback/evaluate`
- `GET /model-lifecycle/improvement-status`
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

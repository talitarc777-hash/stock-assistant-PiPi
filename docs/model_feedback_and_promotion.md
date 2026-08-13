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
block older five-session outcomes. Production, validated-candidate, and
compatible saved-model decisions contribute to model scores; rule-based fallback
decisions are excluded. GLOBAL model decisions retain both the traded ticker and the GLOBAL
model origin so their forward evidence is attributed to the correct registry row.

Repeated five-minute scheduler runs do not create repeated feedback for the same
model and trading date.

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

This is a champion/challenger-style safeguard. A small number of lucky outcomes
cannot immediately replace the validated model.

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

The system is for virtual trading and educational monitoring. Better historical
scores do not guarantee future performance.

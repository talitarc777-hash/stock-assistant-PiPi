# Model Training and Virtual Trader Decisions

This note explains how models are formed, whether they update daily, and how model results affect the Virtual Trader.

## 1) What The Model Learns From

The model is trained from one daily feature dataset per ticker.

Dataset builder:

- `app/services/research_pipeline.py`
- main function: `build_feature_dataset(...)`

The dataset combines:

- daily OHLCV price history: open, high, low, close, volume
- return features: 1-day, 5-day, 20-day, 1-month, 3-month, 6-month, 12-month returns
- technical indicators: moving averages, RSI, MACD, volatility, and related fields
- benchmark-relative features versus `VOO` by default
- optional Yahoo Finance news sentiment features
- future target labels used only for training and evaluation

Important target columns:

- `target_5d_updown`
  - classification target
  - `1` means the ticker closed higher 5 trading days later
  - `0` means it did not
- `target_5d_return`
  - regression target
  - the future 5-trading-day return percentage
- `target_20d_regime`
  - longer regime label, currently built as research data

The model does not see future target columns as input features. Those columns are used as labels during training/evaluation.

## 2) How The Model Is Formed

Training code:

- `app/services/model_training.py`
- main functions:
  - `train_baseline_model(...)`
  - `train_baseline_models_for_ticker(...)`

For each ticker, the system trains baseline model families:

- Classification models for `target_5d_updown`:
  - `logistic_regression`
  - `random_forest`
  - `gradient_boosting`
- Regression models for `target_5d_return`:
  - `linear_regression`
  - `random_forest`
  - `gradient_boosting`

The Virtual Trader trading model set is:

- `linear_regression`
- `random_forest`
- `gradient_boosting`

These are trained against `target_5d_return` for trading decisions. Classification models such as `logistic_regression` can still exist for model evaluation pages, but live Virtual Trader selection is not driven by the user's UI model choice.

Validation uses expanding-window time-series splits:

- older rows train the model
- newer rows test the model
- random train/test shuffling is avoided

This is important because stock data is time ordered. Training on future rows and testing on older rows would make the evaluation misleading.

## 3) What Files Are Saved

Model artifacts are saved under:

```text
data/models/<TICKER>/<period>/<target_name>/<model_name>/
```

Each trained model folder normally contains:

- `model.pkl`
  - fitted sklearn pipeline
- `feature_list.json`
  - exact feature columns expected by the model
- `metrics_summary.json`
  - validation metrics and metadata
- `predictions.csv`
  - compact prediction history
- `evaluation_table.csv`
  - walk-forward prediction rows with explanations

The app also syncs these saved folders into the model lifecycle registry at startup.

## 4) Does The Model Update Daily?

Yes, if the backend is running and the model lifecycle scheduler starts successfully.

Scheduler:

- `app/services/model_lifecycle_scheduler.py`
- started during FastAPI startup in `app/main.py`

Automatic lifecycle schedule:

- Daily incremental workflow:
  - runs once per U.S. business day after 4:30 PM America/New_York
  - workflow type: `daily_incremental`
  - period: `2y`
  - universe limit: `18`
  - verifies all three trading models: `linear_regression`, `random_forest`, and `gradient_boosting`
- Weekly full workflow:
  - runs Friday after 5:00 PM America/New_York
  - workflow type: `weekly_full`
  - period: `5y`
  - universe limit: `35`
  - gradient boosting enabled
- Monthly deep workflow:
  - runs on the first U.S. business day of the month after 5:30 PM America/New_York
  - workflow type: `monthly_deep`
  - period: `10y`
  - universe limit: `55`
  - gradient boosting enabled
- Trigger-based workflow:
  - checked periodically when no scheduled workflow is due
  - can retrain models when drift or stale-model signals are detected

The scheduler wakes every 15 minutes to check whether a workflow is due. It does not retrain all models every 15 minutes.

Manual retraining is also available through model lifecycle endpoints and the related web UI.

## 5) Which Model Is Used By The Web App

The selected model name is stored per profile in backend SQLite for model-evaluation views.

Selection service:

- `app/services/model_selection_service.py`

The selected model affects:

- Model Evaluation page
- historical replay endpoints when a profile/user id is provided

Live Virtual Trader decisions do not use the user-selected model. They use automatic runtime selection.

## 6) How The Virtual Trader Uses Model Results

Live trader entry point:

- `app/services/live_virtual_trader.py`
- main function: `run_live_virtual_trader_now(...)`

For each live run, the trader:

1. Resolves the ticker universe.
2. Builds the latest feature dataset for each ticker.
3. Loads model candidates from the lifecycle registry and saved artifacts.
4. Builds the latest feature row using `feature_list.json`.
5. Calls the model to produce a prediction.
6. Converts prediction output into bullish/bearish decision flags.
7. Applies confidence and risk rules.
8. Writes simulated ledger/trade events.

For the automatic trading model set:

- predicted return above the configured minimum means bullish
- predicted return at or below zero means bearish
- confidence is normally unavailable unless the model supports it

The selected runtime model can differ by ticker. The trade log stores the actual model used for each simulated decision.

The model output alone does not directly execute a trade. It must pass the trader's risk and account rules.

## 7) Decision Rules After Prediction

After inference, the Virtual Trader applies additional rules:

- confidence threshold, default around `0.55`
- max position size, default around `25%` of equity
- stop-loss rule, default around `10%`
- optional take-profit rule
- cash availability
- concentration guardrail
- valuation guardrail, for example high PE filtering
- volatility guardrail
- duplicate-signal and cooldown checks

Typical outcomes:

- `buy`
  - bullish model/fallback signal
  - confidence/risk checks pass
  - cash is available
- `sell`
  - stop loss, take profit, or bearish model signal while holding a position
- `hold`
  - already holding and no exit trigger fired
- `no_action`
  - entry conditions did not pass, cash/risk constraints blocked entry, confidence was too low, or cooldown suppressed the signal

All actions are simulation only. No broker order is sent.

## 8) What Happens If No Model Is Available

If no compatible saved model can be loaded, live mode uses a rule-based fallback:

- function: `_build_rule_based_fallback(...)`
- inputs: latest technical state such as SMA trend, RSI, and MACD

Fallback exists so the simulator can keep running instead of failing the whole run.

Automatic selection priority is:

1. production lifecycle model for the ticker
2. latest validated lifecycle candidate for the ticker
3. shared/global production or validated candidate
4. compatible saved ticker/global artifacts
5. rule-based fallback

Only `linear_regression`, `random_forest`, and `gradient_boosting` are considered for automatic live trading model selection. The trade log metadata records whether a decision came from a saved model or fallback.

## 9) Live Mode vs Historical Replay

Live mode:

- uses current/latest available feature data
- runs through the scheduler or manual run-now
- writes simulated account ledger events
- affects the live virtual account state

Historical replay mode:

- uses saved walk-forward evaluation artifacts
- is mainly for research and comparison
- does not represent the current live account state

The live account summary and historical replay charts should be read separately.

## 10) Quick Mental Model

Short version:

```text
market/news data
  -> daily feature dataset
  -> time-series model training
  -> saved model artifacts + lifecycle registry
  -> selected/runtime model
  -> latest prediction during trader run
  -> risk/account rules
  -> simulated buy/sell/hold/no_action
  -> immutable virtual account ledger
```

So daily model updates can change future Virtual Trader decisions, but only after:

- the scheduled or manual lifecycle workflow trains and saves a model
- the model is validated and promoted/resolved at runtime as the best available trading candidate
- the latest market/news features produce a different prediction
- the trader's risk and account rules allow action

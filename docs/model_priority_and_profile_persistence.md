# Model Priority and Profile Persistence

This note documents the latest behavior for model selection, fallback usage, and profile-scoped virtual account persistence.

## Model Selection Priority

At startup, the system prefers saved models first. During live trading runs, the Virtual Trader uses automatic trading-model selection instead of the user's model-evaluation selector.

Priority order:

1. Production model from the lifecycle registry
2. Latest validated candidate from the lifecycle registry
3. Compatible existing saved model for ticker/target
4. Compatible validated shared/existing model artifacts
5. Rule-based fallback (last resort only)

For live Virtual Trader decisions, the automatic trading model set is:

- `linear_regression`
- `random_forest`
- `gradient_boosting`

The user-selected model is still useful for Model Evaluation views, but it no longer controls live Virtual Trader decisions.

Fallback is only used when no compatible saved model can be loaded (missing, corrupted, or unusable).

## Why Fallback Might Still Appear

Rule-based fallback can still be used in edge cases:

- Model artifact path missing
- Artifact exists but cannot be loaded
- Artifact is incompatible with required features

This keeps the trader running safely instead of crashing.

## Profile-Scoped Persistence

Trading account data is persisted by `user_id` (Profile ID) and survives:

- Frontend refresh
- Backend restart
- Frontend rebuild/reload

Backend SQLite storage is the source of truth.

Persisted records include:

- Account ledger events (`monthly_contribution`, `manual_deposit`, `withdrawal`, `buy_trade`, `sell_trade`)
- Monthly contribution records
- Live trader trades
- Live trader positions
- Live trader account snapshot rows

## Rebuild Behavior

Account state is rebuilt from persisted records for the requested `user_id`.

This ensures holdings, cash, and history remain consistent after restart.

## Reset Trading Account (Profile Scoped)

An explicit reset endpoint is available:

- `POST /virtual-account/reset`

Rules:

- Requires `user_id`
- Requires explicit confirmation payload (`confirm_reset=true`)
- Resets only that profile’s trading account data
- Does not touch models
- Does not affect other users

## Diagnostics

Profile diagnostics endpoint:

- `GET /virtual-account/diagnostics?user_id=...`

Includes:

- Ledger row count
- Trade row count
- Position row count
- Monthly contribution row count
- Current cash
- Holdings count
- Total account value

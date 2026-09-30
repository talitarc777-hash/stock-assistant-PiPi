# Model system review — 2026-09-11

## Scope and conclusion

Reviewed executable training, lifecycle, version/deployment, feedback, scheduler,
runtime, model-results and UI selection paths; inspected the SQLite snapshot and
model artifacts; ran an isolated chronological calibration experiment and tests.
No deployment, production training, promotion, gate relaxation, or account changes.

**Workflow correctness improved; higher live predictive accuracy is NOT established.**
The public Cloudflare Pages frontend was reachable and its deployed bundle confirms
that it calls `https://nanopi-r76s.tail8919df.ts.net`. The NanoPi API itself was
unreachable from this audit environment; the precise network/access restriction
was not independently established. The changing values visible in the user's browser are
therefore newer production data that this audit could not download. The local
database is only a historical repository snapshot: its decision history ends May
7, 2026. Do not interpret its statistics as September production results.

## Current actual model flow

1. `model_lifecycle_scheduler.run_cycle` imports saved artifact metadata (capped
   at 400), settles pending feedback (300), refreshes scores when outcomes settle,
   runs due daily/weekly/monthly training and repair triggers.
2. `model_lifecycle_service.run_training_workflow` selects a bounded universe.
   `train_baseline_models_for_ticker` builds price/volume/benchmark features,
   without historical news sentiment in this workflow. Four regression families
   predict `target_5d_return`. A model identity includes market, ticker or GLOBAL,
   training window, target, algorithm, and immutable version.
3. `model_training._train_single_model` uses stationary features, expanding
   chronological folds, a five-session purge, and inner chronological abstention
   calibration. It refits the final model on available labeled training data.
4. `register_training_result` calculates statistical/economic gates, saves a
   version, and may activate an initial historically validated return model.
   A replacement becomes a shadow challenger; enough forward evidence and a
   noninferior/better comparison are required before switching the active pointer.
   The separate benchmark-outperformance objective has its own forward gate.
5. `live_virtual_trader.run_live_virtual_trader_now` calls
   `resolve_runtime_model_candidates`, loads the chosen artifact, predicts,
   calibrates confidence, and applies data quality, market regime, context,
   edge-after-cost, sizing/cash/position rules. Predictions do not automatically
   become trades. HK also uses board lots and HKD accounting.
6. The decision log records the source/version. Runtime and bounded shadow
   observations feed `model_decision_feedback`. After the horizon matures,
   outcomes affect evidence scores, confidence/context adjustments and challenger
   review. This does **not** rewrite all feature weights every five days.

After this change, executable selection uses active version pointers only (exact
ticker before GLOBAL, score-ranked across requested windows). A legacy production
row can still be snapshotted into a version through the compatibility bootstrap.
An unpromoted family candidate can no longer bypass that pointer.
This safety correction may initially increase fallback where candidates previously
bypassed promotion. A lower fallback rate is not claimed until valid coverage and
production adoption are measured after deployment.

## Historical local fallback evidence — not the current production rate

Local repository snapshot: 31,386 US decisions, April 15–May 7; no HK decisions.
Latest 10,000 US rows:

| Recorded source | Count | Share |
|---|---:|---:|
| Rule fallback | 8,497 | 84.97% |
| Production model | 835 | 8.35% |
| Validated candidate | 250 | 2.50% |
| Requested model | 418 | 4.18% |

These counts do **not** answer the current production fallback-rate question. They
only show what happened in the older local snapshot. All 8,497 fallback records contain model-loading errors; examples explicitly
request missing `2y/target_5d_updown/logistic_regression` artifacts. These are old
classifier-era decisions, not evidence about today's return-model runtime.
All 1,503 non-fallback records lack version provenance. All 10,000 actions are
`no_action`.

The present snapshot contains 1,991 registry rows: 1,938 candidate and 53 labeled
production. Only one stored validation flag is true, with gate version 8; 1,990
rows have gate version 0. **None meets current gate version 9.** It has zero
version records, active pointers, or five-day feedback records. Consequently this
snapshot cannot demonstrate current adoption or continuous live improvement.

Stored gate reasons overlap: balanced accuracy below minimum 1,903/1,991 (95.58%);
no majority-baseline edge 1,805 (90.66%); non-overlap direction robustness 1,805;
unstable folds 1,626 (81.67%); low direction accuracy 1,554 (78.05%). These are
registry diagnostics, not independent samples or current production rejection rates.

Disk inventory: 1,998 model files, only three under version directories;
1,001 return-target, 996 up/down-target, one outperformance-target. 1,993 summaries
lack validation-scheme provenance; four declare scheme 5 and one scheme 4.
The number of pickle files is not the number of usable models.

## Duplication found / canonical ownership

- Family registry versus version table versus active deployment pointers held
  different definitions of "the model". Runtime formerly merged them. The active
  deployment pointer now owns execution; registry rows remain compatibility/UI data.
- Training metrics and lifecycle-derived gates were different dictionaries.
  Version registration previously saved raw training metrics, losing lifecycle
  gate provenance. It now receives the normalized validation metrics explicitly.
- `model_selection_service` still scans canonical saved families and defaults to
  up/down classification for evaluation endpoints. It is not execution authority.
- Benchmark shadow feedback and version-specific return-model shadow feedback
  are separate objectives/stores, not interchangeable accuracy evidence.
- `virtual_trader.py` historical simulation and `live_virtual_trader.py` live paper
  trading remain separate paths. Historical simulations do not prove live adoption.

## Contradictions and remaining risks

Fixed:

- Selection trace said only ACTIVE executes, but higher-scoring family candidates
  could execute directly. Removed that path, including its pre-filter 1,000-row cap.
- Feedback refresh concatenated US first then truncated the combined list. A full
  US batch excluded HK. Newest-first refresh also repeatedly revisited the same
  rows. It now selects least-recently-reviewed eligible rows across markets;
  shadow-version review likewise uses oldest-first, without active rows consuming
  its candidate budget.
- A deployment pointer could join a mismatched, retired or unvalidated version.
  The join now enforces full identity, active status and validation flag.

Remaining, not claimed fixed:

- Versioned challenger comparison uses separately aggregated forward histories,
  despite a comment saying histories overlap. It does not explicitly pair dates.
  Before treating this as strong superiority evidence, compare matched ticker/date
  predictions and costs with block uncertainty intervals.
- Shadow collection normally takes one score-prioritized challenger; rollback
  candidates take priority. This can starve other shadow versions even though the
  feedback-review batch now rotates. Add a bounded, dated observation allocation
  policy rather than simply training more versions.
- Saved-artifact sync repeatedly examines the 400 newest canonical summaries.
  Older artifacts may never be reconciled. This is now separate from runtime's
  active-pointer selection, but requires a durable reconciliation cursor.
- Active versions are not automatically revalidated against every newer gate
  version or a uniform age policy. Define an explicit grandfather/revalidate/
  suspend policy; do not silently reinterpret historical validation flags.
- Pending settlement takes the oldest 300 rows. Permanently unavailable histories
  can obstruct later rows; add retry timestamps and a visible quarantine policy.
- Training/validation use percentage-cost proxies. Live HK fixed fees and board
  lots, US fees, cash, exposure, and overlapping signals are not fully replicated
  by model gate economics.

## Legacy logic

Eventually retire the mutable family-selection fallback from evaluation settings,
timestamp-as-version identifiers, default `target_5d_updown` assumptions in older
endpoints, and routine canonical-file reconciliation after a verified migration.
Keep legacy bootstrap and historical APIs until existing deployments/data are
reconciled; no artifact or user record was deleted in this review.

## Model formation weaknesses

- Directional edge is weak, especially after accounting for majority-UP and
  overlapping five-session labels. More passing models is not an accuracy metric.
- Two-year folds and the inner calibration slice can be small (as few as ten
  calibration observations). Choosing among abstention thresholds can overfit.
- Repeated algorithms/windows/ticker selection creates multiple-testing risk.
- Current news/social/analyst context lacks the archived as-of feature panel needed
  for defensible historical training; live context is not evidence the model learned
  those factors. No synthetic news history was introduced.
- US training uses fixed 18/35/55-ticker refresh limits and a smaller pooled prefix;
  the execution universe is wider. Coverage gaps are therefore expected.
- HK training uses foundation tickers plus effective saved HK watchlists, whereas
  live HK evaluation follows marked/watchlist scope. Training count need not equal
  runtime count. Small HK cross-sections and higher volatility weaken inference.
- Forward "strategy net return" is signed hypothetical return; unexecuted/zero-size
  observations may have no administrative cost. It is not account profit, and a
  bearish forecast is not a short position in this long-only trader.

## Accuracy results and improvements tested now

Ran `audit_forward_calibration.py` on the existing purged OOS streams for a fixed
21-ticker cohort (16 US, five HK), 2y ridge only. No ticker/model search. Proposal:
correct bias using the last 60 residuals, at least 30 observations, delayed six
rows so five-session outcomes have matured, capped at one percentage point.
No training/runtime artifact was modified.

| Market / historical split | Rows | Direction before → after | Balanced before → after | Always-UP accuracy |
|---|---:|---:|---:|---:|
| US development | 2,528 | 48.69% → 48.50% | 49.72% → 49.61% | 58.98% |
| HK development | 765 | 50.46% → 51.11% | 51.63% → 52.19% | 57.78% |
| US previously opened test | 3,952 | 51.16% → 52.28% | 51.53% → 52.19% | 56.73% |
| HK previously opened test | 1,235 | 48.74% → 49.23% | 48.75% → 49.26% | 48.50% |

Later-block MAE: US 5.169 → 4.896 percentage points; HK 7.124 → 6.877.
US later-block MCC: .0304 → .0434; HK: -.0249 → -.0148. Small changes do not
establish useful skill. Development economics weakened in both markets, so the
proposal was **not adopted**. These are recomputed historical OOS diagnostics,
not today's production accuracy or new forward observations.

## Locked-test result — most important limitation

**No fresh untouched locked test is available from the audited evidence.**
The earlier August objective audit already opened the relevant historical block.
The new diagnostic labels it `previously_opened_test`, not a new locked success.
The archived ridge predictions end August 14, 2026. A fresh production snapshot
and a predeclared prospective cutoff are needed for the next valid live test.

Directly inspected archived `POSTHOC_STRONG_BASELINE_RECHECK.json` (not retrained
this turn): the selected high-volatility forest's test ROC-AUC was US .7944 versus
simple baseline .8010; HK .6928 versus .6589, over 5,187 observations. This was
risk-event prediction, not stock-direction accuracy. Cross-market incremental
value was not established. The file explicitly records that the holdout was open.

## Baselines and BUY/HOLD/SELL quality

Always-UP remains stronger than the tested US model in raw direction accuracy.
Balanced accuracy avoids rewarding that imbalance. The prior risk forest did not
consistently beat the calibrated rolling-volatility/simple-risk baselines.

The new experiment's fixed >1% long signal with 0.10pp round-trip diagnostic cost
yielded later-block mean net return per opportunity: US .2612% → .2975%; HK
-.1038% → -.0970%. Always-in-the-same-asset comparisons were .4329% US and
-.2502% HK; cash is zero in this diagnostic. Development signal returns fell
US .0398% → -.0032%, HK .3905% → .3102%.

These are overlapping signal diagnostics with five non-overlapping phase summaries
provided in JSON, **not portfolio CAGR or evidence of better executed BUY/SELL**.
They omit HK board-lot/fixed-fee sizing. No new full account replay or current live
BUY/HOLD/SELL quality result can be substantiated from this snapshot. Do not promote
the correction on these numbers. Next test must replay identical timestamped prices,
cash, position constraints and actual costs, with the existing rule policy and cash
as baselines, and attribute executed trades to immutable versions.

## Model adoption check

Passing tests cover real version registration → promotion → active selection →
rollback, and a mocked-market runtime integration verifies the active artifact path
and version are loaded/persisted into decision feedback. High-scoring unpromoted
candidates are excluded; HK GLOBAL coverage remains available via active pointers.
Tests also cover non-finite/type/feature failures falling back explicitly.
This proves the code path under tests, not that NanoPi currently runs this revision.

## Bugs fixed (correctness only)

1. Unpromoted candidate bypass of immutable active selection.
2. US-first feedback batch exclusion of HK; newest-first registry/shadow review.
3. Loss of lifecycle gate metrics when storing version records.
4. Invalid/mismatched deployment-pointer joins.
5. Non-finite predictions and ValueError/TypeError inference failures now fall back
   with diagnostics instead of poisoning/dropping a ticker decision.
6. Decision metadata now distinguishes `no_eligible_active_model`,
   `eligible_models_failed_inference`, and `active_model_loaded`, with candidate count.

## Recommended canonical architecture

As-of market data → immutable dataset/feature/target contract → purged training →
versioned validation evidence → scheduled shadow observations → matched-date
forward comparison → atomic active pointer → shared decision/risk/cost engine →
immutable trade provenance → horizon settlement → monitored promotion/rollback.

Use one version identity and one decision engine for historical replay and live
paper trading. Keep registry/UI summaries as projections, not alternative model
selection authorities. Preserve strict gates; measure fallback causes and coverage
instead of making more predictions appear validated.

## Files changed

- `app/services/live_virtual_trader.py`
- `app/services/model_lifecycle_service.py`
- `app/services/model_version_service.py`
- `scripts/audit_model_system.py` — standalone read-only snapshot/artifact audit.
- `scripts/audit_forward_calibration.py` — isolated chronological diagnostic.
- `tests/test_hk_virtual_trader_support.py`
- `tests/test_live_virtual_trader_model_version_adoption.py`
- `tests/test_live_virtual_trader_decision_edge.py`
- `tests/test_model_lifecycle_service.py`
- `tests/test_model_version_service.py`
- `tests/test_model_system_audit.py`
- This report.

Generated local evidence (ignored by git):
`reports/model_system_snapshot_2026-09-11.json` and
`reports/forward_calibration_2026-09-11.json`.
No schema migration or frontend source changes. New decision metadata is additive.

## Tests and checks

- Full backend unittest discovery: **335 passed**.
- Frontend `npm test`: **29 passed**.
- Frontend `npm run build`: **passed**.
- Snapshot audit and chronological calibration diagnostic: executed successfully.
- Python compilation of modified services/audit scripts: passed.
- `git diff --check`: passed.
- No dedicated lint/type-check configuration was found; do not claim those passed.
- Initial frontend sandbox runs failed with child-process EPERM; approved reruns passed.
- Initial backend runs exposed an outdated HK selection fixture and two new test
  fixture issues; fixed, then reran the full suite successfully.

Model pickle content fingerprint unchanged before/after:
`1f1f093f2d0659fcbe1cb50aa2a4b3d6e6243e7801d64dea0e7106a9a0465383`.

## Production verification still needed

The changing validated-model count on the live page confirms that production
lifecycle data is being refreshed; by itself it does not reveal which models were
activated, used for decisions, or why individual decisions fell back. Copy/run the
standalone audit script on NanoPi using the backend's **actual resolved
database path** (not a guessed repository database):

```sh
/srv/stock-assistant-PiPi/.venv/bin/python scripts/audit_model_system.py \
  --db /actual/backend/database.db \
  --models /srv/stock-assistant-PiPi/data/models \
  --output /tmp/model-system-production-audit.json
```

Run from `/srv/stock-assistant-PiPi`; replace the database placeholder. This command
does not train, trade, promote, or deploy. Return the JSON for current fallback and
adoption counts. Live accuracy and matched-date economics need the corresponding
versioned forward observations as well; the summary alone cannot certify them.

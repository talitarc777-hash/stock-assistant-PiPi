# Model Trader objective-redesign audit — 2026-08-23

Status: **EXPERIMENTAL ONLY**. Nothing was deployed, registered, promoted, or made runtime-selectable. Production gates, registry entries, active pointers, and Virtual Trader decisions were not changed.

The fixed 21-security sample used the final 252 market dates per market as a locked block, with a five-date label purge. The block was untouched by this redesign until one architecture was selected, but it is not globally virgin because the preceding absolute-return audit had already summarized these historical dates.

## 1. Absolute-return baseline

The prior controlled current-scheme result was frozen and reused read-only: **3/220 passed (1.36%)**. The 38-feature design had about 50.18% balanced accuracy / MCC 0.0041; compact had about 50.21% / MCC 0.0045; pooled and alternative absolute targets had no repeatable design-level edge. The isolated passes were NVDA 2y linear_regression, XLP 5y ridge_regression, XLP 2y ridge_regression.

## 2. Excess-return regression results

Target = ticker adjusted five-day return minus `VOO` (US) or `2800` (HK) over identical dates. Action bands used cost plus training-fold-only uncertainty.

| Model | Correlation | 95% block CI | Balanced | MAE | Recent-relative-momentum MAE | After-cost excess | 95% block CI |
|---|---:|---|---:|---:|---:|---:|---|
| random_forest | 0.017 | [-0.004436142811981172, 0.04127380051053052] | 0.500 | 2.664% | 3.735% | -0.012% | [-0.04221321802743291, 0.01644827213867023] |
| ridge_regression | -0.001 | [-0.0168208039957652, 0.01741507627793204] | 0.506 | 2.619% | 3.735% | 0.022% | [-0.01315330292054693, 0.05524805619444889] |

Neither model shows stable association: Ridge correlation is approximately zero, and the small positive economic replay is not accompanied by predictive correlation.

## 3. Relative classification results

Three-class and binary economic thresholds were recomputed inside every training fold; final/OOS returns never set a class boundary.

| Target:model | Balanced | MCC | ROC-AUC | PR-AUC | After-cost excess |
|---|---:|---:|---:|---:|---:|
| relative_binary:logistic_regression | 0.510 | 0.023 | 0.522 | 0.474 | 0.038% |
| relative_binary:random_forest | 0.511 | 0.024 | 0.519 | 0.472 | 0.026% |
| relative_three_class:logistic_regression | 0.339 | 0.016 | N/A | N/A | 0.026% |
| relative_three_class:random_forest | 0.339 | 0.015 | N/A | N/A | 0.005% |

Three-class balanced accuracy is about 0.339; binary balanced accuracy is about 0.51. Neither is meaningfully repeatable.

## 4. Cross-sectional ranking results

Ranking was market-local and date-global: no future-period normalization and no US/HK mixing.

| Model | Spearman IC | Worst market CI lower | Pearson IC | After-cost spread | Worst market CI lower | Top hit | Turnover | Stable folds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| random_forest | 0.025 | -0.024 | 0.016 | 0.101% | -0.489 | 0.250 | 0.910 | 0.484 |
| ridge_regression | 0.005 | -0.027 | 0.006 | -0.148% | -0.653 | 0.249 | 0.998 | 0.400 |

The forest's aggregate IC/spread is positive, but both worst-market confidence bounds cross zero and fewer than half the folds are jointly positive.

## 5. Risk-prediction results

Future volatility is annualized next-five-session realized volatility. Adverse/high-vol events use training-fold 85th/75th percentiles.

| Volatility model | MAE | Rolling-vol MAE | Improvement | Correlation | Positive-improvement folds |
|---|---:|---:|---:|---:|---:|
| random_forest | 11.210% | 12.044% | 0.833% | 0.593 | 0.800 |
| ridge_regression | 10.897% | 12.044% | 1.147% | 0.617 | 1.000 |

| Event:model | ROC-AUC | PR-AUC | Recall | FPR | Brier |
|---|---:|---:|---:|---:|---:|
| adverse_event:logistic_regression | 0.669 | 0.351 | 0.112 | 0.034 | 0.161 |
| adverse_event:random_forest | 0.676 | 0.339 | 0.033 | 0.009 | 0.157 |
| high_vol_event:logistic_regression | 0.793 | 0.726 | 0.458 | 0.089 | 0.178 |
| high_vol_event:random_forest | 0.808 | 0.718 | 0.445 | 0.092 | 0.175 |

The initially selected high-vol forest has development ROC-AUC 0.808, but the best calibrated one-feature baseline has 0.808 (uplift 0.000). Its PR-AUC is 0.718 versus baseline 0.724 (uplift -0.006). Thus most risk predictability already exists in simple observable risk state.

## 6. Regime-conditioned results

Bull/non-bull, high/low volatility, and stressed/normal boundaries used only each training fold's median benchmark trend, median ticker volatility, and 25th-percentile drawdown. Detailed rows are preserved in the JSON artifact. No candidate was stable enough across regimes and folds to rescue an otherwise weak objective; no regime threshold was tuned on final data.

## 7. Existing non-price data inventory

| Source | Coverage/depth | Refresh | US/HK | Safe now? |
|---|---|---|---|---|
| Yahoo Finance ticker/company news | up to the provider's current returned article window per request; not guaranteed; no persistent article archive | on dataset build; provider-dependent | US and HK symbols accepted; depth not guaranteed for either | no |
| Reddit social search | configured subreddits, newest one-week search, capped posts; one week at fetch time; no database history | one-hour in-memory cache | query accepts both; mapping quality varies | no |
| yfinance analyst consensus and revisions | current info plus provider's recent recommendation tables; not persisted | one-hour in-memory cache | US and some HK provider symbols | no |
| Alpha Vantage news sentiment and earnings transcripts | optional API-key feed; recent news and up to six recent quarters; not persisted as a dated feature panel | one-hour in-memory context cache | provider-dependent, principally US | no |
| SEC EDGAR filing metadata | latest submission list and filing dates; official history available in response but current code aggregates latest 20 | CIK map daily, context hourly in memory | US only | no |
| fundamentals / macro / true market breadth | none in the research dataset; none | none | none | no |

## 8. Point-in-time / leakage assessment

No non-price source qualified for model fitting. Yahoo news lacks a persistent archive and zero-fills unavailable dates; Reddit/analyst/Alpha Vantage context is a current snapshot; SEC filing dates are promising but the current code aggregates recent filings rather than constructing a daily as-of panel. Fundamentals, macro, and true breadth have no historical training source. Therefore no price-plus-non-price experiment was fabricated.

## 9. Three passing-model investigation

The pass rate was 3/220 (1.36%), with material multiple-testing risk.

| Model | Original direction/balanced | Non-overlap direction | 95% CI | Feature sensitivity (current/reduced/compact) | Start sensitivity (0/63/126) |
|---|---|---:|---|---|---|
| NVDA 2y linear_regression | 0.577/0.565 | 0.512 | [0.4059077238820504, 0.6173910413938009] | 0.491/0.505/0.557 | 0.491/0.498/0.512 |
| XLP 5y ridge_regression | 0.598/0.609 | 0.519 | [0.45160034026039847, 0.5861637501331223] | 0.558/0.554/0.559 | 0.558/0.543/0.537 |
| XLP 2y ridge_regression | 0.591/0.565 | 0.537 | [0.4294472560322624, 0.6404490397952913] | 0.558/0.554/0.559 | 0.558/0.543/0.537 |

All non-overlapping intervals cross 50%; feature/start sensitivity is non-trivial. Their individual final block was not opened, preserving the one-architecture rule. They look more like sparse selection exceptions than established ticker-specific signal and were not promoted.

## 10. US versus HK findings

HK had 5 securities versus 16 US securities. Median development rows: 1742 HK. Median excess-return volatility: 4.633% HK vs 2.753% US; annualized volatility: 40.433% vs 21.838%. The five-name HK cross-section is too thin for robust quintiles. HK risk discrimination was more incremental than US in the locked diagnostic, but this did not repeat across both markets. `2800` remains the safest existing benchmark; no HK standards were lowered.

## 11. Development-period results

The original predeclared selector chose **pooled_random_forest_high_vol_event_risk_filter**; 4/16 candidates cleared its first-pass criteria. Final review found those risk criteria incomplete because they cleared chance/prevalence but did not require incremental discrimination over the strongest calibrated simple risk baseline. The immutable selection file was preserved; no second model was selected after this defect was found.

## 12. Locked-test results

Only the selected high-volatility forest was evaluated on 5187 matured locked rows. The later run recalculated baselines for that same fixed architecture only; it is explicitly post-hoc because the holdout was already open.

| Market | Model ROC | Best simple ROC | Uplift | Model PR | Best simple PR | Uplift | Model Brier | Best simple Brier |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| HK | 0.693 | 0.659 | 0.034 | 0.347 | 0.295 | 0.052 | 0.158 | 0.163 |
| US | 0.794 | 0.801 | -0.007 | 0.559 | 0.559 | 0.000 | 0.142 | 0.142 |

US ROC-AUC is worse than the simple baseline; HK improves. That is not cross-market repeatability.

## 13. Best simple baseline

For absolute direction, Always-UP remains the hard raw-accuracy baseline (56.77%, balanced 50%). For relative return, recent five-day relative momentum is the direct magnitude baseline. For risk, the strongest fair comparison is a training-only one-feature calibrated model using rolling 20-day volatility, current drawdown, or intraday range. The selected forest does not consistently beat this simple risk baseline.

## 14. Best ML architecture

The strongest ML diagnostic is the pooled shallow random-forest high-volatility classifier. It predicts high-volatility states, not BUY/SELL direction. It is **not adoption-ready** because its incremental development PR-AUC is negative versus the best simple proxy and locked US ROC-AUC is worse. No alternate architecture may be selected now that the holdout is open.

## 15. Statistical significance

Relative correlation and ranking-spread intervals cross zero. The three prior-pass non-overlap Wilson intervals cross 50%. The high-vol forest clears chance strongly, but that tests whether volatility is predictable—not whether ML adds value beyond observable volatility. Incremental risk skill is near zero in development and changes sign by market in the locked block. No multiple-comparison-adjusted, repeatable incremental ML edge is established.

## 16. Economic significance

The unchanged rule plus high-vol forest filter reduced mean after-cost signal return from 0.023% to 0.005%; its improvement was -0.018% with a 95% block interval approximately [-0.024%, -0.010%]. It retained about 91% of winning rule signals but avoided only about 7% of losing signals. Relative/ranking economic results were not confidence-stable. No tested ML architecture shows exploitable economic improvement.

## 17. Computational cost

Primary audit wall time: 525.2 seconds; fixed-architecture calibrated-baseline recheck: 84.0 seconds; total about 10.2 minutes on this workstation. The controlled design used two simple model forms, five purged folds, 21 securities, and 10-year source histories; it did not launch a broader hyperparameter or indicator search. Compressed OOS streams and JSON evidence are under `data/model_design_experiments/objective_redesign_2026_08/`.

## 18. Files changed

- `scripts/model_objective_benchmark.py` — isolated objective/ranking/risk/holdout harness.
- `tests/test_model_objective_benchmark.py` — leakage, split, threshold, ranking, baseline, and isolation tests.
- `reports/model_objective_audit_2026-08-23.md` — this audit.

Generated experiment evidence is ignored under `data/model_design_experiments/objective_redesign_2026_08/`. Production model-tree fingerprint before/after is unchanged: `4469e6ddc1a1ae94f9c138f38fb5e7f7f00eac746345c7ec5c830257995bab3c`.

## 19. Tests run

- `python -m unittest tests.test_model_objective_benchmark -v`: 10 passed.
- `python -m unittest discover -s tests`: 328 passed.
- `python -m py_compile scripts/model_objective_benchmark.py tests/test_model_objective_benchmark.py`: passed.
- `git diff --check -- scripts/model_objective_benchmark.py tests/test_model_objective_benchmark.py reports/model_objective_audit_2026-08-23.md`: passed.

No frontend source or production runtime source was changed by this objective-redesign stage, so frontend build/tests were not rerun for this stage.

## Architecture recommendation

**F. NO ML APPROACH CURRENTLY JUSTIFIED**

# Model Trader design audit — 2026-08-23

Status: **EXPERIMENTAL ONLY**. Nothing was deployed, promoted, registered, or made selectable by Virtual Trader. Existing validation thresholds were not changed.

## 1. Broader benchmark results

The fixed Stage 1 sample contained 21 real configured securities: six US ETFs, seven US stocks, three US REITs, and five HK stocks. All received 2y and 5y runs; the fixed 13-security experiment core also received 10y runs. This produced 220 current-scheme models (55 per algorithm) with zero data/training failures.

Gate funnel:

| Gate | Independent pass | Cumulative pass |
|---|---:|---:|
| Readable OOS evaluation | 220 | 220 |
| Validation score >= existing minimum | 138 | 138 |
| Existing walk-forward quality gate | 4 | 4 |
| Existing historical trading gate | 102 | 3 |
| Current validation provenance | 220 | 3 |
| All gates | 3 | 3 (1.36%) |

The three isolated current-scheme passes were NVDA 2y Linear Regression and XLP 2y/5y Ridge Regression. HK was 0/52; 10y was 0/52. By class: ETF 2/60, stock 1/124, REIT 0/36. By algorithm: Linear 1/55, Ridge 2/55, Random Forest 0/55, Gradient Boosting 0/55.

No point-in-time historical market-cap field exists in the research dataset. The audit therefore did not backfill a present-day market cap into old folds. It stratified supported liquidity and volatility using median dollar volume and annualized volatility measured only on the initial 60% of each history.

## 2. Current-feature model performance

Across the fixed 13-security, 2y/5y/10y experiment core and all four algorithms (156 runs), the 38-feature design averaged 50.13% direction accuracy, 50.18% balanced accuracy, MCC 0.0041, and a **-7.15 percentage-point edge versus each sample's majority direction**. Mean prediction/actual correlation was 0.0332 and MAE was 5.66 percentage points. One experiment-core run cleared the behavioral gate calculation.

## 3. Reduced-feature performance

Training-fold-only constant/correlation removal retained a median 30 of 38 features. It averaged 50.24% direction accuracy, 50.09% balanced accuracy, MCC 0.0025, majority edge -7.04 points, correlation 0.0288, and MAE 4.71 points. One isolated run cleared the behavioral calculation. Paired improvement versus current was not significant: direction +0.11 points (95% cluster-bootstrap CI -0.17 to +0.41) and balanced accuracy -0.09 points (CI -0.34 to +0.19).

## 4. Compact-feature performance

The predeclared 10-feature core used short/medium/long momentum, benchmark-relative momentum, volatility, drawdown, RSI, long trend distance, MACD histogram, and relative volume. A constant fold field was removed, so the realized median was 10 features (range 9–10).

It averaged 51.38% direction accuracy, 50.21% balanced accuracy, MCC 0.0045, majority edge -5.90 points, correlation 0.0249, and MAE 4.39 points. Raw direction improved by +1.25 points (95% CI +0.69 to +1.82), but balanced accuracy changed only +0.03 points (CI -0.43 to +0.53). The apparent improvement is therefore mostly majority/up-market alignment, not repeatable two-sided skill. Three isolated runs cleared behavioral calculations.

For HK alone, compact improved raw direction from 49.33% to 52.19% and balanced accuracy from 50.11% to 51.02%, but mean majority edge remained -3.42 points and 0/36 compact HK runs cleared all behavioral gates.

## 5. Per-ticker versus pooled

Forty-eight globally chronological compact pooled models were evaluated for US ETFs, US stocks, US REITs, and HK stocks. Ticker identity was not encoded; preprocessing used training folds only; comparisons used shared future dates.

Pooled models cleared 0/48 behavioral gates. Across exact-window comparisons, pooled direction changed -0.07 points and balanced accuracy -0.84 points versus per-ticker compact models. HK pooling was specifically worse: direction -1.81 points and balanced accuracy -2.39 points. US ETF raw direction improved +1.29 points, but balanced accuracy improved only +0.27 and aggregate majority edge remained negative.

## 6. Regression versus classification/two-stage

The economic class boundary was derived per training fold as configured round-trip cost plus the 95% robust uncertainty of non-overlapping training returns. Its median was 0.695% (IQR 0.447%–0.949%). The two-stage probability threshold median was 0.636.

| Target | Direction | Balanced | MCC | Majority edge | Brier | Behavioral clears |
|---|---:|---:|---:|---:|---:|---:|
| Binary direction | 52.55% | 50.00% | -0.0003 | -4.73 pts | 0.310 | 0/39 |
| Economic 3-class | 52.46% | 50.37% | 0.0085 | -4.83 pts | 0.768 (multiclass scale) | 0/39 |
| Two-stage | 48.51% | 49.71% | -0.0059 | -8.77 pts | 0.310 | 1/39 |

Binary and economic-class raw direction improved by about 0.98 and 0.88 points versus compact Ridge, but balanced-accuracy confidence intervals crossed zero. Two-stage reduced actionable signals to 17,757 from roughly 32k–34k but made direction materially worse. Classification labels do not have percentage-return MAE; only the two-stage magnitude output is eligible for that metric.

## 7. Best-performing configuration

No configuration demonstrated genuine repeatable edge. The least-bad aggregate balanced accuracy was economic three-class at 50.37% with MCC 0.0085, but its majority edge was still -4.83 points and it cleared 0/39 gates. It is not a supported adoption candidate.

## 8. Simple baselines

Always-UP averaged 56.77% raw direction because the samples were upward-biased, while balanced accuracy was exactly 50% and MCC 0. Training-majority averaged 54.67% direction. The complex designs did not beat the direction/majority baseline on aggregate. Recent-5d momentum averaged 50.51%, 20d momentum 51.40%, SMA trend 51.50%, matured historical mean 54.04%, and zero-return 43.23% direction.

Positive long-horizon replay totals were not treated as proof of skill: always-UP produced larger bull-market totals, while experimental signal proxies suffered worst drawdowns around -60% to -71%. The replay is a symmetric, five-path signal proxy—not a funded Virtual Trader portfolio backtest.

## 9. Existing quality gates

Three of 220 current-scheme Stage 1 artifacts and six individual experiment runs cleared the existing behavioral calculations. Those six do not establish a successful configuration: they are sparse, ticker/period-specific exceptions among hundreds of runs, aggregate majority edge is negative, and pooled models passed 0/48. Experimental models were never lifecycle-registered, so none is runtime `CURRENTLY_VALIDATED` or selectable.

## 10. Statistical meaning

Compact raw direction improvement and binary/economic raw direction improvement had paired 95% intervals above zero. Their balanced-accuracy intervals crossed zero, MCC stayed near zero, and majority edge stayed materially negative. Reduced features were not significantly different. The evidence supports simpler features as a useful diagnostic direction, but not predictive adoption.

## 11. Files changed in this stage

- `.gitignore`
- `scripts/model_design_benchmark.py`
- `tests/test_model_design_benchmark.py`
- `reports/model_design_audit_2026-08-23.md`

Ignored experiment artifacts are under `data/model_design_experiments/controlled_2026_08/`. Production `data/models`, lifecycle registries, and active pointers were not written.

## 12. Tests run

- `python -m unittest tests.test_model_design_benchmark -v`: 6 passed.
- `python -m unittest discover -s tests`: 318 passed.
- `python -m py_compile scripts/model_design_benchmark.py tests/test_model_design_benchmark.py`: passed.
- `npm.cmd test`: 20 passed. An earlier `npm.cmd test -- --run` attempt failed before assertions because Node could not spawn workers; the normal project command then passed with required process permission.
- `npm.cmd run build`: passed; Vite transformed 62 modules and produced the production bundle.

## 13. Recommendation

Do not deploy, promote, change gates, or start a full-universe retraining campaign. The compact design is a cleaner research basis, especially for HK, but current evidence does not show repeatable balanced OOS edge. The next experiment should add genuinely new information or rethink the decision objective, then confirm it on a locked untouched time block; it should not tune the existing 38 technical variants further.

**E. MORE EVIDENCE REQUIRED**

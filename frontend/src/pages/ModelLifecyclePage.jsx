import React, { useEffect, useMemo, useState } from "react";

import {
  fetchModelLifecycleRegistry,
  fetchModelLifecycleRuns,
  fetchModelLifecycleStatus,
  fetchModelHealth,
  fetchModelImprovementStatus,
  runModelLifecycleNow,
} from "../api";
import {
  buildModelPerformanceRows,
  formatModelRate,
  historicalAccuracy,
  liveMatchingRate,
  predictionRate,
} from "../utils/modelMetrics";

const TRADING_PERIODS = ["2y", "5y", "10y"];
const TRADING_MODELS = [
  "linear_regression",
  "ridge_regression",
  "random_forest",
  "gradient_boosting",
];

function labelByMode(mode, en, zh) {
  if (mode === "zh") return zh;
  if (mode === "en") return en;
  return `${en} / ${zh}`;
}

function scoreText(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${(numeric * 100).toFixed(1)}%` : "N/A";
}

function dateText(value) {
  if (!value) return "N/A";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function modelText(value) {
  const labels = {
    linear_regression: "Linear regression",
    ridge_regression: "Ridge regression",
    random_forest: "Random forest",
    gradient_boosting: "Gradient boosting",
  };
  return labels[value] || value || "N/A";
}

function workflowText(value, languageMode) {
  const labels = {
    daily_incremental: ["Daily 2-year refresh", "\u6bcf\u65e5 2 \u5e74\u6a21\u578b\u66f4\u65b0"],
    weekly_full: ["Weekly 5-year refresh", "\u6bcf\u9031 5 \u5e74\u6a21\u578b\u66f4\u65b0"],
    monthly_deep: ["Monthly 10-year refresh", "\u6bcf\u6708 10 \u5e74\u6a21\u578b\u66f4\u65b0"],
    trigger_based: ["Automatic repair refresh", "\u81ea\u52d5\u4fee\u5fa9\u66f4\u65b0"],
  };
  const [en, zh] = labels[value] || [value || "Unknown", value || "\u672a\u77e5"];
  return labelByMode(languageMode, en, zh);
}

function statusText(value, languageMode) {
  if (value === "success") return labelByMode(languageMode, "Completed", "\u5df2\u5b8c\u6210");
  if (value === "partial_success") return labelByMode(languageMode, "Partly completed", "\u90e8\u5206\u5b8c\u6210");
  if (value === "failed") return labelByMode(languageMode, "Failed", "\u5931\u6557");
  return value || "N/A";
}

export default function ModelLifecyclePage({ languageMode }) {
  const [status, setStatus] = useState(null);
  const [hkStatus, setHkStatus] = useState(null);
  const [improvementStatus, setImprovementStatus] = useState(null);
  const [modelHealth, setModelHealth] = useState({ US: null, HK: null });
  const [registry, setRegistry] = useState([]);
  const [runs, setRuns] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isRunningNow, setIsRunningNow] = useState("");
  const [showTrustExplanation, setShowTrustExplanation] = useState(true);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [error, setError] = useState("");

  async function loadAll() {
    setIsLoading(true);
    setError("");
    const results = await Promise.allSettled([
      fetchModelLifecycleStatus("VOO", "2y", "target_5d_return", 6, "US"),
      fetchModelLifecycleStatus("0700", "2y", "target_5d_return", 6, "HK"),
      fetchModelLifecycleRegistry(1000, { market: "US" }),
      fetchModelLifecycleRegistry(1000, { market: "HK" }),
      fetchModelLifecycleRuns(8),
      fetchModelImprovementStatus(),
      fetchModelHealth("US"),
      fetchModelHealth("HK"),
    ]);

    const [statusResult, hkStatusResult, usRegistryResult, hkRegistryResult, runsResult, improvementResult, usHealthResult, hkHealthResult] = results;
    setStatus(statusResult.status === "fulfilled" ? statusResult.value : null);
    setHkStatus(hkStatusResult.status === "fulfilled" ? hkStatusResult.value : null);
    setRegistry([
      ...(usRegistryResult.status === "fulfilled" ? usRegistryResult.value || [] : []),
      ...(hkRegistryResult.status === "fulfilled" ? hkRegistryResult.value || [] : []),
    ]);
    setRuns(runsResult.status === "fulfilled" ? runsResult.value || [] : []);
    setImprovementStatus(
      improvementResult.status === "fulfilled" ? improvementResult.value : null
    );
    setModelHealth({
      US: usHealthResult.status === "fulfilled" ? usHealthResult.value : null,
      HK: hkHealthResult.status === "fulfilled" ? hkHealthResult.value : null,
    });

    const failedCount = results.filter((item) => item.status === "rejected").length;
    if (failedCount > 0) {
      setError(
        failedCount === results.length
          ? "Model information could not be loaded. Please retry."
          : "Some model details are temporarily unavailable."
      );
    }
    setIsLoading(false);
  }

  useEffect(() => {
    loadAll();
  }, []);

  async function handleRunNow(market) {
    setIsRunningNow(market);
    setError("");
    try {
      await runModelLifecycleNow(
        "daily_incremental",
        `manual_dashboard_run:${market}`,
        null,
        market
      );
      await loadAll();
    } catch (requestError) {
      setError(requestError.message || `Failed to refresh the ${market} 2-year models.`);
    } finally {
      setIsRunningNow("");
    }
  }

  const tradingRegistry = useMemo(
    () =>
      registry.filter(
        (item) =>
          item.target_name === "target_5d_return" &&
          TRADING_PERIODS.includes(item.period) &&
          TRADING_MODELS.includes(item.model_name)
      ),
    [registry]
  );

  const modelPerformanceRows = useMemo(
    () => buildModelPerformanceRows(tradingRegistry, TRADING_MODELS),
    [tradingRegistry]
  );

  const rankedForVoo = useMemo(
    () =>
      tradingRegistry
        .filter(
          (item) =>
            item.market === "US" &&
            item.ticker === "VOO" &&
            item.is_validated &&
            ["production", "candidate"].includes(item.status)
        )
        .sort((left, right) => {
          if (Boolean(left.is_stale) !== Boolean(right.is_stale)) return left.is_stale ? 1 : -1;
          return Number(right.validation_score || 0) - Number(left.validation_score || 0);
        }),
    [tradingRegistry]
  );

  const preferredModel = rankedForVoo[0] || null;
  const improvementRows = ["US", "HK"].map(
    (market) => improvementStatus?.markets?.[market] || { market }
  );
  const legacyValidationCount = tradingRegistry.filter(
    (item) => item.stored_is_validated && !item.validation_evidence_current
  ).length;
  const currentValidationCount = tradingRegistry.filter((item) => item.is_validated).length;
  const pooledRecordCount = tradingRegistry.filter(
    (item) => item.ticker === "GLOBAL" || item.metrics_summary?.pooled_training
  ).length;
  const outperformanceRegistry = registry.filter(
    (item) => item.target_name === "target_5d_outperform"
  );
  const economicsPassedCount = outperformanceRegistry.filter(
    (item) => item.metrics_summary?.outperformance_economics_gate?.passed
  ).length;
  const validatedOutperformanceTickers = Array.from(new Set(
    outperformanceRegistry
      .filter(
        (item) =>
          item.is_validated &&
          !item.is_stale &&
          ["production", "candidate"].includes(item.status) &&
          item.ticker !== "GLOBAL"
      )
      .map((item) => item.ticker)
  )).sort();
  const leadingValidatedOutperformance = outperformanceRegistry
    .filter((item) => item.is_validated && !item.is_stale)
    .slice()
    .sort((left, right) => Number(right.validation_score || 0) - Number(left.validation_score || 0))[0];
  const leadingImbalanceGate =
    leadingValidatedOutperformance?.metrics_summary?.walk_forward_quality_gate || null;
  const forwardGateRecords = outperformanceRegistry.filter(
    (item) => item.metrics_summary?.benchmark_forward_promotion_gate
  );
  const forwardReadyCount = forwardGateRecords.filter(
    (item) => item.metrics_summary.benchmark_forward_promotion_gate.passed
  ).length;
  const leadingForwardGate = forwardGateRecords
    .slice()
    .sort(
      (left, right) =>
        Number(right.metrics_summary.benchmark_forward_promotion_gate.sample_count || 0)
        - Number(left.metrics_summary.benchmark_forward_promotion_gate.sample_count || 0)
    )[0]?.metrics_summary?.benchmark_forward_promotion_gate;
  const periodRows = TRADING_PERIODS.map((period) => {
    const rows = tradingRegistry
      .filter(
        (item) =>
          item.period === period &&
          item.is_validated &&
          ["production", "candidate"].includes(item.status)
      )
      .sort((left, right) => Number(right.validation_score || 0) - Number(left.validation_score || 0));
    return {
      period,
      available: rows.length,
      best: rows[0] || null,
    };
  });

  return (
    <>
      <header className="app-header">
        <div>
          <h1>{labelByMode(languageMode, "Trading Models", "\u4ea4\u6613\u6a21\u578b")}</h1>
          <p>
            {labelByMode(
              languageMode,
              "The Virtual Trader automatically chooses the model that currently matches real results best.",
              "\u865b\u64ec\u4ea4\u6613\u54e1\u6703\u81ea\u52d5\u9078\u64c7\u76ee\u524d\u6700\u8cbc\u8fd1\u5be6\u969b\u7d50\u679c\u7684\u6a21\u578b\u3002"
            )}
          </p>
        </div>
        <div className="header-controls">
          <button type="button" onClick={loadAll} disabled={isLoading}>
            {labelByMode(languageMode, "Refresh", "\u91cd\u65b0\u8f09\u5165")}
          </button>
          <button type="button" onClick={() => handleRunNow("US")} disabled={Boolean(isRunningNow)}>
            {isRunningNow === "US"
              ? labelByMode(languageMode, "Refreshing US models...", "正在更新美股模型...")
              : labelByMode(languageMode, "Refresh US 2-year models", "更新美股 2 年模型")}
          </button>
          <button type="button" onClick={() => handleRunNow("HK")} disabled={Boolean(isRunningNow)}>
            {isRunningNow === "HK"
              ? labelByMode(languageMode, "Refreshing HK models...", "正在更新港股模型...")
              : labelByMode(languageMode, "Refresh HK 2-year models", "更新港股 2 年模型")}
          </button>
        </div>
      </header>

      {error ? <div className="error-box"><p>{error}</p></div> : null}

      {showTrustExplanation ? (
      <section className={`panel ${currentValidationCount ? "" : "model-evidence-warning"}`}>
        <div className="panel-title-row">
          <h3>{labelByMode(languageMode, "Can the models be trusted now?", "目前可以信任模型嗎？")}</h3>
          <button
            type="button"
            className="section-visibility-button"
            aria-expanded="true"
            onClick={() => setShowTrustExplanation(false)}
          >
            {labelByMode(languageMode, "Hide", "隱藏")}
          </button>
        </div>
        <p>
          {currentValidationCount
            ? labelByMode(
              languageMode,
              `${currentValidationCount} model records passed the current non-overlapping accuracy, cost, stability, and drawdown checks. This is evidence, not a profit guarantee.`,
              `${currentValidationCount} 個模型記錄通過目前的非重疊準確率、成本、穩定性及回撤檢查。這是證據，並非獲利保證。`
            )
            : labelByMode(
              languageMode,
              "No loaded model has current validation evidence. The Virtual Trader will use its safety fallback instead of trusting an old score.",
              "目前沒有已載入模型具備最新驗證證據。虛擬交易員會使用安全後備規則，不會信任舊分數。"
            )}
        </p>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            "A prediction is eligible for validation as a trade only when it was larger than a separately calibrated uncertainty level. Smaller predictions become no action before the result is known.",
            "只有當預測幅度高於獨立校準的不確定性水平時，才會以交易訊號進行驗證。較小的預測會在結果公布前列為不行動。"
          )}
        </p>
        {leadingImbalanceGate ? (
          <p className="helper-text">
            {labelByMode(
              languageMode,
              `Best benchmark model (${leadingValidatedOutperformance.ticker}) in context: ${scoreText(leadingImbalanceGate.direction_accuracy)} raw accuracy versus ${scoreText(leadingImbalanceGate.naive_majority_accuracy)} from always choosing the common result. Its real lift is ${scoreText(leadingImbalanceGate.direction_edge)}, balanced accuracy is ${scoreText(leadingImbalanceGate.balanced_direction_accuracy)}, and worst-class recall is ${scoreText(leadingImbalanceGate.worst_class_recall)}.`,
              `最佳基準模型（${leadingValidatedOutperformance.ticker}）的背景：原始準確率 ${scoreText(leadingImbalanceGate.direction_accuracy)}，而總是選擇常見結果已有 ${scoreText(leadingImbalanceGate.naive_majority_accuracy)}。真正提升為 ${scoreText(leadingImbalanceGate.direction_edge)}，平衡準確率為 ${scoreText(leadingImbalanceGate.balanced_direction_accuracy)}，較難辨認類別的召回率為 ${scoreText(leadingImbalanceGate.worst_class_recall)}。`
            )}
          </p>
        ) : null}
        <p className="helper-text">
          {labelByMode(
            languageMode,
            forwardReadyCount
              ? `${forwardReadyCount} benchmark-relative model has enough profitable forward evidence for promotion review.`
              : `Live promotion check: ${leadingForwardGate?.sample_count || 0}/${leadingForwardGate?.required_sample_count || 20} matured predictions, ${leadingForwardGate?.pending_count || 0} waiting for five-day outcomes${leadingForwardGate?.estimated_next_maturity_date ? ` (earliest estimate ${leadingForwardGate.estimated_next_maturity_date})` : ""}, and ${leadingForwardGate?.active_signal_count || 0}/${leadingForwardGate?.required_active_signal_count || 5} active signals. Promotion stays locked until forward accuracy and after-cost profit checks also pass.`,
            forwardReadyCount
              ? `${forwardReadyCount} \u500b\u57fa\u6e96\u76f8\u5c0d\u6a21\u578b\u5df2\u6709\u8db3\u5920\u7684\u524d\u77bb\u7372\u5229\u8b49\u64da\u4f9b\u6649\u7d1a\u5be9\u6838\u3002`
              : `\u5be6\u6642\u6649\u7d1a\u6aa2\u67e5\uff1a${leadingForwardGate?.sample_count || 0}/${leadingForwardGate?.required_sample_count || 20} \u500b\u5df2\u5230\u671f\u9810\u6e2c\uff0c${leadingForwardGate?.pending_count || 0} \u500b\u7b49\u5f85\u4e94\u500b\u4ea4\u6613\u65e5\u7d50\u679c${leadingForwardGate?.estimated_next_maturity_date ? `\uff08\u6700\u65e9\u4f30\u8a08 ${leadingForwardGate.estimated_next_maturity_date}\uff09` : ""}\uff0c${leadingForwardGate?.active_signal_count || 0}/${leadingForwardGate?.required_active_signal_count || 5} \u500b\u4e3b\u52d5\u8a0a\u865f\u3002\u5728\u524d\u77bb\u6b63\u78ba\u7387\u53ca\u6263\u9664\u6210\u672c\u5f8c\u7684\u7372\u5229\u6aa2\u67e5\u540c\u6642\u901a\u904e\u524d\uff0c\u6649\u7d1a\u6703\u4fdd\u6301\u9396\u5b9a\u3002`
          )}
        </p>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            `Accuracy versus profit: ${outperformanceRegistry.length} benchmark-relative experiments are recorded; ${economicsPassedCount} passed actual after-cost stock-return checks. A high hit rate alone is never treated as profit evidence.`,
            `準確率與盈利：現有 ${outperformanceRegistry.length} 個相對基準實驗；其中 ${economicsPassedCount} 個通過實際扣除估算成本後的股票回報檢查。只有高命中率，絕不會被視為盈利證據。`
          )}
        </p>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            validatedOutperformanceTickers.length
              ? `Exact-ticker benchmark coverage: ${validatedOutperformanceTickers.join(", ")}. Other tickers do not borrow this evidence and continue using the safety fallback until their own model passes every gate.`
              : "Exact-ticker benchmark coverage: none. Every ticker continues using the safety fallback until its own model passes every gate.",
            validatedOutperformanceTickers.length
              ? `個別股票基準模型覆蓋：${validatedOutperformanceTickers.join("、")}。其他股票不會借用這些證據；在其本身模型通過所有檢查前，會繼續使用安全後備規則。`
              : "個別股票基準模型覆蓋：暫時沒有。每隻股票在其本身模型通過所有檢查前，都會繼續使用安全後備規則。"
          )}
        </p>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            `Shared market models loaded: ${pooledRecordCount}. They use scale-independent percentage and ratio inputs. A shared model enters automatic selection only when at least 60% of its individual tickers pass prediction and after-cost trading checks; a good combined headline is not enough.`,
            `已載入共享市場模型：${pooledRecordCount}。模型使用不受價格尺度影響的百分比及比率輸入。只有至少 60% 個別股票通過預測及扣除成本後的交易檢查，才可加入自動選擇；整體數字理想並不足夠。`
          )}
        </p>
        {legacyValidationCount ? (
          <p className="helper-text">
            {labelByMode(
              languageMode,
              `${legacyValidationCount} older records were marked validated under previous rules and now require re-evaluation.`,
              `${legacyValidationCount} 個舊記錄曾按舊規則標示為已驗證，現時需要重新評估。`
            )}
          </p>
        ) : null}
      </section>
      ) : (
        <button
          type="button"
          className="restore-section-button"
          aria-expanded="false"
          onClick={() => setShowTrustExplanation(true)}
        >
          {labelByMode(languageMode, "Show model trust explanation", "顯示模型信任說明")}
        </button>
      )}

      <section className="panel">
        <h3>{labelByMode(languageMode, "Automatic Selection", "\u81ea\u52d5\u9078\u64c7")}</h3>
        <div className="model-overview-grid">
          <div>
            <span>{labelByMode(languageMode, "System", "\u7cfb\u7d71")}</span>
            <strong>
              {status?.scheduler_started
                && hkStatus?.scheduler_started
                ? labelByMode(languageMode, "Active", "\u904b\u4f5c\u4e2d")
                : labelByMode(languageMode, "Not running", "\u672a\u904b\u4f5c")}
            </strong>
          </div>
          <div>
            <span>{labelByMode(languageMode, "Preferred example: VOO", "\u76ee\u524d\u504f\u597d\u7bc4\u4f8b\uff1aVOO")}</span>
            <strong>{preferredModel ? modelText(preferredModel.model_name) : "N/A"}</strong>
          </div>
          <div>
            <span>{labelByMode(languageMode, "Training window", "\u8a13\u7df4\u6642\u9593")}</span>
            <strong>{preferredModel?.period || "N/A"}</strong>
          </div>
          <div>
            <span>{labelByMode(languageMode, "Validation score", "\u9a57\u8b49\u5206\u6578")}</span>
            <strong>{scoreText(preferredModel?.validation_score)}</strong>
          </div>
        </div>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            "Selection is made separately for every ticker. VOO is shown only as an easy-to-read example.",
            "\u6bcf\u500b\u80a1\u7968\u4ee3\u865f\u90fd\u6703\u7368\u7acb\u9078\u64c7\u6a21\u578b\u3002VOO \u53ea\u662f\u6613\u65bc\u95b1\u8b80\u7684\u7bc4\u4f8b\u3002"
          )}
        </p>
      </section>

      <section className="panel">
        <h3>{labelByMode(languageMode, "Model Health", "模型健康狀態")}</h3>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            "Validated means current time-ordered evidence passed every gate. Fallback is reported separately and is never counted as an ML model.",
            "已驗證代表目前按時間排序的證據通過全部關卡。後備規則會獨立顯示，絕不當作機器學習模型。"
          )}
        </p>
        <div className="model-health-grid">
          {["US", "HK"].map((market) => {
            const health = modelHealth[market];
            const funnel = health?.version_funnel || {};
            const runtime = health?.runtime || {};
            const feedback = health?.feedback || {};
            return (
              <article key={market} className={health?.status === "MODEL_READY" ? "health-ready" : "health-warning"}>
                <h4>{market}</h4>
                <dl>
                  <div><dt>{labelByMode(languageMode, "Status", "狀態")}</dt><dd>{health?.status || "N/A"}</dd></div>
                  <div><dt>{labelByMode(languageMode, "Validated", "已驗證")}</dt><dd>{funnel.validated ?? "N/A"}</dd></div>
                  <div><dt>{labelByMode(languageMode, "Active", "使用中")}</dt><dd>{funnel.active ?? "N/A"}</dd></div>
                  <div><dt>{labelByMode(languageMode, "Shadow", "影子模型")}</dt><dd>{funnel.shadow ?? "N/A"}</dd></div>
                  <div><dt>{labelByMode(languageMode, "Fallback use", "後備使用率")}</dt><dd>{runtime.fallback_usage_rate == null ? "N/A" : `${(runtime.fallback_usage_rate * 100).toFixed(1)}%`}</dd></div>
                  <div><dt>{labelByMode(languageMode, "Feedback", "回饋")}</dt><dd>{feedback.evaluated_count ?? 0} / {(feedback.evaluated_count ?? 0) + (feedback.pending_count ?? 0)}</dd></div>
                  <div><dt>BUY</dt><dd>{runtime.decision_outcomes?.BUY ?? 0}</dd></div>
                  <div><dt>SELL</dt><dd>{runtime.decision_outcomes?.SELL ?? 0}</dd></div>
                  <div><dt>HOLD</dt><dd>{runtime.decision_outcomes?.HOLD ?? 0}</dd></div>
                  <div><dt>SKIP</dt><dd>{runtime.decision_outcomes?.SKIP ?? 0}</dd></div>
                </dl>
              </article>
            );
          })}
        </div>
      </section>

      <section className="panel">
        <h3>{labelByMode(languageMode, "Continuous Improvement Pipeline", "持續改進流程")}</h3>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            "New challengers are trained and tested on unseen, time-ordered data. Rejected challengers do not replace a safer current model. Five-day live outcomes then update the evidence score; they do not bypass validation.",
            "新挑戰模型會使用未見過並按時間排序的資料測試。被拒絕的挑戰模型不會取代較安全的現有模型；其後五個交易日的實際結果只會更新證據評分，不會繞過驗證。"
          )}
        </p>
        <div className="table-wrap responsive-card-table">
          <table className="static-table">
            <thead>
              <tr>
                <th>{labelByMode(languageMode, "Market", "市場")}</th>
                <th>{labelByMode(languageMode, "Latest training", "最近訓練")}</th>
                <th>{labelByMode(languageMode, "Current candidates", "現有候選模型")}</th>
                <th>{labelByMode(languageMode, "Validated", "已驗證")}</th>
                <th>{labelByMode(languageMode, "Runtime coverage", "交易覆蓋")}</th>
                <th>{labelByMode(languageMode, "5-day feedback", "五日回饋")}</th>
                <th>{labelByMode(languageMode, "Top rejection reasons", "主要拒絕原因")}</th>
              </tr>
            </thead>
            <tbody>
              {improvementRows.map((item) => {
                const reasons = Object.entries(item.top_rejection_reasons || {})
                  .slice(0, 3)
                  .map(([reason, count]) => `${reason.replaceAll("_", " ")} (${count})`)
                  .join(", ");
                return (
                  <tr key={item.market}>
                    <td data-label={labelByMode(languageMode, "Market", "市場")}><strong>{item.market}</strong></td>
                    <td data-label={labelByMode(languageMode, "Latest training", "最近訓練")}>{dateText(item.latest_training_at_utc)}</td>
                    <td data-label={labelByMode(languageMode, "Current candidates", "現有候選模型")}>{item.candidate_models ?? 0}</td>
                    <td data-label={labelByMode(languageMode, "Validated", "已驗證")}>{item.validated_models ?? 0}</td>
                    <td data-label={labelByMode(languageMode, "Runtime coverage", "交易覆蓋")}>{`${item.runtime_eligible_tickers ?? 0} exact tickers + ${item.validated_pooled_models ?? 0} validated pooled`}</td>
                    <td data-label={labelByMode(languageMode, "5-day feedback", "五日回饋")}>{`${item.feedback?.evaluated_count ?? 0} evaluated; ${item.feedback?.pending_count ?? 0} pending`}</td>
                    <td data-label={labelByMode(languageMode, "Top rejection reasons", "主要拒絕原因")}>{reasons || labelByMode(languageMode, "None recorded", "暫無記錄")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h3>{labelByMode(languageMode, "Model Performance", "模型表現")}</h3>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            "Accuracy is the median historical walk-forward direction accuracy. Prediction rate is how often the model predicted an upward move, not its chance of success. Matching rate is the sample-weighted share of matured live predictions that matched the actual direction.",
            "準確率是歷史走步驗證方向準確率的中位數。預測率是模型預測上升的比例，並非成功機率。吻合率是已到期實時預測與實際方向相符的樣本加權比例。"
          )}
        </p>
        <div className="table-wrap responsive-card-table model-performance-table">
          <table className="static-table">
            <thead>
              <tr>
                <th>{labelByMode(languageMode, "Model", "模型")}</th>
                <th>{labelByMode(languageMode, "Accuracy rate", "準確率")}</th>
                <th>{labelByMode(languageMode, "Prediction rate", "預測率")}</th>
                <th>{labelByMode(languageMode, "Matching rate", "吻合率")}</th>
                <th>{labelByMode(languageMode, "Evidence", "證據量")}</th>
              </tr>
            </thead>
            <tbody>
              {modelPerformanceRows.map((item) => (
                <tr key={item.modelName}>
                  <td data-label={labelByMode(languageMode, "Model", "模型")}>
                    <strong>{modelText(item.modelName)}</strong>
                  </td>
                  <td data-label={labelByMode(languageMode, "Accuracy rate", "準確率")}>
                    {formatModelRate(item.accuracyRate)}
                  </td>
                  <td data-label={labelByMode(languageMode, "Prediction rate", "預測率")}>
                    {formatModelRate(item.predictionRate)}
                  </td>
                  <td data-label={labelByMode(languageMode, "Matching rate", "吻合率")}>
                    {formatModelRate(item.matchingRate)}
                  </td>
                  <td data-label={labelByMode(languageMode, "Evidence", "證據量")}>
                    {labelByMode(
                      languageMode,
                      `${item.recordCount} model records; ${item.matchingSamples} matured live results`,
                      `${item.recordCount} 個模型記錄；${item.matchingSamples} 個已到期實時結果`
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h3>{labelByMode(languageMode, "Three Views of the Market", "\u4e09\u7a2e\u5e02\u5834\u8996\u89d2")}</h3>
        <div className="table-wrap responsive-card-table">
          <table className="static-table">
            <thead>
              <tr>
                <th>{labelByMode(languageMode, "History", "\u6b77\u53f2\u9577\u5ea6")}</th>
                <th>{labelByMode(languageMode, "Purpose", "\u7528\u9014")}</th>
                <th>{labelByMode(languageMode, "Refresh", "\u66f4\u65b0")}</th>
                <th>{labelByMode(languageMode, "Validated models", "\u5df2\u9a57\u8b49\u6a21\u578b")}</th>
                <th>{labelByMode(languageMode, "Best available", "\u6700\u4f73\u53ef\u7528\u6a21\u578b")}</th>
              </tr>
            </thead>
            <tbody>
              {periodRows.map((row) => (
                <tr key={row.period}>
                  <td data-label={labelByMode(languageMode, "History", "歷史長度")}><strong>{row.period}</strong></td>
                  <td data-label={labelByMode(languageMode, "Purpose", "用途")}>
                    {row.period === "2y"
                      ? labelByMode(languageMode, "Recent market behaviour", "\u8fd1\u671f\u5e02\u5834\u8b8a\u5316")
                      : row.period === "5y"
                      ? labelByMode(languageMode, "Medium-term balance", "\u4e2d\u671f\u5e02\u5834\u5e73\u8861")
                      : labelByMode(languageMode, "Long-term stability", "\u9577\u671f\u5e02\u5834\u7a69\u5b9a\u6027")}
                  </td>
                  <td data-label={labelByMode(languageMode, "Refresh", "更新")}>
                    {row.period === "2y"
                      ? labelByMode(languageMode, "Daily", "\u6bcf\u65e5")
                      : row.period === "5y"
                      ? labelByMode(languageMode, "Weekly", "\u6bcf\u9031")
                      : labelByMode(languageMode, "Monthly", "\u6bcf\u6708")}
                  </td>
                  <td data-label={labelByMode(languageMode, "Validated models", "已驗證模型")}>{row.available}</td>
                  <td data-label={labelByMode(languageMode, "Best available", "最佳可用模型")}>
                    {row.best
                      ? `${row.best.market} ${row.best.ticker}: ${modelText(row.best.model_name)} (${scoreText(row.best.validation_score)})`
                      : labelByMode(languageMode, "Waiting for training", "\u7b49\u5f85\u8a13\u7df4")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            "This counts validated trading models across the loaded market registry, not only VOO.",
            "\u9019\u88e1\u7d71\u8a08\u5df2\u8f09\u5165\u5e02\u5834\u767b\u8a18\u4e2d\u7684\u5df2\u9a57\u8b49\u4ea4\u6613\u6a21\u578b\uff0c\u4e0d\u53ea\u662f VOO\u3002"
          )}
        </p>
      </section>

      <section className="panel">
        <h3>{labelByMode(languageMode, "Recent Model Updates", "\u6700\u8fd1\u6a21\u578b\u66f4\u65b0")}</h3>
        <div className="table-wrap responsive-card-table">
          <table className="static-table">
            <thead>
              <tr>
                <th>{labelByMode(languageMode, "Time", "\u6642\u9593")}</th>
                <th>{labelByMode(languageMode, "Market", "市場")}</th>
                <th>{labelByMode(languageMode, "Update type", "\u66f4\u65b0\u985e\u578b")}</th>
                <th>{labelByMode(languageMode, "Result", "\u7d50\u679c")}</th>
                <th>{labelByMode(languageMode, "Tickers", "\u80a1\u7968\u6578\u91cf")}</th>
                <th>{labelByMode(languageMode, "Validated / rejected", "通過／拒絕")}</th>
              </tr>
            </thead>
            <tbody>
              {runs.length ? runs.map((item) => (
                <tr key={item.id}>
                  <td data-label={labelByMode(languageMode, "Time", "時間")}>{dateText(item.started_at_utc)}</td>
                  <td data-label={labelByMode(languageMode, "Market", "市場")}>{item.details?.market || "US"}</td>
                  <td data-label={labelByMode(languageMode, "Update type", "更新類型")}>{workflowText(item.run_type, languageMode)}</td>
                  <td data-label={labelByMode(languageMode, "Result", "結果")}>{statusText(item.status, languageMode)}</td>
                  <td data-label={labelByMode(languageMode, "Tickers", "股票數量")}>{item.processed_tickers}</td>
                  <td data-label={labelByMode(languageMode, "Validated / rejected", "通過／拒絕")}>{`${item.details?.validated_models ?? 0} / ${item.details?.rejected_models ?? 0}`}</td>
                </tr>
              )) : (
                <tr>
                  <td colSpan={6}>
                    {labelByMode(languageMode, "No model updates recorded yet.", "\u76ee\u524d\u5c1a\u672a\u6709\u6a21\u578b\u66f4\u65b0\u8a18\u9304\u3002")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <button type="button" onClick={() => setShowAdvanced((value) => !value)}>
          {showAdvanced
            ? labelByMode(languageMode, "Hide technical details", "\u96b1\u85cf\u6280\u8853\u8cc7\u6599")
            : labelByMode(languageMode, "Show technical details", "\u986f\u793a\u6280\u8853\u8cc7\u6599")}
        </button>

        {showAdvanced ? (
          <>
            <h4>{labelByMode(languageMode, "Automatic repair signals", "\u81ea\u52d5\u4fee\u5fa9\u8a0a\u865f")}</h4>
            {(status?.active_triggers || []).length ? (
              <ul className="bullet-list">
                {status.active_triggers.map((item) => <li key={item}>{item}</li>)}
              </ul>
            ) : (
              <p className="helper-text">
                {labelByMode(languageMode, "No model problems detected.", "\u672a\u5075\u6e2c\u5230\u6a21\u578b\u554f\u984c\u3002")}
              </p>
            )}

            <h4>{labelByMode(languageMode, "Trading model registry", "\u4ea4\u6613\u6a21\u578b\u767b\u8a18\u8868")}</h4>
            <div className="table-wrap responsive-card-table model-registry-table">
              <table className="static-table">
                <thead>
                  <tr>
                    <th>{labelByMode(languageMode, "Market", "市場")}</th>
                    <th>Ticker</th>
                    <th>{labelByMode(languageMode, "History", "\u6b77\u53f2\u9577\u5ea6")}</th>
                    <th>{labelByMode(languageMode, "Model", "\u6a21\u578b")}</th>
                    <th>{labelByMode(languageMode, "State", "\u72c0\u614b")}</th>
                    <th>{labelByMode(languageMode, "Score", "\u5206\u6578")}</th>
                    <th>{labelByMode(languageMode, "Accuracy rate", "準確率")}</th>
                    <th>{labelByMode(languageMode, "Prediction rate", "預測率")}</th>
                    <th>{labelByMode(languageMode, "Matching rate", "吻合率")}</th>
                    <th>{labelByMode(languageMode, "Updated", "\u66f4\u65b0")}</th>
                  </tr>
                </thead>
                <tbody>
                  {tradingRegistry.slice(0, 80).map((item) => (
                    <tr key={`${item.market}-${item.ticker}-${item.period}-${item.model_name}`}>
                      <td data-label={labelByMode(languageMode, "Market", "市場")}>{item.market}</td>
                      <td data-label="Ticker">{item.ticker}</td>
                      <td data-label={labelByMode(languageMode, "History", "歷史長度")}>{item.period}</td>
                      <td data-label={labelByMode(languageMode, "Model", "模型")}>{modelText(item.model_name)}</td>
                      <td data-label={labelByMode(languageMode, "State", "狀態")}>{item.status}{item.is_stale ? " (stale)" : ""}</td>
                      <td data-label={labelByMode(languageMode, "Score", "分數")}>{scoreText(item.validation_score)}</td>
                      <td data-label={labelByMode(languageMode, "Accuracy rate", "準確率")}>{formatModelRate(historicalAccuracy(item))}</td>
                      <td data-label={labelByMode(languageMode, "Prediction rate", "預測率")}>{formatModelRate(predictionRate(item))}</td>
                      <td data-label={labelByMode(languageMode, "Matching rate", "吻合率")}>{formatModelRate(liveMatchingRate(item))}</td>
                      <td data-label={labelByMode(languageMode, "Updated", "更新")}>{dateText(item.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : null}
      </section>
    </>
  );
}

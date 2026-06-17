import React, { useEffect, useMemo, useState } from "react";

import {
  fetchModelLifecycleRegistry,
  fetchModelLifecycleRuns,
  fetchModelLifecycleStatus,
  runModelLifecycleNow,
} from "../api";

const TRADING_PERIODS = ["2y", "5y", "10y"];
const TRADING_MODELS = ["linear_regression", "random_forest", "gradient_boosting"];

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
  const [registry, setRegistry] = useState([]);
  const [runs, setRuns] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isRunningNow, setIsRunningNow] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [error, setError] = useState("");

  async function loadAll() {
    setIsLoading(true);
    setError("");
    const results = await Promise.allSettled([
      fetchModelLifecycleStatus("VOO", "2y", "target_5d_return", 6),
      fetchModelLifecycleRegistry(1000, { targetName: "target_5d_return" }),
      fetchModelLifecycleRuns(8),
    ]);

    const [statusResult, registryResult, runsResult] = results;
    setStatus(statusResult.status === "fulfilled" ? statusResult.value : null);
    setRegistry(registryResult.status === "fulfilled" ? registryResult.value || [] : []);
    setRuns(runsResult.status === "fulfilled" ? runsResult.value || [] : []);

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

  async function handleRunNow() {
    setIsRunningNow(true);
    setError("");
    try {
      await runModelLifecycleNow("daily_incremental", "manual_dashboard_run");
      await loadAll();
    } catch (requestError) {
      setError(requestError.message || "Failed to refresh the 2-year models.");
    } finally {
      setIsRunningNow(false);
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

  const rankedForVoo = useMemo(
    () =>
      tradingRegistry
        .filter(
          (item) =>
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
          <button type="button" onClick={handleRunNow} disabled={isRunningNow}>
            {isRunningNow
              ? labelByMode(languageMode, "Refreshing models...", "\u6b63\u5728\u66f4\u65b0\u6a21\u578b...")
              : labelByMode(languageMode, "Refresh 2-year models", "\u66f4\u65b0 2 \u5e74\u6a21\u578b")}
          </button>
        </div>
      </header>

      {error ? <div className="error-box"><p>{error}</p></div> : null}

      <section className="panel">
        <h3>{labelByMode(languageMode, "Automatic Selection", "\u81ea\u52d5\u9078\u64c7")}</h3>
        <div className="model-overview-grid">
          <div>
            <span>{labelByMode(languageMode, "System", "\u7cfb\u7d71")}</span>
            <strong>
              {status?.scheduler_started
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
        <h3>{labelByMode(languageMode, "Three Views of the Market", "\u4e09\u7a2e\u5e02\u5834\u8996\u89d2")}</h3>
        <div className="table-wrap">
          <table>
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
                  <td><strong>{row.period}</strong></td>
                  <td>
                    {row.period === "2y"
                      ? labelByMode(languageMode, "Recent market behaviour", "\u8fd1\u671f\u5e02\u5834\u8b8a\u5316")
                      : row.period === "5y"
                      ? labelByMode(languageMode, "Medium-term balance", "\u4e2d\u671f\u5e02\u5834\u5e73\u8861")
                      : labelByMode(languageMode, "Long-term stability", "\u9577\u671f\u5e02\u5834\u7a69\u5b9a\u6027")}
                  </td>
                  <td>
                    {row.period === "2y"
                      ? labelByMode(languageMode, "Daily", "\u6bcf\u65e5")
                      : row.period === "5y"
                      ? labelByMode(languageMode, "Weekly", "\u6bcf\u9031")
                      : labelByMode(languageMode, "Monthly", "\u6bcf\u6708")}
                  </td>
                  <td>{row.available}</td>
                  <td>
                    {row.best
                      ? `${row.best.ticker}: ${modelText(row.best.model_name)} (${scoreText(row.best.validation_score)})`
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
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{labelByMode(languageMode, "Time", "\u6642\u9593")}</th>
                <th>{labelByMode(languageMode, "Update type", "\u66f4\u65b0\u985e\u578b")}</th>
                <th>{labelByMode(languageMode, "Result", "\u7d50\u679c")}</th>
                <th>{labelByMode(languageMode, "Tickers", "\u80a1\u7968\u6578\u91cf")}</th>
                <th>{labelByMode(languageMode, "Models ready", "\u5b8c\u6210\u6a21\u578b")}</th>
              </tr>
            </thead>
            <tbody>
              {runs.length ? runs.map((item) => (
                <tr key={item.id}>
                  <td>{dateText(item.started_at_utc)}</td>
                  <td>{workflowText(item.run_type, languageMode)}</td>
                  <td>{statusText(item.status, languageMode)}</td>
                  <td>{item.processed_tickers}</td>
                  <td>{item.successful_models}</td>
                </tr>
              )) : (
                <tr>
                  <td colSpan={5}>
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
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th>{labelByMode(languageMode, "History", "\u6b77\u53f2\u9577\u5ea6")}</th>
                    <th>{labelByMode(languageMode, "Model", "\u6a21\u578b")}</th>
                    <th>{labelByMode(languageMode, "State", "\u72c0\u614b")}</th>
                    <th>{labelByMode(languageMode, "Score", "\u5206\u6578")}</th>
                    <th>{labelByMode(languageMode, "Updated", "\u66f4\u65b0")}</th>
                  </tr>
                </thead>
                <tbody>
                  {tradingRegistry.slice(0, 80).map((item) => (
                    <tr key={`${item.ticker}-${item.period}-${item.model_name}`}>
                      <td>{item.ticker}</td>
                      <td>{item.period}</td>
                      <td>{modelText(item.model_name)}</td>
                      <td>{item.status}{item.is_stale ? " (stale)" : ""}</td>
                      <td>{scoreText(item.validation_score)}</td>
                      <td>{dateText(item.updated_at)}</td>
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

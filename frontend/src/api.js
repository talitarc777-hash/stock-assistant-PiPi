import { requestJson } from "./services/httpClient";

async function fetchJson(path, options = {}) {
  return requestJson(path, {
    method: "GET",
    ...options,
  });
}

async function postJson(path, body, options = {}) {
  return requestJson(path, {
    method: "POST",
    body: body || {},
    retries: 0,
    ...options,
  });
}

export async function fetchWatchlistAnalyze(tickers, period = "5y") {
  const joined = encodeURIComponent(tickers.join(","));
  // Batch analysis can be slow on cold-start (multiple tickers + indicator computation).
  return fetchJson(`/watchlist-analyze?tickers=${joined}&period=${period}`, { timeoutMs: 45000, retries: 2 });
}

export async function fetchAnalyze(ticker, period = "5y") {
  return fetchJson(`/analyze?ticker=${encodeURIComponent(ticker)}&period=${period}`, { timeoutMs: 14000 });
}

export async function fetchChartData(ticker, period = "5y") {
  return fetchJson(`/chart-data?ticker=${encodeURIComponent(ticker)}&period=${period}`, {
    timeoutMs: 18000,
    retries: 1,
  });
}

export async function fetchLiveMarketSnapshot(ticker, period = "3mo") {
  return fetchJson(
    `/market-data/live-snapshot?ticker=${encodeURIComponent(ticker)}&period=${encodeURIComponent(period)}`,
    { timeoutMs: 18000, retries: 1 }
  );
}

export async function fetchForecast(ticker, period = "2y") {
  return fetchJson(`/forecast?ticker=${encodeURIComponent(ticker)}&period=${period}`, { timeoutMs: 14000 });
}

export async function fetchModelLatest(
  ticker,
  period = "5y",
  targetName = "target_5d_updown",
  modelName = "logistic_regression",
  userId = null
) {
  const userQuery = userId ? `&user_id=${encodeURIComponent(userId)}` : "";
  return fetchJson(
    `/model-latest?ticker=${encodeURIComponent(ticker)}&period=${period}&target_name=${encodeURIComponent(
      targetName
    )}&model_name=${encodeURIComponent(modelName)}${userQuery}`
  );
}

export async function fetchModelHistory(
  ticker,
  period = "5y",
  targetName = "target_5d_updown",
  modelName = "logistic_regression",
  limit = 200,
  userId = null
) {
  const userQuery = userId ? `&user_id=${encodeURIComponent(userId)}` : "";
  return fetchJson(
    `/model-history?ticker=${encodeURIComponent(ticker)}&period=${period}&target_name=${encodeURIComponent(
      targetName
    )}&model_name=${encodeURIComponent(modelName)}&limit=${limit}${userQuery}`
  );
}

export async function fetchModelAccuracy(
  ticker,
  period = "5y",
  targetName = "target_5d_updown",
  modelName = "logistic_regression",
  window = 20,
  userId = null
) {
  const userQuery = userId ? `&user_id=${encodeURIComponent(userId)}` : "";
  return fetchJson(
    `/model-accuracy?ticker=${encodeURIComponent(ticker)}&period=${period}&target_name=${encodeURIComponent(
      targetName
    )}&model_name=${encodeURIComponent(modelName)}&window=${window}${userQuery}`
  );
}

export async function fetchVirtualTraderSummary(
  ticker,
  period = "5y",
  modelName = null,
  equityLimit = 300,
  userId = null
) {
  const userQuery = userId ? `&user_id=${encodeURIComponent(userId)}` : "";
  const modelQuery = modelName ? `&model_name=${encodeURIComponent(modelName)}` : "";
  return fetchJson(
    `/virtual-trader-summary?ticker=${encodeURIComponent(ticker)}&period=${period}${modelQuery}&equity_limit=${equityLimit}${userQuery}`
  );
}

export async function fetchVirtualTraderTrades(
  ticker,
  period = "5y",
  modelName = null,
  limit = 120,
  userId = null
) {
  const userQuery = userId ? `&user_id=${encodeURIComponent(userId)}` : "";
  const modelQuery = modelName ? `&model_name=${encodeURIComponent(modelName)}` : "";
  return fetchJson(
    `/virtual-trader-trades?ticker=${encodeURIComponent(ticker)}&period=${period}${modelQuery}&limit=${limit}${userQuery}`
  );
}

export async function fetchLiveVirtualTraderStatus(
  userId,
  ticker = null,
  modelName = null,
  autoRun = false
) {
  const tickerQuery = ticker ? `&ticker=${encodeURIComponent(ticker)}` : "";
  const modelQuery = modelName ? `&model_name=${encodeURIComponent(modelName)}` : "";
  return fetchJson(
    `/virtual-trader/live-status?user_id=${encodeURIComponent(userId)}${tickerQuery}${modelQuery}&auto_run=${autoRun ? "true" : "false"}`,
    { timeoutMs: 15000, retries: 1 }
  );
}

export async function runLiveVirtualTraderNow(userId, tickers = null, modelName = null) {
  return postJson("/virtual-trader/run-now", {
    user_id: userId,
    tickers,
    model_name: modelName,
  });
}

export async function fetchLiveVirtualTraderTrades(userId, ticker = null, limit = 50) {
  const tickerQuery = ticker ? `&ticker=${encodeURIComponent(ticker)}` : "";
  return fetchJson(
    `/virtual-trader/live-trades?user_id=${encodeURIComponent(userId)}${tickerQuery}&limit=${limit}`,
    { timeoutMs: 15000, retries: 1 }
  );
}

export async function fetchTraderSchedulerStatus(recentHours = 24) {
  return fetchJson(
    `/virtual-trader/scheduler-status?recent_hours=${encodeURIComponent(recentHours)}`,
    { timeoutMs: 10000, retries: 1 }
  );
}

export async function fetchNewsSentimentLatest(ticker, period = "6mo") {
  return fetchJson(`/news-sentiment/latest?ticker=${encodeURIComponent(ticker)}&period=${period}`);
}

export async function fetchVirtualAccountHistory(userId, limit = 120, offset = 0) {
  return fetchJson(
    `/virtual-account/history?user_id=${encodeURIComponent(userId)}&limit=${limit}&offset=${offset}`,
    { timeoutMs: 14000, retries: 1 }
  );
}

export async function fetchVirtualAccountRecentTrades(userId, limit = 20) {
  return fetchJson(`/virtual-account/recent-trades?user_id=${encodeURIComponent(userId)}&limit=${limit}`, {
    timeoutMs: 12000,
    retries: 1,
  });
}

export async function postVirtualAccountDeposit(userId, amount, reason = "") {
  return postJson("/virtual-account/deposit", {
    user_id: userId,
    amount,
    reason,
    source: "web",
  });
}

export async function postVirtualAccountWithdraw(userId, amount, reason = "") {
  return postJson("/virtual-account/withdraw", {
    user_id: userId,
    amount,
    reason,
    source: "web",
  });
}

export async function postVirtualAccountReset(userId, resetMonthlyContributions = true) {
  return postJson("/virtual-account/reset", {
    user_id: userId,
    confirm_reset: true,
    reset_monthly_contributions: Boolean(resetMonthlyContributions),
  });
}

export async function fetchModelLifecycleStatus(
  ticker = "VOO",
  period = "5y",
  targetName = "target_5d_updown",
  logLimit = 8
) {
  return fetchJson(
    `/model-lifecycle/status?ticker=${encodeURIComponent(ticker)}&period=${encodeURIComponent(
      period
    )}&target_name=${encodeURIComponent(targetName)}&log_limit=${encodeURIComponent(logLimit)}`
  );
}

export async function fetchModelLifecycleRegistry(limit = 200) {
  return fetchJson(`/model-lifecycle/registry?limit=${encodeURIComponent(limit)}`);
}

export async function fetchModelLifecycleRuns(limit = 20) {
  return fetchJson(`/model-lifecycle/runs?limit=${encodeURIComponent(limit)}`);
}

export async function runModelLifecycleNow(
  workflowType = "daily_incremental",
  triggerReason = "manual_trigger",
  tickers = null
) {
  return postJson("/model-lifecycle/run-now", {
    workflow_type: workflowType,
    trigger_reason: triggerReason,
    tickers,
  });
}

import React, { useEffect, useMemo, useState } from "react";

import {
  fetchTraderSchedulerStatus,
  fetchLiveVirtualTraderStatus,
  fetchVirtualAccountHistory,
  fetchVirtualAccountRecentTrades,
  fetchVirtualTraderTrades,
  fetchVirtualTraderSummary,
  postVirtualAccountDeposit,
  postVirtualAccountWithdraw,
  runLiveVirtualTraderNow,
} from "../api";
import AccountSummaryCards from "../components/AccountSummaryCards";
import { getLabel } from "../constants/i18n";
import EquityChart from "../components/EquityChart";
import HoldingsTable from "../components/HoldingsTable";
import LineChart from "../components/LineChart";
import MonthlyContributionInput from "../components/MonthlyContributionInput";
import NewsSentimentPanel from "../components/NewsSentimentPanel";
import RecentRunsPanel from "../components/RecentRunsPanel";
import RecentTradesTable from "../components/RecentTradesTable";
import ResetTradingAccountButton from "../components/ResetTradingAccountButton";
import TransactionHistoryTable from "../components/TransactionHistoryTable";
import { fetchModelEvaluationSettings } from "../services/modelSettingsApi";

const DEFAULT_PERIOD = "5y";
const DEFAULT_MODEL = "logistic_regression";
const HISTORY_PAGE_SIZE = 120;
const SCHEDULER_REFRESH_MS = 60000;

const ZH = {
  virtualTrader: "\u865b\u64ec\u4ea4\u6613\u54e1",
  intro:
    "\u5373\u6642\u6a21\u5f0f\u6703\u4f7f\u7528\u6700\u65b0\u6a21\u578b\u8f38\u51fa\u8207\u5e02\u5834\u8cc7\u6599\uff1b\u6b77\u53f2\u6a21\u5f0f\u5247\u7528\u65bc\u56de\u653e\u8207\u6bd4\u8f03\u3002",
  ticker: "\u80a1\u7968\u4ee3\u865f",
  model: "\u6a21\u578b",
  running: "\u57f7\u884c\u4e2d...",
  runNow: "\u7acb\u5373\u57f7\u884c\u865b\u64ec\u4ea4\u6613",
  loading: "\u8f09\u5165\u4e2d...",
  liveStatusTitle: "\u5373\u6642\u72c0\u614b\uff08\u8fd1\u5373\u6642\u6a21\u64ec\uff09",
  simulationOnly: "\u53ea\u5c6c\u6a21\u64ec\uff0c\u4e0d\u6703\u767c\u9001\u771f\u5be6\u4e0b\u55ae\u3002",
  delayedDataWarning:
    "\u5e02\u5834\u8207\u65b0\u805e\u8cc7\u6599\u70ba\u300c\u8fd1\u5373\u6642\u300d\u8cc7\u6599\uff0c\u53ef\u80fd\u5b58\u5728\u4f9b\u61c9\u5546\u5ef6\u9072\uff0c\u4e26\u975e\u4ea4\u6613\u6240\u7b49\u7d1a\u5be6\u6642\u4e32\u6d41\u3002",
  universeSize: "\u5e02\u5834\u76e3\u63a7\u6578\u91cf",
  tickersEvaluated: "\u5df2\u8a55\u4f30\u80a1\u7968",
  tickersFailed: "\u8a55\u4f30\u5931\u6557\u80a1\u7968",
  fallbackDecisions: "\u5099\u63f4\u7b56\u7565\u6b21\u6578",
  cash: "\u73fe\u91d1",
  holdingsValue: "\u6301\u5009\u5e02\u503c",
  totalEquity: "\u7e3d\u8cc7\u7522",
  realizedPnl: "\u5df2\u5be6\u73fe\u640d\u76ca",
  unrealizedPnl: "\u672a\u5be6\u73fe\u640d\u76ca",
  netDeposits: "\u6de8\u5165\u91d1",
  appliedContributions: "\u5df2\u5957\u7528\u6ce8\u8cc7",
  generatedAt: "\u751f\u6210\u6642\u9593",
  noLiveStatus: "\u76ee\u524d\u6c92\u6709\u5373\u6642\u72c0\u614b\u3002",
  tradingAccount: "\u4ea4\u6613\u5e33\u6236",
  totalAccountValue: "\u7e3d\u5e33\u6236\u50f9\u503c",
  amountUsd: "\u91d1\u984d\uff08USD\uff09",
  deposit: "\u65b0\u589e\u5165\u91d1",
  withdraw: "\u65b0\u589e\u63d0\u6b3e",
  currentHoldings: "\u76ee\u524d\u6301\u5009",
  quantity: "\u6578\u91cf",
  entry: "\u5165\u5834\u50f9",
  current: "\u73fe\u50f9",
  value: "\u5e02\u503c",
  noHoldings: "\u76ee\u524d\u6c92\u6709\u6301\u5009\u3002",
  latestDecisions: "\u6700\u65b0\u6c7a\u7b56",
  action: "\u52d5\u4f5c",
  price: "\u50f9\u683c",
  reason: "\u539f\u56e0",
  latestTrades: "\u6700\u65b0\u6a21\u64ec\u4ea4\u6613",
  latestReason: "\u6700\u65b0\u6c7a\u7b56\u8aaa\u660e",
  noDecision: "\u672a\u9078\u64c7\u4ea4\u6613\u8a18\u9304\u3002",
  technicalState: "\u6280\u8853\u72c0\u614b",
  newsSentiment: "\u65b0\u805e\u60c5\u7dd2",
  benchmarkStrength: "\u76f8\u5c0d\u57fa\u6e96\u5f37\u5ea6",
  confidence: "\u4fe1\u5fc3",
  historicalMode: "\u6b77\u53f2\u56de\u653e\u6a21\u5f0f",
  historicalIntro:
    "\u6b77\u53f2\u8996\u5716\u7528\u65bc\u8a55\u4f30\uff0c\u4e0a\u65b9\u7684\u5373\u6642\u6a21\u5f0f\u624d\u662f\u7576\u4e0b\u6a21\u64ec\u4ea4\u6613\u3002",
  monthlyContributionHistory: "\u6bcf\u6708\u6ce8\u8cc7\u7d00\u9304",
  totalContributions: "\u7d2f\u7a4d\u6ce8\u8cc7\u91d1\u984d",
  monthlyContribution: "\u7576\u6708\u6ce8\u8cc7\u91d1\u984d",
  noData: "\u6c92\u6709\u53ef\u7528\u8cc7\u6599",
  depositHint: "\u5165\u91d1\u6703\u4ee5\u65b0\u7684\u4e0d\u53ef\u4fee\u6539\u5206\u985e\u5e33\u4e8b\u4ef6\u8a18\u9304\u3002",
  withdrawHint: "\u63d0\u6b3e\u6703\u4ee5\u65b0\u7684\u4e0d\u53ef\u4fee\u6539\u5206\u985e\u5e33\u4e8b\u4ef6\u8a18\u9304\u3002",
};

function labelByMode(mode, en, zh) {
  if (mode === "zh") return zh;
  if (mode === "en") return en;
  return `${en} / ${zh}`;
}

function toNumeric(value) {
  if (value === null || value === undefined) return Number.NaN;
  const num = Number(value);
  return Number.isFinite(num) ? num : Number.NaN;
}

function formatMoney(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "N/A";
  return numeric.toFixed(2);
}

export default function VirtualTraderPage({ languageMode, currentWatchlist, profileId }) {
  const [selectedTicker, setSelectedTicker] = useState(currentWatchlist[0] || "VOO");
  const [selectedModelName, setSelectedModelName] = useState(DEFAULT_MODEL);
  const [modelSettingsLoaded, setModelSettingsLoaded] = useState(false);
  const [liveStatus, setLiveStatus] = useState(null);
  const [schedulerStatus, setSchedulerStatus] = useState(null);
  const [accountSummary, setAccountSummary] = useState(null);
  const [accountHoldings, setAccountHoldings] = useState([]);
  const [accountHistory, setAccountHistory] = useState([]);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyEnabled, setHistoryEnabled] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [recentTrades, setRecentTrades] = useState([]);
  const [equityCurve, setEquityCurve] = useState([]);
  const [liveDecisionLog, setLiveDecisionLog] = useState([]);
  const [cashAmount, setCashAmount] = useState("");
  const [cashReason, setCashReason] = useState("");
  const [selectedLiveTrade, setSelectedLiveTrade] = useState(null);
  const [historicalSummary, setHistoricalSummary] = useState(null);
  const [historicalContributionData, setHistoricalContributionData] = useState(null);
  const [historicalLoading, setHistoricalLoading] = useState(false);
  const [historicalEnabled, setHistoricalEnabled] = useState(false);
  const [newsEnabled, setNewsEnabled] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isRunningNow, setIsRunningNow] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!currentWatchlist.length) return;
    if (!currentWatchlist.includes(selectedTicker)) {
      setSelectedTicker(currentWatchlist[0]);
    }
  }, [currentWatchlist.join(","), selectedTicker]);

  useEffect(() => {
    let isActive = true;
    setModelSettingsLoaded(false);
    async function loadModelSettings() {
      try {
        const settings = await fetchModelEvaluationSettings(profileId);
        if (!isActive) return;
        setSelectedModelName(settings.selected_model_name || DEFAULT_MODEL);
      } catch {
        if (!isActive) return;
        setSelectedModelName(DEFAULT_MODEL);
      } finally {
        if (isActive) setModelSettingsLoaded(true);
      }
    }
    if (profileId) {
      loadModelSettings();
    } else {
      setModelSettingsLoaded(true);
    }
    return () => {
      isActive = false;
    };
  }, [profileId]);

  function applyLiveStatusPayload(payload) {
    if (!payload) return;
    const account = payload.account || {};
    const holdings = (payload.holdings || []).map((holding) => {
      const marketValue = Number(holding.market_value);
      const unrealizedPnl = Number(holding.unrealized_pnl);
      const costBasis = marketValue - unrealizedPnl;
      const unrealizedPnlPct =
        Number.isFinite(costBasis) && Math.abs(costBasis) > 0.000001
          ? (unrealizedPnl / costBasis) * 100
          : null;
      return {
        ...holding,
        unrealized_pnl_pct: holding.unrealized_pnl_pct ?? unrealizedPnlPct,
      };
    });
    const latestDecisions = payload.latest_decisions || [];

    setLiveStatus(payload);
    setAccountSummary({
      user_id: payload.user_id,
      as_of: payload.generated_at_utc,
      last_updated: account.snapshot_timestamp || payload.generated_at_utc,
      curve_last_point_timestamp: account.curve_last_point_timestamp,
      cash: account.cash,
      holdings_value: account.holdings_value,
      total_account_value: account.total_equity,
      realized_pnl: account.realized_pnl,
      unrealized_pnl: account.unrealized_pnl,
      net_deposits: account.net_deposits ?? account.total_contributions_applied,
      holdings,
      latest_prices: {},
    });
    setAccountHoldings(holdings);
    setEquityCurve(payload.equity_curve || []);
    setLiveDecisionLog(latestDecisions);
    setSelectedLiveTrade(latestDecisions[0] || null);
  }

  async function loadGlobalViews() {
    if (!profileId || !modelSettingsLoaded) return;
    setIsLoading(true);
    setError("");
    try {
      // Keep initial render lightweight for low-resource deployments:
      // load core profile-level cards first; ticker-specific replay data is loaded separately.
      const [liveStatusResult, schedulerStatusResult, recentTradesResult] = await Promise.allSettled([
        fetchLiveVirtualTraderStatus(profileId, null, selectedModelName, false),
        fetchTraderSchedulerStatus(24),
        fetchVirtualAccountRecentTrades(profileId, 20),
      ]);

      if (schedulerStatusResult.status === "fulfilled") setSchedulerStatus(schedulerStatusResult.value);
      if (liveStatusResult.status === "fulfilled") applyLiveStatusPayload(liveStatusResult.value);
      if (recentTradesResult.status === "fulfilled") setRecentTrades(recentTradesResult.value.trades || []);

      if (liveStatusResult.status === "rejected") {
        setError(liveStatusResult.reason?.message || "Failed to load live trader status. You can retry below.");
      }
    } catch (requestError) {
      setError(requestError.message || "Failed to load virtual trader views.");
    } finally {
      setIsLoading(false);
    }
  }

  async function loadTickerSpecificViews(activeTicker = selectedTicker) {
    if (!profileId || !activeTicker || !historicalEnabled) return;
    await loadHistoricalReplayData(activeTicker);
  }

  async function loadAccountHistoryPage({ reset = false } = {}) {
    if (!profileId) return;
    setHistoryLoading(true);
    setHistoryError("");
    try {
      const nextOffset = reset ? 0 : historyOffset;
      const payload = await fetchVirtualAccountHistory(profileId, HISTORY_PAGE_SIZE, nextOffset);
      const newEvents = payload?.events || [];
      setAccountHistory((current) => (reset ? newEvents : [...current, ...newEvents]));
      setHistoryOffset(nextOffset + newEvents.length);
      setHistoryHasMore(Boolean(payload?.has_more));
    } catch (requestError) {
      setHistoryError(requestError.message || "Failed to load account history.");
    } finally {
      setHistoryLoading(false);
    }
  }

  async function loadHistoricalReplayData(activeTicker = selectedTicker) {
    if (!profileId || !activeTicker) return;
    setHistoricalLoading(true);
    try {
      const [historicalSummaryPayload, historicalTradesPayload] = await Promise.all([
        fetchVirtualTraderSummary(activeTicker, DEFAULT_PERIOD, selectedModelName, 300, profileId),
        fetchVirtualTraderTrades(activeTicker, DEFAULT_PERIOD, selectedModelName, 120, profileId),
      ]);
      setHistoricalSummary(historicalSummaryPayload);
      setHistoricalContributionData(historicalTradesPayload);
    } catch (requestError) {
      setError(requestError.message || "Failed to load historical replay section.");
    } finally {
      setHistoricalLoading(false);
    }
  }

  async function loadSchedulerStatusOnly() {
    try {
      const payload = await fetchTraderSchedulerStatus(24);
      setSchedulerStatus(payload);
    } catch {
      setSchedulerStatus(null);
    }
  }

  useEffect(() => {
    if (!modelSettingsLoaded) return;
    loadGlobalViews();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId, selectedModelName, modelSettingsLoaded]);

  useEffect(() => {
    if (!historyEnabled || !modelSettingsLoaded) return;
    loadAccountHistoryPage({ reset: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId, historyEnabled, modelSettingsLoaded]);

  useEffect(() => {
    if (!modelSettingsLoaded) return;
    loadTickerSpecificViews(selectedTicker);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId, selectedModelName, selectedTicker, historicalEnabled, modelSettingsLoaded]);

  useEffect(() => {
    if (!profileId) return undefined;
    const timer = window.setInterval(() => {
      if (!document.hidden) {
        loadSchedulerStatusOnly();
      }
    }, SCHEDULER_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [profileId]);

  async function handleRunNow() {
    if (!profileId) return;
    setIsRunningNow(true);
    setError("");
    try {
      await runLiveVirtualTraderNow(profileId, null, selectedModelName);
      await loadGlobalViews();
      await loadTickerSpecificViews(selectedTicker);
    } catch (requestError) {
      setError(requestError.message || "Failed to run live virtual trader now.");
    } finally {
      setIsRunningNow(false);
    }
  }

  async function handleDeposit() {
    if (!profileId || !cashAmount) return;
    setError("");
    try {
      await postVirtualAccountDeposit(profileId, Number(cashAmount), cashReason);
      setCashAmount("");
      setCashReason("");
      await loadGlobalViews();
      await loadTickerSpecificViews(selectedTicker);
    } catch (requestError) {
      setError(requestError.message || "Failed to deposit cash.");
    }
  }

  async function handleWithdraw() {
    if (!profileId || !cashAmount) return;
    setError("");
    try {
      await postVirtualAccountWithdraw(profileId, Number(cashAmount), cashReason);
      setCashAmount("");
      setCashReason("");
      await loadGlobalViews();
      await loadTickerSpecificViews(selectedTicker);
    } catch (requestError) {
      setError(requestError.message || "Failed to withdraw cash.");
    }
  }

  async function handleEnableHistory() {
    setHistoryEnabled(true);
    setHistoryOffset(0);
    setAccountHistory([]);
  }

  async function handleEnableHistoricalReplay() {
    setHistoricalEnabled(true);
  }

  const liveEquityPoints = useMemo(() => {
    if (!equityCurve?.length) return [];
    return equityCurve.map((item) => ({
      date: item.timestamp,
      total_equity: toNumeric(item.total_equity),
      cash: toNumeric(item.cash),
      holdings_value: toNumeric(item.holdings_value),
    }));
  }, [equityCurve]);

  const liveLatestEquityPoint = liveEquityPoints.length ? liveEquityPoints[liveEquityPoints.length - 1] : null;
  const historicalEquityPoints = useMemo(() => {
    if (!historicalSummary?.equity_curve) return [];
    return historicalSummary.equity_curve.map((item) => ({
      date: item.date,
      total_equity: toNumeric(item.total_equity),
      cash: toNumeric(item.cash),
      holdings_value: toNumeric(item.holdings_value),
      benchmark_equity: toNumeric(item.benchmark_equity),
    }));
  }, [historicalSummary]);
  const liveEquityMismatch =
    accountSummary && liveLatestEquityPoint
      ? Math.abs(Number(accountSummary.total_account_value || 0) - Number(liveLatestEquityPoint.total_equity || 0)) >
        0.01
      : false;

  const contributionPoints = useMemo(() => {
    if (!historicalContributionData?.monthly_contributions) return [];
    const confirmed = historicalContributionData.monthly_contributions.filter(
      (item) => Number(item.amount) > 0 || Number(item.cumulative_contributions) > 0
    );
    return confirmed.map((item) => ({
      date: item.date,
      cumulative_contributions: toNumeric(item.cumulative_contributions),
      amount: toNumeric(item.amount),
    }));
  }, [historicalContributionData]);

  return (
    <>
      <header className="app-header">
        <div>
          <h1>{getLabel(languageMode, "virtualTrader")}</h1>
          <p>
            {labelByMode(
              languageMode,
              "Live mode scans the active market universe automatically using model-or-fallback decisions.",
              ZH.intro
            )}
          </p>
        </div>
        <div className="header-controls">
          <span className="helper-chip">
            {labelByMode(languageMode, "Universe Size", ZH.universeSize)}: {liveStatus?.universe_size ?? "N/A"}
          </span>
          <span className="helper-chip">
            {labelByMode(languageMode, "Tickers Evaluated", ZH.tickersEvaluated)}: {liveStatus?.tickers_evaluated ?? 0}
          </span>
          <span className="helper-chip">
            {labelByMode(languageMode, "Model", ZH.model)}: {selectedModelName}
          </span>
          <button type="button" onClick={handleRunNow} disabled={isRunningNow}>
            {isRunningNow
              ? labelByMode(languageMode, "Running...", ZH.running)
              : labelByMode(languageMode, "Run Trader Now", ZH.runNow)}
          </button>
        </div>
      </header>

      {error ? (
        <div className="error-box">
          <p>{error}</p>
          <button
            type="button"
            onClick={async () => {
              await loadGlobalViews();
              await loadTickerSpecificViews(selectedTicker);
            }}
          >
            {labelByMode(languageMode, "Retry overview", "重新整理總覽")}
          </button>
        </div>
      ) : null}
      {isLoading ? (
        <section className="panel">
          <p>
            {labelByMode(
              languageMode,
              "Loading the account summary, holdings, and recent activity. Smaller sections will appear as they finish loading.",
              "正在載入帳戶摘要、持倉與近期活動，完成的區塊會先顯示。"
            )}
          </p>
        </section>
      ) : null}

      <RecentRunsPanel
        languageMode={languageMode}
        status={schedulerStatus}
        isLoading={isLoading && !schedulerStatus}
        onRefresh={loadSchedulerStatusOnly}
      />

      <section className="panel">
        <h3>{getLabel(languageMode, "liveTraderStatus")}</h3>
        <p className="helper-text">
          {labelByMode(languageMode, "This is simulation only. No broker orders are sent.", ZH.simulationOnly)}
        </p>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            "Market/news inputs are near-live snapshots and may be delayed by the data provider.",
            ZH.delayedDataWarning
          )}
        </p>
        {liveStatus ? (
          <div className="detail-grid">
            <p><strong>{labelByMode(languageMode, "Cash", ZH.cash)}:</strong> {formatMoney(accountSummary?.cash)}</p>
            <p><strong>{labelByMode(languageMode, "Holdings value", ZH.holdingsValue)}:</strong> {formatMoney(accountSummary?.holdings_value)}</p>
            <p><strong>{labelByMode(languageMode, "Total equity", ZH.totalEquity)}:</strong> {formatMoney(accountSummary?.total_account_value)}</p>
            <p><strong>{labelByMode(languageMode, "Realized PnL", ZH.realizedPnl)}:</strong> {formatMoney(accountSummary?.realized_pnl)}</p>
            <p><strong>{labelByMode(languageMode, "Applied contributions", ZH.appliedContributions)}:</strong> {formatMoney(accountSummary?.net_deposits)}</p>
            <p><strong>{labelByMode(languageMode, "Tickers evaluated", ZH.tickersEvaluated)}:</strong> {liveStatus.tickers_evaluated ?? 0}</p>
            <p><strong>{labelByMode(languageMode, "Tickers failed", ZH.tickersFailed)}:</strong> {liveStatus.tickers_failed ?? 0}</p>
            <p><strong>{labelByMode(languageMode, "Fallback decisions", ZH.fallbackDecisions)}:</strong> {liveStatus.fallback_used_count ?? 0}</p>
            <p><strong>{labelByMode(languageMode, "Generated at", ZH.generatedAt)}:</strong> {liveStatus.generated_at_utc}</p>
            <p><strong>Last updated:</strong> {accountSummary?.last_updated || accountSummary?.as_of || liveStatus.generated_at_utc}</p>
            <p><strong>Latest equity point:</strong> {formatMoney(liveLatestEquityPoint?.total_equity)}</p>
            <p>
              <strong>Curve timestamp:</strong>{" "}
              {accountSummary?.curve_last_point_timestamp || getLabel(languageMode, "curveTimestampUnavailable")}
            </p>
          </div>
        ) : (
          <p>{labelByMode(languageMode, "No live status yet.", ZH.noLiveStatus)}</p>
        )}
      </section>

      {liveEquityMismatch ? (
        <p className="error-box">
          {labelByMode(
            languageMode,
            "Live summary and latest equity-curve point are out of sync. Please refresh and check backend logs.",
            "\u5373\u6642\u6458\u8981\u8207\u8cc7\u7522\u66f2\u7dda\u6700\u65b0\u9ede\u4e0d\u4e00\u81f4\uff0c\u8acb\u91cd\u65b0\u6574\u7406\u4e26\u6aa2\u67e5\u5f8c\u7aef\u65e5\u8a8c\u3002"
          )}
        </p>
      ) : null}

      <EquityChart
        ticker={profileId}
        points={liveEquityPoints}
        languageMode={languageMode}
        title={labelByMode(
          languageMode,
          "Live Virtual Trader Equity Curve",
          "\u5373\u6642\u865b\u64ec\u4ea4\u6613\u8cc7\u7522\u66f2\u7dda"
        )}
        subtitle={labelByMode(
          languageMode,
          `Profile ID: ${profileId}`,
          `Profile ID\uff1a${profileId}`
        )}
      />

      <AccountSummaryCards languageMode={languageMode} summary={accountSummary} />
      <HoldingsTable languageMode={languageMode} holdings={accountHoldings} />

      <MonthlyContributionInput
        userId={profileId}
        languageMode={languageMode}
        onUpdated={async () => {
          await loadGlobalViews();
          await loadTickerSpecificViews(selectedTicker);
        }}
      />

      <section className="panel">
        <h3>{labelByMode(languageMode, "Trading Account", ZH.tradingAccount)}</h3>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            `Profile ID: ${profileId} | Persistence: saved by profile and survives refresh/restart.`,
            `Profile ID\uff1a${profileId} | \u6301\u4e45\u5316\uff1a\u4ee5 Profile \u5132\u5b58\uff0c\u91cd\u65b0\u6574\u7406/\u91cd\u555f\u5f8c\u4ecd\u4fdd\u7559\u3002`
          )}
        </p>
        <div className="settings-form">
          <label>
            {labelByMode(languageMode, "Amount (USD)", ZH.amountUsd)}
            <input
              type="number"
              min="0.01"
              step="0.01"
              value={cashAmount}
              onChange={(event) => setCashAmount(event.target.value)}
            />
          </label>
          <label>
            {labelByMode(languageMode, "Reason", ZH.reason)}
            <input type="text" value={cashReason} onChange={(event) => setCashReason(event.target.value)} />
          </label>
          <div className="settings-actions">
            <button type="button" onClick={handleDeposit}>
              {labelByMode(languageMode, "Add Deposit Event", ZH.deposit)}
            </button>
            <button type="button" onClick={handleWithdraw}>
              {labelByMode(languageMode, "Add Withdrawal Event", ZH.withdraw)}
            </button>
          </div>
          <p className="helper-text">
            {labelByMode(
              languageMode,
              "Deposits and withdrawals are saved as immutable ledger events.",
              `${ZH.depositHint} ${ZH.withdrawHint}`
            )}
          </p>
        </div>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            "Reset is destructive and permanent. It clears this profile's simulated cash flow, holdings, trade history, and monthly contribution settings/history.",
            "\u91cd\u8a2d\u70ba\u6bc0\u58de\u6027\u4e14\u7121\u6cd5\u5fa9\u539f\uff0c\u6703\u6e05\u9664\u6b64 Profile \u7684\u6a21\u64ec\u73fe\u91d1\u6d41\u3001\u6301\u5009\u3001\u4ea4\u6613\u7d00\u9304\u53ca\u6bcf\u6708\u6ce8\u8cc7\u8a2d\u5b9a/\u6b77\u53f2\u3002"
          )}
        </p>
        <ResetTradingAccountButton
          userId={profileId}
          languageMode={languageMode}
          onResetComplete={async () => {
            await loadGlobalViews();
            await loadTickerSpecificViews(selectedTicker);
          }}
        />
      </section>

      <RecentTradesTable languageMode={languageMode} trades={recentTrades} />

      <section className="panel">
        <h3>{labelByMode(languageMode, "Latest Decisions", ZH.latestDecisions)}</h3>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>{labelByMode(languageMode, "Ticker", ZH.ticker)}</th>
                <th>{labelByMode(languageMode, "Action", ZH.action)}</th>
                <th>{labelByMode(languageMode, "Price", ZH.price)}</th>
                <th>{labelByMode(languageMode, "Reason", ZH.reason)}</th>
              </tr>
            </thead>
            <tbody>
              {liveDecisionLog.length ? (
                liveDecisionLog.slice(0, 8).map((item) => (
                  <tr
                    key={`${item.timestamp}-${item.ticker}-${item.action}`}
                    className={selectedLiveTrade?.timestamp === item.timestamp ? "selected-row" : ""}
                    onClick={() => setSelectedLiveTrade(item)}
                  >
                    <td>{item.timestamp}</td>
                    <td>{item.ticker}</td>
                    <td>{item.action}</td>
                    <td>{formatMoney(item.price)}</td>
                    <td>{item.reason}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={5}>
                    {getLabel(languageMode, "noRecentTraderDecisions")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel explanation-panel">
        <h3>{labelByMode(languageMode, "Latest Decision Reason", ZH.latestReason)}</h3>
        {!selectedLiveTrade ? (
          <p>{labelByMode(languageMode, "No trade decision selected.", ZH.noDecision)}</p>
        ) : (
          <>
            <p><strong>{selectedLiveTrade.action_summary}</strong></p>
            <p>{selectedLiveTrade.threshold_summary}</p>
            <div className="detail-grid">
              <p><strong>{labelByMode(languageMode, "Technical state", ZH.technicalState)}:</strong> {selectedLiveTrade.technical_state_summary}</p>
              <p><strong>{labelByMode(languageMode, "News sentiment", ZH.newsSentiment)}:</strong> {selectedLiveTrade.news_sentiment_summary}</p>
              <p><strong>{labelByMode(languageMode, "Benchmark strength", ZH.benchmarkStrength)}:</strong> {selectedLiveTrade.benchmark_strength_summary}</p>
              <p>
                <strong>{labelByMode(languageMode, "Confidence", ZH.confidence)}:</strong>{" "}
                {selectedLiveTrade.confidence_score !== null && selectedLiveTrade.confidence_score !== undefined
                  ? `${(Number(selectedLiveTrade.confidence_score) * 100).toFixed(0)}%`
                  : "N/A"}
              </p>
            </div>
          </>
        )}
      </section>

      {!newsEnabled ? (
        <section className="panel explanation-panel">
          <h3>{labelByMode(languageMode, "News Sentiment", ZH.newsSentiment)}</h3>
          <p className="helper-text">
            {labelByMode(
              languageMode,
              "News sentiment is optional and can be slower on a small backend, so it loads only when requested.",
              "新聞情緒屬於選用資料，在小型後端上可能較慢，因此改為需要時才載入。"
            )}
          </p>
          <div className="settings-actions">
            <button type="button" onClick={() => setNewsEnabled(true)}>
              {labelByMode(languageMode, "Load news sentiment", "載入新聞情緒")}
            </button>
          </div>
        </section>
      ) : (
        <NewsSentimentPanel ticker={selectedTicker} languageMode={languageMode} />
      )}
      {!historyEnabled ? (
        <section className="panel">
          <h3>{getLabel(languageMode, "historyTitle")}</h3>
          <p className="helper-text">
            {labelByMode(
              languageMode,
              "History is loaded on demand to keep this page responsive on low-resource servers.",
              "為了在低資源伺服器保持頁面流暢，歷史紀錄會按需載入。"
            )}
          </p>
          <div className="settings-actions">
            <button type="button" onClick={handleEnableHistory} disabled={historyLoading}>
              {historyLoading ? getLabel(languageMode, "loading") : getLabel(languageMode, "loadAccountHistory")}
            </button>
          </div>
        </section>
      ) : (
        <TransactionHistoryTable
          languageMode={languageMode}
          events={accountHistory}
          isLoading={historyLoading}
          hasMore={historyHasMore}
          onLoadMore={() => loadAccountHistoryPage({ reset: false })}
          errorMessage={historyError}
        />
      )}

      {!historicalEnabled ? (
        <section className="panel">
          <h3>{labelByMode(languageMode, "Historical Replay Mode", ZH.historicalMode)}</h3>
          <p className="helper-text">
            {labelByMode(
              languageMode,
              "Historical replay data is optional and loaded on demand to reduce backend load.",
              "歷史回放資料屬可選內容，會按需載入以減少後端負載。"
            )}
          </p>
          <div className="settings-actions">
            <button type="button" onClick={handleEnableHistoricalReplay} disabled={historicalLoading}>
              {historicalLoading ? getLabel(languageMode, "loading") : getLabel(languageMode, "loadHistoricalReplay")}
            </button>
          </div>
        </section>
      ) : historicalContributionData ? (
        <>
          <EquityChart
            ticker={selectedTicker}
            points={historicalEquityPoints}
            languageMode={languageMode}
            title={labelByMode(
              languageMode,
              "Historical Replay Equity Curve",
              "\u6b77\u53f2\u56de\u653e\u8cc7\u7522\u66f2\u7dda"
            )}
            subtitle={labelByMode(
              languageMode,
              `Ticker: ${selectedTicker}`,
              `\u80a1\u7968: ${selectedTicker}`
            )}
          />
          <LineChart
            title={labelByMode(languageMode, "Monthly Contribution History", ZH.monthlyContributionHistory)}
            subtitle={`Ticker: ${selectedTicker}`}
            points={contributionPoints}
            xAxisLabel="Month"
            yAxisLabel="Contribution Amount (USD)"
            yValueKind="price"
            lines={[
              {
                key: "cumulative_contributions",
                label: labelByMode(languageMode, "Total Contributions", ZH.totalContributions),
                color: "#047857",
                strokeWidth: 2.4,
                valueKind: "price",
              },
              {
                key: "amount",
                label: labelByMode(languageMode, "Monthly Contribution", ZH.monthlyContribution),
                color: "#2563eb",
                strokeWidth: 1.8,
                valueKind: "price",
              },
            ]}
            noDataMessage={getLabel(languageMode, "noDataAvailable")}
            height={180}
          />
        </>
      ) : (
        <section className="panel">
          <p>{labelByMode(languageMode, "Loading...", ZH.loading)}</p>
        </section>
      )}
    </>
  );
}

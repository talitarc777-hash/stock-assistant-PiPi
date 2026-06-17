import React, { useEffect, useMemo, useState } from "react";

import {
  fetchTraderSchedulerStatus,
  fetchLiveVirtualTraderStatus,
  fetchLiveVirtualTraderTrades,
  fetchVirtualAccountHistory,
  fetchVirtualAccountRecentTrades,
  fetchVirtualTraderTrades,
  fetchVirtualTraderSummary,
  postVirtualAccountDeposit,
  postVirtualAccountWithdraw,
  runLiveVirtualTraderNow,
} from "../api";
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

const DEFAULT_PERIOD = "5y";
const AUTO_TRADING_MODEL = "auto_best";
const HISTORY_PAGE_SIZE = 120;
const SCHEDULER_REFRESH_MS = 60000;
const BUY_POTENTIAL_FILTERS = ["all", "bought", "high_blocked", "watching", "low_now", "holding", "sell_action"];

const ZH = {
  virtualTrader: "虛擬交易員",
  intro: "專注於最新動作、原因、持倉、賺蝕和注資。所有交易都是模擬，不會發送真實下單。",
  ticker: "股票代號",
  model: "模型",
  running: "執行中...",
  runNow: "立即更新模擬決策",
  loading: "載入中...",
  simulationOnly: "只屬模擬，不會發送真實下單。",
  cash: "現金",
  holdingsValue: "持倉市值",
  totalEquity: "總資產",
  realizedPnl: "已實現賺蝕",
  unrealizedPnl: "未實現賺蝕",
  appliedContributions: "累計注資",
  amountUsd: "金額 USD",
  deposit: "加入一次性入金",
  withdraw: "加入一次性提款",
  action: "動作",
  price: "價格",
  reason: "原因",
  newsSentiment: "新聞情緒",
  historicalMode: "歷史回放模式",
  monthlyContributionHistory: "每月注資紀錄",
  totalContributions: "累計注資金額",
  monthlyContribution: "每月注資",
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

function formatPercent(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "";
  return ` (${numeric.toFixed(2)}%)`;
}

function actionText(action, languageMode) {
  const normalized = String(action || "").toLowerCase();
  const map = {
    buy: ["Buy", "買入"],
    sell: ["Sell", "賣出"],
    hold: ["Hold", "持有"],
    no_action: ["No action", "暫不動作"],
  };
  const [en, zh] = map[normalized] || [action || "N/A", action || "N/A"];
  return labelByMode(languageMode, en, zh);
}

function decisionReasonText(reason, languageMode) {
  const normalized = String(reason || "").toLowerCase();
  const reasons = {
    model_bullish_signal: ["Model approved this buy", "\u6a21\u578b\u5df2\u6279\u51c6\u8cb7\u5165"],
    model_bearish_signal: ["Model recommended reducing the holding", "\u6a21\u578b\u5efa\u8b70\u6e1b\u6301"],
    model_not_bullish: ["Model is not bullish yet", "\u6a21\u578b\u5c1a\u672a\u770b\u597d"],
    confidence_below_threshold: ["Model confidence is below 55%", "\u6a21\u578b\u4fe1\u5fc3\u4f4e\u65bc 55%"],
    risk_or_cash_constraint: ["Blocked by cash or risk rules", "\u53d7\u73fe\u91d1\u6216\u98a8\u96aa\u898f\u5247\u9650\u5236"],
    holding_position: ["Continue holding", "\u7e7c\u7e8c\u6301\u6709"],
    stop_loss: ["Stop-loss was reached", "\u5df2\u89f8\u53ca\u6b62\u8755"],
    take_profit: ["Profit target was reached", "\u5df2\u9054\u5230\u6b62\u76c8\u76ee\u6a19"],
    signal_cooldown_active: ["Waiting for the signal cooldown", "\u6b63\u7b49\u5f85\u8a0a\u865f\u51b7\u975c\u671f"],
    duplicate_signal_suppressed: ["Repeated signal was ignored", "\u91cd\u8907\u8a0a\u865f\u5df2\u5ffd\u7565"],
    fallback_rule_neutral_hold: ["Backup rules are neutral", "\u5f8c\u5099\u898f\u5247\u76ee\u524d\u4e2d\u6027"],
  };
  const [en, zh] = reasons[normalized] || [
    String(reason || "No action"),
    String(reason || "\u672a\u6709\u52d5\u4f5c"),
  ];
  return labelByMode(languageMode, en, zh);
}

function opportunityText(item, languageMode) {
  const labels = {
    bought: ["Bought", "\u5df2\u8cb7\u5165"],
    high_blocked: ["High, but blocked", "\u9ad8\uff0c\u4f46\u53d7\u898f\u5247\u9650\u5236"],
    watching: ["Watching", "\u89c0\u5bdf\u4e2d"],
    low_now: ["Low now", "\u76ee\u524d\u8f03\u4f4e"],
    holding: ["Holding", "\u6301\u6709\u4e2d"],
    sell_action: ["Sell action", "\u8ce3\u51fa\u52d5\u4f5c"],
  };
  const [en, zh] = labels[getBuyPotentialKey(item)] || labels.low_now;
  return labelByMode(languageMode, en, zh);
}

function getBuyPotentialKey(item) {
  if (BUY_POTENTIAL_FILTERS.includes(item?.potential)) return item.potential;
  const action = String(item?.action || "").toLowerCase();
  if (action === "buy") return "bought";
  if (action === "sell") return "sell_action";
  if (action === "hold") return "holding";

  const prediction = Number(item?.metadata?.prediction_value);
  const confidence = Number(item?.confidence_score);
  if (prediction > 0 && confidence >= 0.55) {
    return "high_blocked";
  }
  if (prediction > 0) return "watching";
  return "low_now";
}

function decisionOpportunityScore(item, isWatchlist) {
  const actionRank = { buy: 5, sell: 4, hold: 2, no_action: 1 };
  const action = String(item?.action || "no_action").toLowerCase();
  const prediction = Number(item?.metadata?.prediction_value);
  const confidence = Number(item?.confidence_score);
  return (
    (actionRank[action] || 0) * 1000000
    + (isWatchlist ? 10000 : 0)
    + (Number.isFinite(prediction) ? prediction * 1000 : 0)
    + (Number.isFinite(confidence) ? confidence * 100 : 0)
  );
}

export default function VirtualTraderPage({ languageMode, currentWatchlist, profileId }) {
  const [selectedTicker, setSelectedTicker] = useState(currentWatchlist[0] || "VOO");
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
  const [advancedEnabled, setAdvancedEnabled] = useState(false);
  const [buyPotentialFilter, setBuyPotentialFilter] = useState("all");
  const [isLoading, setIsLoading] = useState(false);
  const [isRunningNow, setIsRunningNow] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!currentWatchlist.length) return;
    if (!currentWatchlist.includes(selectedTicker)) {
      setSelectedTicker(currentWatchlist[0]);
    }
  }, [currentWatchlist.join(","), selectedTicker]);

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
    if (!profileId) return;
    setIsLoading(true);
    setError("");
    try {
      const [liveStatusResult, schedulerStatusResult, recentTradesResult, decisionHistoryResult] =
        await Promise.allSettled([
          fetchLiveVirtualTraderStatus(profileId, null, AUTO_TRADING_MODEL, false),
          fetchTraderSchedulerStatus(24),
          fetchVirtualAccountRecentTrades(profileId, 20),
          fetchLiveVirtualTraderTrades(profileId, null, 300),
        ]);

      if (schedulerStatusResult.status === "fulfilled") setSchedulerStatus(schedulerStatusResult.value);
      if (liveStatusResult.status === "fulfilled") applyLiveStatusPayload(liveStatusResult.value);
      if (recentTradesResult.status === "fulfilled") setRecentTrades(recentTradesResult.value.trades || []);
      if (decisionHistoryResult.status === "fulfilled") {
        const decisions = decisionHistoryResult.value.trades || [];
        setLiveDecisionLog(decisions);
        setSelectedLiveTrade(decisions[0] || null);
      }

      if (liveStatusResult.status === "rejected") {
        setError(liveStatusResult.reason?.message || "Failed to load virtual trader status.");
      }
    } catch (requestError) {
      setError(requestError.message || "Failed to load virtual trader.");
    } finally {
      setIsLoading(false);
    }
  }

  async function loadHistoricalReplayData(activeTicker = selectedTicker) {
    if (!profileId || !activeTicker) return;
    setHistoricalLoading(true);
    try {
      const [historicalSummaryPayload, historicalTradesPayload] = await Promise.all([
        fetchVirtualTraderSummary(activeTicker, DEFAULT_PERIOD, null, 300, profileId),
        fetchVirtualTraderTrades(activeTicker, DEFAULT_PERIOD, null, 120, profileId),
      ]);
      setHistoricalSummary(historicalSummaryPayload);
      setHistoricalContributionData(historicalTradesPayload);
    } catch (requestError) {
      setError(requestError.message || "Failed to load historical replay section.");
    } finally {
      setHistoricalLoading(false);
    }
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

  async function loadSchedulerStatusOnly() {
    try {
      setSchedulerStatus(await fetchTraderSchedulerStatus(24));
    } catch {
      setSchedulerStatus(null);
    }
  }

  useEffect(() => {
    loadGlobalViews();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId]);

  useEffect(() => {
    if (!historyEnabled) return;
    loadAccountHistoryPage({ reset: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId, historyEnabled]);

  useEffect(() => {
    if (!historicalEnabled) return;
    loadHistoricalReplayData(selectedTicker);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId, selectedTicker, historicalEnabled]);

  useEffect(() => {
    if (!profileId) return undefined;
    const timer = window.setInterval(() => {
      if (!document.hidden) loadSchedulerStatusOnly();
    }, SCHEDULER_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [profileId]);

  async function handleRunNow() {
    if (!profileId) return;
    setIsRunningNow(true);
    setError("");
    try {
      await runLiveVirtualTraderNow(profileId, null, AUTO_TRADING_MODEL);
      await loadGlobalViews();
      if (historicalEnabled) await loadHistoricalReplayData(selectedTicker);
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
    } catch (requestError) {
      setError(requestError.message || "Failed to withdraw cash.");
    }
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

  const contributionPoints = useMemo(() => {
    if (!historicalContributionData?.monthly_contributions) return [];
    return historicalContributionData.monthly_contributions
      .filter((item) => Number(item.amount) > 0 || Number(item.cumulative_contributions) > 0)
      .map((item) => ({
        date: item.date,
        cumulative_contributions: toNumeric(item.cumulative_contributions),
        amount: toNumeric(item.amount),
      }));
  }, [historicalContributionData]);

  const totalProfitLoss = useMemo(() => {
    const realized = Number(accountSummary?.realized_pnl || 0);
    const unrealized = Number(accountSummary?.unrealized_pnl || 0);
    return realized + unrealized;
  }, [accountSummary]);

  const totalProfitLossPct = useMemo(() => {
    const netDeposits = Number(accountSummary?.net_deposits || 0);
    if (!Number.isFinite(netDeposits) || Math.abs(netDeposits) < 0.000001) return null;
    return (totalProfitLoss / Math.abs(netDeposits)) * 100;
  }, [accountSummary, totalProfitLoss]);

  const contributionSummary = useMemo(() => {
    const events = liveStatus?.contribution_events || [];
    return events.reduce(
      (totals, event) => {
        const amount = Number(event.amount ?? event.applied_amount ?? 0);
        if (!Number.isFinite(amount)) return totals;
        if (event.event_type === "monthly_contribution") totals.monthly += amount;
        if (event.event_type === "manual_deposit" || event.event_type === "withdrawal") totals.oneTime += amount;
        return totals;
      },
      { monthly: 0, oneTime: 0 }
    );
  }, [liveStatus]);

  const actionRows = useMemo(() => {
    const watchlistSet = new Set(
      currentWatchlist.map((ticker) => String(ticker).trim().toUpperCase()).filter(Boolean)
    );
    const latestByTicker = new Map();

    for (const item of liveDecisionLog) {
      const ticker = String(item?.ticker || "").trim().toUpperCase();
      if (!ticker || latestByTicker.has(ticker)) continue;
      latestByTicker.set(ticker, {
        ...item,
        is_watchlist: watchlistSet.has(ticker),
      });
    }

    return [...latestByTicker.values()]
      .sort(
        (left, right) =>
          decisionOpportunityScore(right, right.is_watchlist)
          - decisionOpportunityScore(left, left.is_watchlist)
      )
      .slice(0, 15);
  }, [currentWatchlist, liveDecisionLog]);

  const buyPotentialCounts = useMemo(() => {
    return actionRows.reduce(
      (counts, item) => {
        const potential = getBuyPotentialKey(item);
        counts.all += 1;
        counts[potential] = (counts[potential] || 0) + 1;
        return counts;
      },
      {
        all: 0,
        bought: 0,
        high_blocked: 0,
        watching: 0,
        low_now: 0,
        holding: 0,
        sell_action: 0,
      }
    );
  }, [actionRows]);

  const filteredActionRows = useMemo(() => {
    if (buyPotentialFilter === "all") return actionRows;
    return actionRows.filter((item) => getBuyPotentialKey(item) === buyPotentialFilter);
  }, [actionRows, buyPotentialFilter]);

  useEffect(() => {
    if (!filteredActionRows.length) {
      setSelectedLiveTrade(null);
      return;
    }
    setSelectedLiveTrade((current) => {
      const remainsVisible = filteredActionRows.some(
        (item) => item.timestamp === current?.timestamp && item.ticker === current?.ticker
      );
      return remainsVisible ? current : filteredActionRows[0];
    });
  }, [filteredActionRows]);

  return (
    <>
      <header className="app-header">
        <div>
          <h1>{getLabel(languageMode, "virtualTrader")}</h1>
          <p>{labelByMode(languageMode, "A simple simulation view for new investors.", ZH.intro)}</p>
        </div>
        <div className="header-controls">
          <button type="button" onClick={handleRunNow} disabled={isRunningNow}>
            {isRunningNow
              ? labelByMode(languageMode, "Running...", ZH.running)
              : labelByMode(languageMode, "Update decisions", ZH.runNow)}
          </button>
        </div>
      </header>

      {error ? (
        <div className="error-box">
          <p>{error}</p>
          <button type="button" onClick={loadGlobalViews}>
            {labelByMode(languageMode, "Retry", "重試")}
          </button>
        </div>
      ) : null}

      {isLoading ? (
        <section className="panel">
          <p>{labelByMode(languageMode, "Loading your simulation account...", "正在載入模擬帳戶...")}</p>
        </section>
      ) : null}

      <section className="panel">
        <h3>{labelByMode(languageMode, "Latest Stock Action", "最新股票動作")}</h3>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            "Top opportunities from the full market universe are shown. Executed actions appear first, and watchlist tickers are clearly marked.",
            "\u986f\u793a\u6574\u500b\u5e02\u5834\u7bc4\u570d\u5167\u7684\u6700\u4f73\u6a5f\u6703\u3002\u5df2\u57f7\u884c\u7684\u52d5\u4f5c\u6703\u512a\u5148\u986f\u793a\uff0c\u89c0\u5bdf\u6e05\u55ae\u80a1\u7968\u6703\u6e05\u695a\u6a19\u8a18\u3002"
          )}
        </p>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            "Trading rule: quantities use whole shares. Normal sell signals reduce about 50% of the holding; a stop-loss sells all shares. Each trade costs HKD 50.",
            "\u4ea4\u6613\u898f\u5247\uff1a\u53ea\u4f7f\u7528\u6574\u6578\u80a1\u6578\u3002\u4e00\u822c\u8ce3\u51fa\u8a0a\u865f\u6703\u6e1b\u6301\u7d04 50%\uff1b\u89f8\u53ca\u6b62\u8755\u6642\u6703\u8ce3\u51fa\u5168\u90e8\u6301\u5009\u3002\u6bcf\u6b21\u4ea4\u6613\u6536\u53d6 50 \u6e2f\u5143\u3002"
          )}
        </p>
        <div className="action-filter-panel">
          <div className="action-filter-title">
            {labelByMode(languageMode, "Group by buy potential", "\u6309\u8cb7\u5165\u6f5b\u529b\u5206\u985e")}
          </div>
          <div className="action-filter-bar" aria-label={labelByMode(languageMode, "Filter buy potential", "\u7be9\u9078\u8cb7\u5165\u6f5b\u529b")}>
            {BUY_POTENTIAL_FILTERS.map((filterValue) => (
              <button
                key={filterValue}
                type="button"
                className={buyPotentialFilter === filterValue ? "active" : ""}
                onClick={() => setBuyPotentialFilter(filterValue)}
              >
                {filterValue === "all"
                  ? labelByMode(languageMode, "All", "\u5168\u90e8")
                  : opportunityText({ action: "no_action", metadata: {}, confidence_score: null, potential: filterValue }, languageMode)}
                <span>{buyPotentialCounts[filterValue] || 0}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="table-wrap beginner-action-table">
          <table>
            <thead>
              <tr>
                <th>{labelByMode(languageMode, "Ticker", ZH.ticker)}</th>
                <th>{labelByMode(languageMode, "Source", "\u4f86\u6e90")}</th>
                <th>{labelByMode(languageMode, "Action", ZH.action)}</th>
                <th>{labelByMode(languageMode, "Buy potential", "\u8cb7\u5165\u6f5b\u529b")}</th>
                <th>{labelByMode(languageMode, "Reason", ZH.reason)}</th>
                <th>{labelByMode(languageMode, "Model used", "\u4f7f\u7528\u6a21\u578b")}</th>
                <th>{labelByMode(languageMode, "Price", ZH.price)}</th>
              </tr>
            </thead>
            <tbody>
              {filteredActionRows.length ? (
                filteredActionRows.map((item) => (
                  <tr
                    key={`${item.timestamp}-${item.ticker}-${item.action}`}
                    className={selectedLiveTrade?.timestamp === item.timestamp ? "selected-row" : ""}
                    onClick={() => setSelectedLiveTrade(item)}
                  >
                    <td>{item.ticker}</td>
                    <td>
                      <span className={item.is_watchlist ? "ticker-source-watchlist" : "ticker-source-market"}>
                        {item.is_watchlist
                          ? labelByMode(languageMode, "Watchlist", "\u89c0\u5bdf\u6e05\u55ae")
                          : labelByMode(languageMode, "Market", "\u5e02\u5834")}
                      </span>
                    </td>
                    <td>{actionText(item.action, languageMode)}</td>
                    <td>{opportunityText(item, languageMode)}</td>
                    <td>{decisionReasonText(item.reason, languageMode)}</td>
                    <td>
                      {item.model_name || "N/A"}
                      {(item.metadata?.model_period || item.model_period)
                        ? ` (${item.metadata?.model_period || item.model_period})`
                        : ""}
                    </td>
                    <td>{formatMoney(item.price)}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={7}>
                    {labelByMode(
                      languageMode,
                      actionRows.length
                        ? "No decisions match this buy potential filter."
                        : "No recent market decisions. Select Update decisions to scan the universe now.",
                      actionRows.length
                        ? "\u6c92\u6709\u6c7a\u7b56\u7b26\u5408\u6b64\u8cb7\u5165\u6f5b\u529b\u7be9\u9078\u3002"
                        : "\u76ee\u524d\u5c1a\u672a\u6709\u6700\u65b0\u5e02\u5834\u6c7a\u7b56\u3002\u8acb\u6309\u300c\u66f4\u65b0\u6c7a\u7b56\u300d\u7acb\u5373\u6383\u63cf\u5e02\u5834\u3002"
                    )}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {selectedLiveTrade ? (
          <p className="helper-text">
            <strong>{labelByMode(languageMode, "Reason detail", "原因詳情")}:</strong>{" "}
            {selectedLiveTrade.threshold_summary || selectedLiveTrade.reason}
          </p>
        ) : null}
      </section>

      <section className="panel">
        <h3>{labelByMode(languageMode, "Total Earn / Loss", "總賺蝕")}</h3>
        <div className="beginner-summary-grid">
          <div>
            <span>{labelByMode(languageMode, "Account value", ZH.totalEquity)}</span>
            <strong>{formatMoney(accountSummary?.total_account_value)}</strong>
          </div>
          <div className={totalProfitLoss >= 0 ? "pnl-positive" : "pnl-negative"}>
            <span>{labelByMode(languageMode, "Profit / Loss", "賺蝕")}</span>
            <strong>{formatMoney(totalProfitLoss)}{formatPercent(totalProfitLossPct)}</strong>
          </div>
          <div>
            <span>{labelByMode(languageMode, "Cash", ZH.cash)}</span>
            <strong>{formatMoney(accountSummary?.cash)}</strong>
          </div>
          <div>
            <span>{labelByMode(languageMode, "Holdings value", ZH.holdingsValue)}</span>
            <strong>{formatMoney(accountSummary?.holdings_value)}</strong>
          </div>
        </div>
      </section>

      <HoldingsTable languageMode={languageMode} holdings={accountHoldings} />

      <section className="panel">
        <h3>{labelByMode(languageMode, "Contributions", "注資")}</h3>
        <div className="beginner-summary-grid">
          <div>
            <span>{labelByMode(languageMode, "Total cash added", ZH.appliedContributions)}</span>
            <strong>{formatMoney(accountSummary?.net_deposits)}</strong>
          </div>
          <div>
            <span>{labelByMode(languageMode, "Recent monthly", "最近每月注資")}</span>
            <strong>{formatMoney(contributionSummary.monthly)}</strong>
          </div>
          <div>
            <span>{labelByMode(languageMode, "Recent one-time", "最近一次性現金變動")}</span>
            <strong>{formatMoney(contributionSummary.oneTime)}</strong>
          </div>
        </div>
      </section>

      <MonthlyContributionInput userId={profileId} languageMode={languageMode} onUpdated={loadGlobalViews} />

      <section className="panel">
        <h3>{labelByMode(languageMode, "One-Time Cash Add / Withdraw", "一次性入金 / 提款")}</h3>
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
              {labelByMode(languageMode, "Add deposit", ZH.deposit)}
            </button>
            <button type="button" onClick={handleWithdraw}>
              {labelByMode(languageMode, "Add withdrawal", ZH.withdraw)}
            </button>
          </div>
        </div>
        <ResetTradingAccountButton userId={profileId} languageMode={languageMode} onResetComplete={loadGlobalViews} />
      </section>

      <RecentTradesTable languageMode={languageMode} trades={recentTrades} />

      <section className="panel">
        <h3>{labelByMode(languageMode, "Advanced Details", "進階資料")}</h3>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            "Open this for scheduler status, charts, news sentiment, account history, and replay data.",
            "打開後可查看排程狀態、圖表、新聞情緒、交易歷史和回放資料。"
          )}
        </p>
        <div className="settings-actions">
          <button type="button" onClick={() => setAdvancedEnabled((value) => !value)}>
            {advancedEnabled
              ? labelByMode(languageMode, "Hide advanced details", "隱藏進階資料")
              : labelByMode(languageMode, "Show advanced details", "顯示進階資料")}
          </button>
        </div>
      </section>

      {advancedEnabled ? (
        <>
          <RecentRunsPanel
            languageMode={languageMode}
            status={schedulerStatus}
            isLoading={isLoading && !schedulerStatus}
            onRefresh={loadSchedulerStatusOnly}
          />

          <EquityChart
            ticker={profileId}
            points={liveEquityPoints}
            languageMode={languageMode}
            title={labelByMode(languageMode, "Account Value Chart", "帳戶價值圖")}
            subtitle={labelByMode(languageMode, `Profile ID: ${profileId}`, `Profile ID: ${profileId}`)}
          />

          {!newsEnabled ? (
            <section className="panel">
              <h3>{labelByMode(languageMode, "News Sentiment", ZH.newsSentiment)}</h3>
              <p className="helper-text">
                {labelByMode(
                  languageMode,
                  "News sentiment is optional and can be slower, so it loads only when requested.",
                  "新聞情緒屬於選用資料，可能較慢，因此只在需要時載入。"
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
              <div className="settings-actions">
                <button
                  type="button"
                  onClick={() => {
                    setHistoryEnabled(true);
                    setHistoryOffset(0);
                    setAccountHistory([]);
                  }}
                  disabled={historyLoading}
                >
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
              <div className="settings-actions">
                <button type="button" onClick={() => setHistoricalEnabled(true)} disabled={historicalLoading}>
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
                title={labelByMode(languageMode, "Historical Replay Equity Curve", "歷史回放資產曲線")}
                subtitle={labelByMode(languageMode, `Ticker: ${selectedTicker}`, `股票: ${selectedTicker}`)}
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
      ) : null}
    </>
  );
}

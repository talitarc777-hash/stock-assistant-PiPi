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
const DECISION_HISTORY_LIMIT = 160;
const SCHEDULER_REFRESH_MS = 60000;
const BUY_POTENTIAL_FILTERS = ["all", "bought", "high_blocked", "watching", "low_now", "holding", "sell_action"];

const ZH = {
  intro: "這是一個給新手投資者使用的虛擬交易和學習頁面，只作教育和模擬用途。",
  ticker: "股票代號",
  running: "執行中...",
  runNow: "更新決策",
  loadingAccount: "正在載入模擬帳戶...",
  retry: "重試",
  cash: "現金",
  holdingsValue: "持倉價值",
  totalEquity: "帳戶總值",
  appliedContributions: "已加入資金",
  amountUsd: "金額 (USD)",
  deposit: "加入現金",
  withdraw: "提取現金",
  action: "動作",
  price: "價格",
  reason: "原因",
  latestStockAction: "最新股票動作",
  buyPotential: "買入潛力",
  source: "來源",
  modelUsed: "使用模型",
  reasonDetail: "原因詳情",
  totalEarnLoss: "總賺蝕",
  accountValue: "帳戶總值",
  profitLoss: "賺蝕",
  contributions: "注資",
  totalCashAdded: "總加入現金",
  recentMonthly: "最近每月注資",
  recentOneTime: "最近一次性現金",
  oneTimeCash: "一次性現金加入 / 提取",
  advancedDetails: "進階資料",
  showAdvanced: "顯示進階資料",
  hideAdvanced: "隱藏進階資料",
  newsSentiment: "新聞情緒",
  loadNews: "載入新聞情緒",
  historicalMode: "歷史回測模式",
  historicalEquity: "歷史回測帳戶曲線",
  monthlyContributionHistory: "每月注資記錄",
  totalContributions: "累計注資",
  monthlyContribution: "每月注資",
  watchlist: "觀察清單",
  market: "市場",
  all: "全部",
  filterBuyPotential: "篩選買入潛力",
  groupBuyPotential: "按買入潛力分類",
  noFilterMatch: "沒有決策符合此買入潛力篩選。",
  noRecentDecisions: "目前尚未有最新市場決策。請按「更新決策」立即掃描市場。",
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
    no_action: ["No action", "沒有動作"],
  };
  const [en, zh] = map[normalized] || [action || "N/A", action || "N/A"];
  return labelByMode(languageMode, en, zh);
}

function decisionReasonText(reason, languageMode) {
  const normalized = String(reason || "").toLowerCase();
  const reasons = {
    model_bullish_signal: ["Model approved this buy", "模型已批准買入"],
    model_bearish_signal: ["Model recommended reducing the holding", "模型建議減持"],
    model_not_bullish: ["Model is not bullish yet", "模型暫時未看好"],
    confidence_below_threshold: ["Model confidence is below 55%", "模型信心低於 55%"],
    risk_or_cash_constraint: ["Blocked by cash or risk rules", "受現金或風險規則限制"],
    context_score_too_low: ["Context score is too weak to buy", "背景評分不足，暫不買入"],
    context_risk_reduction: ["Context risk triggered a partial sell", "背景風險觸發部分賣出"],
    holding_position: ["Continue holding", "繼續持有"],
    stop_loss: ["Stop-loss was reached", "已觸及止蝕"],
    take_profit: ["Profit target was reached", "已達到止賺目標"],
    signal_cooldown_active: ["Waiting for signal cooldown", "等待交易冷靜期"],
    duplicate_signal_suppressed: ["Repeated signal ignored", "重複訊號已忽略"],
    fallback_rule_bullish_trend_momentum: ["Backup rule found bullish trend", "後備規則看到上升趨勢"],
    fallback_rule_bearish_trend: ["Backup rule found bearish trend", "後備規則看到下跌趨勢"],
    fallback_rule_neutral_hold: ["Backup rules are neutral", "後備規則目前中性"],
  };
  const [en, zh] = reasons[normalized] || [String(reason || "No action"), String(reason || "未有動作")];
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
  if (prediction > 0 && confidence >= 0.55) return "high_blocked";
  if (prediction > 0) return "watching";
  return "low_now";
}

function buyPotentialText(keyOrItem, languageMode) {
  const key = typeof keyOrItem === "string" ? keyOrItem : getBuyPotentialKey(keyOrItem);
  const labels = {
    bought: ["Bought", "已買入"],
    high_blocked: ["High, but blocked", "高，但受規則限制"],
    watching: ["Watching", "觀察中"],
    low_now: ["Low now", "目前較低"],
    holding: ["Holding", "持有中"],
    sell_action: ["Sell action", "賣出動作"],
  };
  const [en, zh] = labels[key] || labels.low_now;
  return labelByMode(languageMode, en, zh);
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
  }, [currentWatchlist, selectedTicker]);

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
    setLiveDecisionLog(payload.latest_decisions || []);
    setSelectedLiveTrade((payload.latest_decisions || [])[0] || null);
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
          fetchLiveVirtualTraderTrades(profileId, null, DECISION_HISTORY_LIMIT),
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
        fetchVirtualTraderSummary(activeTicker, DEFAULT_PERIOD, null, 240, profileId),
        fetchVirtualTraderTrades(activeTicker, DEFAULT_PERIOD, null, 100, profileId),
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

  const cashValue = Number(cashAmount);
  const canSubmitCash = Number.isFinite(cashValue) && cashValue > 0;

  async function handleDeposit() {
    if (!profileId || !canSubmitCash) return;
    setError("");
    try {
      await postVirtualAccountDeposit(profileId, cashValue, cashReason);
      setCashAmount("");
      setCashReason("");
      await loadGlobalViews();
    } catch (requestError) {
      setError(requestError.message || "Failed to deposit cash.");
    }
  }

  async function handleWithdraw() {
    if (!profileId || !canSubmitCash) return;
    setError("");
    try {
      await postVirtualAccountWithdraw(profileId, cashValue, cashReason);
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
          <p>{labelByMode(languageMode, "A simple virtual trading view for learning and paper trading.", ZH.intro)}</p>
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
            {labelByMode(languageMode, "Retry", ZH.retry)}
          </button>
        </div>
      ) : null}

      {isLoading ? (
        <section className="panel">
          <p>{labelByMode(languageMode, "Loading your simulation account...", ZH.loadingAccount)}</p>
        </section>
      ) : null}

      <section className="panel">
        <h3>{labelByMode(languageMode, "Latest Stock Action", ZH.latestStockAction)}</h3>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            "Top paper-trading opportunities from the market universe are shown. Executed actions appear first, and watchlist tickers are marked.",
            "顯示整個市場範圍內的主要模擬交易機會。已執行的動作會優先顯示，觀察清單股票會清楚標記。"
          )}
        </p>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            "Educational simulation only. Whole-share trades are used, normal sell signals reduce about 50% of a holding, stop-loss sells all shares, and each trade includes HKD 50 cost.",
            "只作教育模擬用途。交易使用整數股；一般賣出訊號會減持約 50%；止蝕會賣出全部持倉；每次交易計入 50 港元成本。"
          )}
        </p>
        <div className="action-filter-panel">
          <div className="action-filter-title">
            {labelByMode(languageMode, "Group by buy potential", ZH.groupBuyPotential)}
          </div>
          <div className="action-filter-bar" aria-label={labelByMode(languageMode, "Filter buy potential", ZH.filterBuyPotential)}>
            {BUY_POTENTIAL_FILTERS.map((filterValue) => (
              <button
                key={filterValue}
                type="button"
                className={buyPotentialFilter === filterValue ? "active" : ""}
                onClick={() => setBuyPotentialFilter(filterValue)}
              >
                {filterValue === "all"
                  ? labelByMode(languageMode, "All", ZH.all)
                  : buyPotentialText(filterValue, languageMode)}
                <span>{buyPotentialCounts[filterValue] || 0}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="table-wrap beginner-action-table responsive-card-table">
          <table>
            <thead>
              <tr>
                <th>{labelByMode(languageMode, "Ticker", ZH.ticker)}</th>
                <th>{labelByMode(languageMode, "Source", ZH.source)}</th>
                <th>{labelByMode(languageMode, "Action", ZH.action)}</th>
                <th>{labelByMode(languageMode, "Buy potential", ZH.buyPotential)}</th>
                <th>{labelByMode(languageMode, "Reason", ZH.reason)}</th>
                <th>{labelByMode(languageMode, "Model used", ZH.modelUsed)}</th>
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
                    <td data-label={labelByMode(languageMode, "Ticker", ZH.ticker)}>{item.ticker}</td>
                    <td data-label={labelByMode(languageMode, "Source", ZH.source)}>
                      <span className={item.is_watchlist ? "ticker-source-watchlist" : "ticker-source-market"}>
                        {item.is_watchlist
                          ? labelByMode(languageMode, "Watchlist", ZH.watchlist)
                          : labelByMode(languageMode, "Market", ZH.market)}
                      </span>
                    </td>
                    <td data-label={labelByMode(languageMode, "Action", ZH.action)}>{actionText(item.action, languageMode)}</td>
                    <td data-label={labelByMode(languageMode, "Buy potential", ZH.buyPotential)}>{buyPotentialText(item, languageMode)}</td>
                    <td data-label={labelByMode(languageMode, "Reason", ZH.reason)}>{decisionReasonText(item.reason, languageMode)}</td>
                    <td data-label={labelByMode(languageMode, "Model used", ZH.modelUsed)}>
                      {item.model_name || "N/A"}
                      {(item.metadata?.model_period || item.model_period)
                        ? ` (${item.metadata?.model_period || item.model_period})`
                        : ""}
                    </td>
                    <td data-label={labelByMode(languageMode, "Price", ZH.price)}>{formatMoney(item.price)}</td>
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
                      actionRows.length ? ZH.noFilterMatch : ZH.noRecentDecisions
                    )}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {selectedLiveTrade ? (
          <div className="decision-context-box">
            <p>
              <strong>{labelByMode(languageMode, "Reason detail", ZH.reasonDetail)}:</strong>{" "}
              {selectedLiveTrade.threshold_summary || selectedLiveTrade.reason}
            </p>
            {selectedLiveTrade.metadata?.context_score !== undefined ? (
              <>
                <p>
                  <strong>{labelByMode(languageMode, "Context score", "背景評分")}:</strong>{" "}
                  {Number(selectedLiveTrade.metadata.context_score).toFixed(0)}/100
                  {selectedLiveTrade.metadata.context_label ? ` (${selectedLiveTrade.metadata.context_label})` : ""}
                </p>
                <p>{selectedLiveTrade.metadata.context_summary}</p>
                {selectedLiveTrade.metadata.feedback_summary ? (
                  <p>
                    <strong>{labelByMode(languageMode, "Model learning", "\u6a21\u578b\u5b78\u7fd2")}:</strong>{" "}
                    {Number(selectedLiveTrade.metadata.feedback_summary.sample_count || 0)}{" "}
                    {labelByMode(languageMode, "evaluated outcomes", "\u500b\u5df2\u8a55\u4f30\u7d50\u679c")}
                    {Number.isFinite(Number(selectedLiveTrade.metadata.feedback_summary.direction_accuracy))
                      ? ", " + labelByMode(languageMode, "direction accuracy", "\u65b9\u5411\u6e96\u78ba\u7387") + " " + (
                        Number(selectedLiveTrade.metadata.feedback_summary.direction_accuracy) * 100
                      ).toFixed(0) + "%"
                      : ""}
                  </p>
                ) : null}
                {selectedLiveTrade.metadata.external_context?.sources_available?.length ? (
                  <p>
                    <strong>{labelByMode(languageMode, "Extra feeds used", "已使用額外資料")}:</strong>{" "}
                    {selectedLiveTrade.metadata.external_context.sources_available.join(", ")}
                  </p>
                ) : null}
                {Array.isArray(selectedLiveTrade.metadata.context_factors)
                  && selectedLiveTrade.metadata.context_factors.length > 0 ? (
                    <div className="decision-factor-list" aria-label="Decision factors">
                      {selectedLiveTrade.metadata.context_factors.slice(0, 8).map((factor) => (
                        <span key={factor}>{factor}</span>
                      ))}
                    </div>
                  ) : null}
              </>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="panel">
        <h3>{labelByMode(languageMode, "Total Earn / Loss", ZH.totalEarnLoss)}</h3>
        <div className="beginner-summary-grid">
          <div>
            <span>{labelByMode(languageMode, "Account value", ZH.accountValue)}</span>
            <strong>{formatMoney(accountSummary?.total_account_value)}</strong>
          </div>
          <div className={totalProfitLoss >= 0 ? "pnl-positive" : "pnl-negative"}>
            <span>{labelByMode(languageMode, "Profit / Loss", ZH.profitLoss)}</span>
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
        <h3>{labelByMode(languageMode, "Contributions", ZH.contributions)}</h3>
        <div className="beginner-summary-grid">
          <div>
            <span>{labelByMode(languageMode, "Total cash added", ZH.totalCashAdded)}</span>
            <strong>{formatMoney(accountSummary?.net_deposits)}</strong>
          </div>
          <div>
            <span>{labelByMode(languageMode, "Recent monthly", ZH.recentMonthly)}</span>
            <strong>{formatMoney(contributionSummary.monthly)}</strong>
          </div>
          <div>
            <span>{labelByMode(languageMode, "Recent one-time", ZH.recentOneTime)}</span>
            <strong>{formatMoney(contributionSummary.oneTime)}</strong>
          </div>
        </div>
      </section>

      <MonthlyContributionInput userId={profileId} languageMode={languageMode} onUpdated={loadGlobalViews} />

      <section className="panel">
        <h3>{labelByMode(languageMode, "One-Time Cash Add / Withdraw", ZH.oneTimeCash)}</h3>
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
            <button type="button" onClick={handleDeposit} disabled={!canSubmitCash}>
              {labelByMode(languageMode, "Add deposit", ZH.deposit)}
            </button>
            <button type="button" onClick={handleWithdraw} disabled={!canSubmitCash}>
              {labelByMode(languageMode, "Add withdrawal", ZH.withdraw)}
            </button>
          </div>
          {!canSubmitCash && cashAmount ? (
            <p className="helper-text">
              {labelByMode(languageMode, "Enter an amount greater than 0.", "請輸入大於 0 的金額。")}
            </p>
          ) : null}
        </div>
        <ResetTradingAccountButton userId={profileId} languageMode={languageMode} onResetComplete={loadGlobalViews} />
      </section>

      <RecentTradesTable languageMode={languageMode} trades={recentTrades} />

      <section className="panel">
        <h3>{labelByMode(languageMode, "Advanced Details", ZH.advancedDetails)}</h3>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            "Open this only when you want scheduler status, charts, news sentiment, account history, or replay data.",
            "只在需要查看排程狀態、圖表、新聞情緒、帳戶歷史或回測資料時才打開。"
          )}
        </p>
        <div className="settings-actions">
          <button type="button" onClick={() => setAdvancedEnabled((value) => !value)}>
            {advancedEnabled
              ? labelByMode(languageMode, "Hide advanced details", ZH.hideAdvanced)
              : labelByMode(languageMode, "Show advanced details", ZH.showAdvanced)}
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
                  "News sentiment is optional and may be slower, so it loads only when requested.",
                  "新聞情緒屬於選擇性資料，可能較慢，所以只會在需要時載入。"
                )}
              </p>
              <div className="settings-actions">
                <button type="button" onClick={() => setNewsEnabled(true)}>
                  {labelByMode(languageMode, "Load news sentiment", ZH.loadNews)}
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
                title={labelByMode(languageMode, "Historical Replay Equity Curve", ZH.historicalEquity)}
                subtitle={labelByMode(languageMode, `Ticker: ${selectedTicker}`, `股票代號: ${selectedTicker}`)}
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
              <p>{getLabel(languageMode, "loading")}</p>
            </section>
          )}
        </>
      ) : null}
    </>
  );
}

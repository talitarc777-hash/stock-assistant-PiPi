import React, { useEffect, useMemo, useRef, useState } from "react";

import {
  fetchTraderSchedulerStatus,
  fetchLiveVirtualTraderStatus,
  fetchLiveVirtualTraderSync,
  fetchLiveVirtualTraderTrades,
  fetchModelLifecycleRegistry,
  fetchVirtualAccountHistory,
  fetchVirtualAccountRecentTrades,
  fetchVirtualTraderTrades,
  fetchVirtualTraderSummary,
  postVirtualAccountDeposit,
  postVirtualAccountWithdraw,
  runLiveVirtualTraderNow,
} from "../api";
import { getLabel } from "../constants/i18n";
import {
  addUserWatchlistTicker,
  fetchUserWatchlist,
  removeUserWatchlistTicker,
} from "../services/userProfileApi";
import EquityChart from "../components/EquityChart";
import HoldingsTable from "../components/HoldingsTable";
import LineChart from "../components/LineChart";
import MonthlyContributionInput from "../components/MonthlyContributionInput";
import NewsSentimentPanel from "../components/NewsSentimentPanel";
import RecentRunsPanel from "../components/RecentRunsPanel";
import RecentTradesTable from "../components/RecentTradesTable";
import ResetTradingAccountButton from "../components/ResetTradingAccountButton";
import TickerIdentity from "../components/TickerIdentity";
import TickerHistorySummary from "../components/TickerHistorySummary";
import TransactionHistoryTable from "../components/TransactionHistoryTable";
import { marketDataReasonText, marketRegimeGuide } from "../utils/decisionExplanations";
import { formatModelRate } from "../utils/modelMetrics";
import { modelUsedText } from "../utils/tradeModelProvenance";
import { tickerDisplayName } from "../utils/tickerIdentity";

const DEFAULT_PERIOD = "5y";
const AUTO_TRADING_MODEL = "auto_best";
const HISTORY_PAGE_SIZE = 120;
const DECISION_HISTORY_LIMIT = 160;
const SCHEDULER_REFRESH_MS = 60000;
const LIVE_SYNC_REFRESH_MS = 5000;
const BUY_POTENTIAL_FILTERS = ["all", "bought", "high_blocked", "watching", "low_now", "holding", "sell_action"];
const BEGINNER_GUIDE_STORAGE_KEY = "stock-assistant-hide-beginner-guide";

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

function normalizeHkTickerInput(value) {
  const match = String(value || "").trim().toUpperCase().match(/^(\d{1,4})(?:\.HK)?$/);
  if (!match) return null;
  const code = Number(match[1]);
  return code >= 1 && code <= 9999 ? String(code).padStart(4, "0") : null;
}

function formatPercent(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "";
  return ` (${numeric.toFixed(2)}%)`;
}

function formatRiskValue(value, suffix = "") {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric.toFixed(2)}${suffix}` : "Not enough history";
}

function confidenceGuide(value, languageMode) {
  const confidence = Number(value);
  if (!Number.isFinite(confidence)) {
    return {
      value: labelByMode(languageMode, "Not available", "未有資料"),
      level: "unknown",
      explanation: labelByMode(languageMode, "No comparable confidence score is available.", "未有可比較的信心評分。"),
    };
  }
  const percent = confidence * 100;
  const level = percent >= 75 ? "high" : percent >= 55 ? "medium" : "low";
  const labels = {
    high: ["Higher model confidence", "較高模型信心"],
    medium: ["Moderate model confidence", "中等模型信心"],
    low: ["Low model confidence", "較低模型信心"],
  };
  return {
    value: `${percent.toFixed(0)}% · ${labelByMode(languageMode, ...labels[level])}`,
    level,
    explanation: labelByMode(
      languageMode,
      "This measures how strongly the model supports its signal, not the chance of making a profit.",
      "這表示模型支持訊號的程度，並不代表獲利機率。"
    ),
  };
}

function contextGuide(value, languageMode) {
  const score = Number(value);
  if (!Number.isFinite(score)) {
    return {
      value: labelByMode(languageMode, "Not evaluated", "未有評估"),
      level: "unknown",
    };
  }
  const level = score >= 60 ? "high" : score >= 40 ? "medium" : "low";
  const labels = {
    high: ["Supportive conditions", "市況較支持"],
    medium: ["Mixed conditions", "市況好壞參半"],
    low: ["Cautious conditions", "市況需要審慎"],
  };
  return {
    value: `${score.toFixed(0)}/100 · ${labelByMode(languageMode, ...labels[level])}`,
    level,
  };
}

function stockScoreText(item) {
  const score = Number(item?.metadata?.overall_score);
  return Number.isFinite(score) ? `${score.toFixed(0)}/100` : "N/A";
}

function decisionModelText(item, languageMode) {
  return modelUsedText(item, languageMode);
}

function decisionModelStatusText(item, languageMode) {
  const status = String(item?.metadata?.model_status || "").toLowerCase();
  if (status === "ready") return labelByMode(languageMode, "Ready", "已就緒");
  if (status === "training_pending") {
    return labelByMode(languageMode, "Training pending", "等待訓練");
  }
  if (status === "fallback" || item?.metadata?.decision_source === "fallback_rule") {
    return labelByMode(languageMode, "Fallback", "後備狀態");
  }
  return labelByMode(languageMode, "Unknown", "未知");
}

function compactDateTime(value) {
  if (!value) return "N/A";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
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
    portfolio_drawdown_pause: ["Portfolio losses paused new buying", "投資組合虧損觸發暫停新買入"],
    portfolio_drawdown_reduction: ["Critical portfolio loss triggered risk reduction", "投資組合嚴重虧損觸發減持"],
    market_data_quality_block: ["Market data was stale or inconsistent, so buying was blocked", "市場資料過時或不一致，因此阻止買入"],
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

export default function VirtualTraderPage({
  languageMode,
  currentWatchlist,
  profileId,
  onWatchlistSynced,
}) {
  const [market, setMarket] = useState("US");
  const [hkTickers, setHkTickers] = useState(["0005", "0700", "1810", "3690", "9988"]);
  const [hkTickerInput, setHkTickerInput] = useState("0700");
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
  const [showBeginnerGuide, setShowBeginnerGuide] = useState(
    () => window.localStorage.getItem(BEGINNER_GUIDE_STORAGE_KEY) !== "true"
  );
  const [lastSyncedAt, setLastSyncedAt] = useState(null);
  const [liveSyncError, setLiveSyncError] = useState("");
  const [modelRegistry, setModelRegistry] = useState([]);
  const [modelRegistryError, setModelRegistryError] = useState("");
  const liveSyncInFlight = useRef(false);
  const tickerDetailRef = useRef(null);
  const activeWatchlist = useMemo(
    () => (market === "HK" ? hkTickers : currentWatchlist),
    [currentWatchlist, hkTickers, market]
  );
  const currencySymbol = market === "HK" ? "HK$" : "$";

  function hideBeginnerGuide() {
    window.localStorage.setItem(BEGINNER_GUIDE_STORAGE_KEY, "true");
    setShowBeginnerGuide(false);
  }

  function restoreBeginnerGuide() {
    window.localStorage.removeItem(BEGINNER_GUIDE_STORAGE_KEY);
    setShowBeginnerGuide(true);
  }

  function selectMarket(nextMarket) {
    if (nextMarket === market) return;
    setMarket(nextMarket);
    setSelectedTicker(nextMarket === "HK" ? (hkTickers[0] || "0700") : (currentWatchlist[0] || "VOO"));
    setLiveStatus(null);
    setAccountSummary(null);
    setAccountHoldings([]);
    setRecentTrades([]);
    setLiveDecisionLog([]);
    setSelectedLiveTrade(null);
    setAccountHistory([]);
    setHistoricalEnabled(false);
    setHistoryEnabled(false);
    setError("");
  }

  function showTickerDetail(item) {
    setSelectedLiveTrade(item);
    window.requestAnimationFrame(() => {
      tickerDetailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  async function activateHkTicker() {
    const ticker = normalizeHkTickerInput(hkTickerInput);
    if (!ticker) {
      setError("Enter an HK code from 1 to 4 digits, for example 700, 0700, or 0700.HK.");
      return;
    }
    try {
      const payload = await addUserWatchlistTicker({
        user_id: profileId,
        ticker,
        market: "HK",
        last_active_source: "dashboard",
      });
      const watchlist = Array.isArray(payload?.watchlist) ? payload.watchlist : [];
      setHkTickers(watchlist);
      setSelectedTicker(ticker);
      setHkTickerInput(ticker);
      setError("");
    } catch (requestError) {
      setError(requestError.message || "Could not add this HK ticker.");
    }
  }

  async function deactivateSelectedHkTicker() {
    if (!profileId || !selectedTicker) return;
    try {
      const payload = await removeUserWatchlistTicker({
        user_id: profileId,
        ticker: selectedTicker,
        market: "HK",
        last_active_source: "dashboard",
      });
      const watchlist = Array.isArray(payload?.watchlist) ? payload.watchlist : [];
      setHkTickers(watchlist);
      setSelectedTicker(watchlist[0] || "0700");
      setError("");
    } catch (requestError) {
      setError(requestError.message || "Could not deactivate this HK ticker.");
    }
  }

  useEffect(() => {
    if (!activeWatchlist.length) return;
    if (!activeWatchlist.includes(selectedTicker)) {
      setSelectedTicker(activeWatchlist[0]);
    }
  }, [activeWatchlist, selectedTicker]);

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
      portfolio_risk_level: account.portfolio_risk_level,
      performance_vs_contributions_pct: account.performance_vs_contributions_pct,
      buying_paused: account.buying_paused,
      position_size_multiplier: account.position_size_multiplier,
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
      const [liveStatusResult, schedulerStatusResult, recentTradesResult, decisionHistoryResult, hkWatchlistResult] =
        await Promise.allSettled([
          fetchLiveVirtualTraderStatus(profileId, null, AUTO_TRADING_MODEL, false, market),
          fetchTraderSchedulerStatus(24),
          fetchVirtualAccountRecentTrades(profileId, 20, market),
          fetchLiveVirtualTraderTrades(profileId, null, DECISION_HISTORY_LIMIT, market),
          market === "HK" ? fetchUserWatchlist(profileId, "HK") : Promise.resolve(null),
        ]);

      if (schedulerStatusResult.status === "fulfilled") setSchedulerStatus(schedulerStatusResult.value);
      if (liveStatusResult.status === "fulfilled") applyLiveStatusPayload(liveStatusResult.value);
      if (recentTradesResult.status === "fulfilled") setRecentTrades(recentTradesResult.value.trades || []);
      if (decisionHistoryResult.status === "fulfilled") {
        const decisions = decisionHistoryResult.value.trades || [];
        setLiveDecisionLog(decisions);
        setSelectedLiveTrade(decisions[0] || null);
      }
      if (market === "HK" && hkWatchlistResult.status === "fulfilled") {
        const watchlist = hkWatchlistResult.value?.watchlist || [];
        if (watchlist.length) setHkTickers(watchlist);
      }
      if ([liveStatusResult, recentTradesResult, decisionHistoryResult].some(
        (result) => result.status === "fulfilled"
      )) {
        setLastSyncedAt(new Date());
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
      const payload = await fetchVirtualAccountHistory(profileId, HISTORY_PAGE_SIZE, nextOffset, market);
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

  async function loadSelectedTickerModels() {
    if (!selectedTicker) return;
    try {
      const rows = await fetchModelLifecycleRegistry(60, {
        market,
        ticker: selectedTicker,
        targetName: "target_5d_return",
      });
      setModelRegistry(Array.isArray(rows) ? rows : []);
      setModelRegistryError("");
    } catch (requestError) {
      setModelRegistry([]);
      setModelRegistryError(requestError.message || "Model registry could not be loaded.");
    }
  }

  async function loadLiveSyncOnly() {
    if (!profileId || liveSyncInFlight.current) return;
    liveSyncInFlight.current = true;
    try {
      const payload = await fetchLiveVirtualTraderSync(
        profileId,
        20,
        DECISION_HISTORY_LIMIT,
        market
      );
      const syncedWatchlist = Array.isArray(payload.watchlist) ? payload.watchlist : [];
      if (
        market === "US" && onWatchlistSynced
        && syncedWatchlist.join(",") !== currentWatchlist.join(",")
      ) {
        onWatchlistSynced(syncedWatchlist);
      }
      if (market === "HK" && syncedWatchlist.length) {
        setHkTickers(syncedWatchlist);
      }
      applyLiveStatusPayload(payload.status);
      setRecentTrades(payload.recent_trades || []);
      const decisions = payload.decisions || [];
      setLiveDecisionLog(decisions);
      setSelectedLiveTrade((current) => {
        if (!current) return decisions[0] || null;
        return decisions.find((item) => item.timestamp === current.timestamp)
          || decisions[0]
          || null;
      });
      setLiveSyncError("");
      setLastSyncedAt(payload.synced_at_utc ? new Date(payload.synced_at_utc) : new Date());
    } catch (requestError) {
      setLiveSyncError(
        requestError.message
          || labelByMode(languageMode, "Live synchronization is temporarily unavailable.", "即時同步暫時未能使用。")
      );
    } finally {
      liveSyncInFlight.current = false;
    }
  }

  useEffect(() => {
    loadGlobalViews();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId, market]);

  useEffect(() => {
    if (!profileId) return undefined;
    const refreshWhenVisible = () => {
      if (!document.hidden) loadLiveSyncOnly();
    };
    refreshWhenVisible();
    const timer = window.setInterval(refreshWhenVisible, LIVE_SYNC_REFRESH_MS);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    window.addEventListener("focus", refreshWhenVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
      window.removeEventListener("focus", refreshWhenVisible);
    };
  }, [profileId, languageMode, currentWatchlist, onWatchlistSynced, market]);

  useEffect(() => {
    if (!historyEnabled) return;
    loadAccountHistoryPage({ reset: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId, historyEnabled, market]);

  useEffect(() => {
    if (!historicalEnabled) return;
    loadHistoricalReplayData(selectedTicker);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId, selectedTicker, historicalEnabled, market]);

  useEffect(() => {
    if (!profileId) return undefined;
    const timer = window.setInterval(() => {
      if (!document.hidden) loadSchedulerStatusOnly();
    }, SCHEDULER_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [profileId]);

  useEffect(() => {
    if (!advancedEnabled) return;
    loadSelectedTickerModels();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [advancedEnabled, market, selectedTicker]);

  async function handleRunNow() {
    if (!profileId) return;
    setIsRunningNow(true);
    setError("");
    try {
      await runLiveVirtualTraderNow(
        profileId,
        null,
        AUTO_TRADING_MODEL,
        market
      );
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
      await postVirtualAccountDeposit(profileId, cashValue, cashReason, market);
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
      await postVirtualAccountWithdraw(profileId, cashValue, cashReason, market);
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
      activeWatchlist.map((ticker) => String(ticker).trim().toUpperCase()).filter(Boolean)
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

    const sortedRows = [...latestByTicker.values()].sort(
      (left, right) =>
        decisionOpportunityScore(right, right.is_watchlist)
        - decisionOpportunityScore(left, left.is_watchlist)
    );
    return market === "HK" ? sortedRows : sortedRows.slice(0, 15);
  }, [activeWatchlist, liveDecisionLog, market]);

  const tickerNames = useMemo(() => {
    const names = new Map();
    for (const item of [...liveDecisionLog, ...recentTrades, ...accountHistory, ...accountHoldings]) {
      const ticker = String(item?.ticker || "").trim().toUpperCase();
      const name = tickerDisplayName(item, ticker);
      if (ticker && name && !names.has(ticker)) names.set(ticker, name);
    }
    return names;
  }, [accountHistory, accountHoldings, liveDecisionLog, recentTrades]);

  const holdingsWithNames = useMemo(
    () => accountHoldings.map((item) => ({
      ...item,
      ticker_name: tickerDisplayName(item, item.ticker) || tickerNames.get(item.ticker) || null,
    })),
    [accountHoldings, tickerNames]
  );

  const recentTradesWithNames = useMemo(
    () => recentTrades.map((item) => ({
      ...item,
      ticker_name: tickerDisplayName(item, item.ticker) || tickerNames.get(item.ticker) || null,
    })),
    [recentTrades, tickerNames]
  );

  const accountHistoryWithNames = useMemo(
    () => accountHistory.map((item) => ({
      ...item,
      ticker_name: tickerDisplayName(item, item.ticker) || tickerNames.get(item.ticker) || null,
    })),
    [accountHistory, tickerNames]
  );

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
          <div className={`live-sync-status ${liveSyncError ? "warning" : ""}`} role="status">
            <span className="live-sync-dot" aria-hidden="true" />
            <span>
              {liveSyncError
                || (lastSyncedAt
                  ? labelByMode(
                    languageMode,
                    `Synced ${lastSyncedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`,
                    `已同步 ${lastSyncedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`
                  )
                  : labelByMode(languageMode, "Live sync starting", "即時同步啟動中"))}
            </span>
          </div>
          <button type="button" onClick={handleRunNow} disabled={isRunningNow}>
            {isRunningNow
              ? labelByMode(languageMode, "Running...", ZH.running)
              : labelByMode(languageMode, "Update decisions", ZH.runNow)}
          </button>
        </div>
      </header>

      <section className="panel virtual-market-controls" aria-label="Virtual trader market">
        <div className="market-selector" role="tablist" aria-label="Market">
          {[
            ["US", "US / 美股"],
            ["HK", "HK / 港股"],
          ].map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={market === value}
              className={market === value ? "active" : ""}
              onClick={() => selectMarket(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="market-context">
          <strong>{market === "HK" ? "HK Virtual Trader" : "US Virtual Trader"}</strong>
          <span>{market === "HK" ? "HKD (HK$) · Asia/Hong_Kong" : "USD ($) · America/New_York"}</span>
        </div>
        {market === "HK" ? (
          <div className="hk-ticker-control">
            <label htmlFor="hk-ticker-input">
              {labelByMode(languageMode, "HK ticker", "港股代號")}
            </label>
            <input
              id="hk-ticker-input"
              value={hkTickerInput}
              inputMode="numeric"
              placeholder="700, 0700, or 0700.HK"
              onChange={(event) => setHkTickerInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") activateHkTicker();
              }}
            />
            <button type="button" onClick={activateHkTicker}>
              {labelByMode(languageMode, "Use ticker", "使用此代號")}
            </button>
            <select
              aria-label={labelByMode(languageMode, "Active HK ticker", "目前港股")}
              value={selectedTicker}
              onChange={(event) => setSelectedTicker(event.target.value)}
            >
              {hkTickers.map((ticker) => (
                <option key={ticker} value={ticker}>
                  {ticker}{tickerNames.get(ticker) ? ` — ${tickerNames.get(ticker)}` : ""}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="secondary-button"
              onClick={deactivateSelectedHkTicker}
            >
              {labelByMode(languageMode, "Deactivate selected", "停用所選股票")}
            </button>
            <span className="helper-text">
              {labelByMode(
                languageMode,
                `${selectedTicker} is fetched from Yahoo as ${selectedTicker}.HK. Missing models train once in the background and are then reused.`,
                `${selectedTicker} 會以 ${selectedTicker}.HK 向 Yahoo 取數；如無模型，會在背景訓練一次後重用。`
              )}
            </span>
          </div>
        ) : null}
      </section>

      {showBeginnerGuide ? (
        <section className="panel beginner-guide">
          <div className="beginner-guide-heading">
            <div>
              <span className="eyebrow">{labelByMode(languageMode, "New here?", "第一次使用？")}</span>
              <h2>{labelByMode(languageMode, "Start virtual trading in four steps", "四步開始模擬交易")}</h2>
              <p className="helper-text">
                {labelByMode(
                  languageMode,
                  "No real money is used. Follow these steps to learn what the trader does before judging its results.",
                  "不會使用真實金錢。請依照以下步驟了解交易員的運作，再評估結果。"
                )}
              </p>
            </div>
            <button type="button" className="secondary-button" onClick={hideBeginnerGuide}>
              {labelByMode(languageMode, "Hide guide", "隱藏指南")}
            </button>
          </div>
          <ol className="beginner-guide-steps">
            <li>
              <strong>{labelByMode(languageMode, "Set up virtual cash", "設定模擬資金")}</strong>
              <span>{labelByMode(languageMode, "Add a one-time amount or a monthly contribution. Deposits are not profit.", "加入一次性金額或每月供款；入金並不等於盈利。")}</span>
            </li>
            <li>
              <strong>{labelByMode(languageMode, "Choose what to watch", "選擇觀察項目")}</strong>
              <span>{labelByMode(languageMode, "Use Settings to maintain a small watchlist. Broad ETFs are easier starting examples than concentrated bets.", "在設定中管理精簡觀察名單；廣泛 ETF 比集中押注更適合作為入門例子。")}</span>
              <a href="/settings">{labelByMode(languageMode, "Open Settings", "開啟設定")}</a>
            </li>
            <li>
              <strong>{labelByMode(languageMode, "Update and read the decision", "更新並閱讀決定")}</strong>
              <span>{labelByMode(languageMode, "Action says what happened; reason says why; confidence is signal strength, not profit probability.", "動作表示發生甚麼，原因解釋為何；信心是訊號強度，並非獲利機率。")}</span>
            </li>
            <li>
              <strong>{labelByMode(languageMode, "Connect Discord", "連接 Discord")}</strong>
              <span>{labelByMode(languageMode, "Generate a one-time code in Settings so both places use the same account and information.", "在設定產生一次性代碼，令兩邊使用相同帳戶及資料。")}</span>
              <a href="/settings">{labelByMode(languageMode, "Link Discord", "連接 Discord")}</a>
            </li>
          </ol>
          <div className="beginner-guide-note">
            <strong>{labelByMode(languageMode, "Remember", "請記住")}</strong>
            <span>{labelByMode(languageMode, "A good decision process can still lose money. Compare results with the benchmark and check drawdown, not profit alone.", "良好決策仍可能虧損。除了盈利，亦要與基準比較並檢查最大跌幅。")}</span>
            <a href="/glossary">{labelByMode(languageMode, "Look up unfamiliar terms", "查閱不熟悉的詞語")}</a>
          </div>
        </section>
      ) : (
        <button type="button" className="restore-beginner-guide" onClick={restoreBeginnerGuide}>
          {labelByMode(languageMode, "Show getting-started guide", "顯示入門指南")}
        </button>
      )}

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
            market === "HK"
              ? "Educational simulation only. HK trades use each security's board lot, normal sell signals reduce about 50% of a holding, stop-loss sells all lots, and each trade includes an HK$50 simulated cost."
              : "Educational simulation only. US trades use whole shares, normal sell signals reduce about 50% of a holding, stop-loss sells all shares, and the configured administrative cost is converted to USD.",
            market === "HK"
              ? "只作教育模擬用途。港股交易按每手股數執行；一般賣出訊號會減持約 50%；止蝕會賣出所有完整手數；每次交易計入 50 港元模擬成本。"
              : "只作教育模擬用途。美股交易使用整數股；一般賣出訊號會減持約 50%；止蝕會賣出全部持倉；管理成本會換算為美元。"
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
                <th>{labelByMode(languageMode, "Score", "\u5206\u6578")}</th>
                <th>{labelByMode(languageMode, "Source", ZH.source)}</th>
                <th>{labelByMode(languageMode, "Action", ZH.action)}</th>
                <th>{labelByMode(languageMode, "Buy potential", ZH.buyPotential)}</th>
                <th>{labelByMode(languageMode, "Reason", ZH.reason)}</th>
                <th>{labelByMode(languageMode, "Model used", ZH.modelUsed)}</th>
                <th>{labelByMode(languageMode, "Model status", "模型狀態")}</th>
                <th>{labelByMode(languageMode, "Price", ZH.price)}</th>
              </tr>
            </thead>
            <tbody>
              {filteredActionRows.length ? (
                filteredActionRows.map((item) => (
                  <tr
                    key={`${item.timestamp}-${item.ticker}-${item.action}`}
                    className={selectedLiveTrade?.timestamp === item.timestamp ? "selected-row" : ""}
                    onClick={() => showTickerDetail(item)}
                  >
                    <td data-label={labelByMode(languageMode, "Ticker", ZH.ticker)}>
                      <button
                        type="button"
                        className="ticker-dashboard-link"
                        onClick={(event) => {
                          event.stopPropagation();
                          showTickerDetail(item);
                        }}
                        aria-label={labelByMode(
                          languageMode,
                          `Show ${item.ticker} details`,
                          `顯示 ${item.ticker} 詳情`
                        )}
                      >
                        <TickerIdentity ticker={item.ticker} data={item} languageMode={languageMode} />
                      </button>
                    </td>
                    <td data-label={labelByMode(languageMode, "Score", "\u5206\u6578")}>
                      <strong>{stockScoreText(item)}</strong>
                    </td>
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
                      {decisionModelText(item, languageMode)}
                    </td>
                    <td data-label={labelByMode(languageMode, "Model status", "模型狀態")}>
                      {decisionModelStatusText(item, languageMode)}
                    </td>
                    <td data-label={labelByMode(languageMode, "Price", ZH.price)}>{currencySymbol}{formatMoney(item.price)}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={9}>
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
          <div className="decision-context-box" ref={tickerDetailRef}>
            <TickerHistorySummary
              ticker={selectedLiveTrade.ticker}
              classification={selectedLiveTrade}
              languageMode={languageMode}
              market={market}
            />
            {market === "HK" ? (
              <p>
                <strong>{labelByMode(languageMode, "Board lot", "每手股數")}:</strong>{" "}
                {selectedLiveTrade.metadata?.board_lot || labelByMode(languageMode, "Unavailable; buying is blocked", "未有可靠資料；已阻止買入")}
              </p>
            ) : null}
            {(() => {
              const confidence = confidenceGuide(selectedLiveTrade.confidence_score, languageMode);
              const context = contextGuide(selectedLiveTrade.metadata?.context_score, languageMode);
              const validationStatus = selectedLiveTrade.metadata?.model_validation_status;
              const benchmarkShadow = selectedLiveTrade.metadata?.benchmark_shadow;
              const usesValidatedModel = validationStatus === "validated";
              const usesSafetyFallback = validationStatus === "safety_fallback";
              const regimeGuide = marketRegimeGuide(selectedLiveTrade.metadata?.market_regime, languageMode);
              const action = String(selectedLiveTrade.action || "no_action").toLowerCase();
              const quantity = Number(selectedLiveTrade.quantity);
              const executed =
                (action === "buy" || action === "sell")
                && Number.isFinite(quantity)
                && quantity > 0;
              return (
                <div className="decision-explainer">
                  <div className="decision-explainer-heading">
                    <div>
                      <span>{labelByMode(languageMode, "What this means", "這代表甚麼")}</span>
                      <strong className="ticker-identity">
                        <span>{actionText(selectedLiveTrade.action, languageMode)}</span>
                        <TickerIdentity
                          ticker={selectedLiveTrade.ticker}
                          data={selectedLiveTrade}
                          languageMode={languageMode}
                        />
                      </strong>
                    </div>
                    <span className={`decision-status-pill ${executed ? "executed" : ""}`}>
                      {executed
                        ? labelByMode(
                          languageMode,
                          `Simulated trade: ${quantity} share${quantity === 1 ? "" : "s"}`,
                          `模擬交易：${quantity} 股`
                        )
                        : labelByMode(languageMode, "No trade was placed", "沒有執行交易")}
                    </span>
                  </div>
                  <div className="decision-guide-grid">
                    <div className={`decision-guide-card ${confidence.level}`}>
                      <span>{labelByMode(languageMode, "Model confidence", "模型信心")}</span>
                      <strong>{confidence.value}</strong>
                      <small>{confidence.explanation}</small>
                      {Number.isFinite(Number(selectedLiveTrade.metadata?.prediction_uncertainty?.out_of_sample_error_pct)) ? (
                        <small className="uncertainty-detail">
                          {labelByMode(
                            languageMode,
                            `Predicted 5-day return: ${formatRiskValue(selectedLiveTrade.metadata.prediction_uncertainty.predicted_return_pct, "%")}. Typical out-of-sample error: ±${formatRiskValue(selectedLiveTrade.metadata.prediction_uncertainty.out_of_sample_error_pct, "%")}.`,
                            `預測 5 日回報：${formatRiskValue(selectedLiveTrade.metadata.prediction_uncertainty.predicted_return_pct, "%")}。一般樣本外誤差：±${formatRiskValue(selectedLiveTrade.metadata.prediction_uncertainty.out_of_sample_error_pct, "%")}。`
                          )}
                        </small>
                      ) : null}
                    </div>
                    <div className={`decision-guide-card ${context.level}`}>
                      <span>{labelByMode(languageMode, "Market context", "市場背景")}</span>
                      <strong>{context.value}</strong>
                      <small>
                        {labelByMode(
                          languageMode,
                          "Combines technical, news, benchmark, and available external context.",
                          "綜合技術、新聞、基準及可用外部資料。"
                        )}
                      </small>
                      {selectedLiveTrade.metadata?.market_data_quality ? (
                        <small className="uncertainty-detail">
                          {selectedLiveTrade.metadata.market_data_quality.trade_safe
                            ? labelByMode(languageMode, "Market data check passed.", "市場資料檢查通過。")
                            : labelByMode(
                              languageMode,
                              `Market data blocked trading: ${(selectedLiveTrade.metadata.market_data_quality.reasons || []).map((reason) => marketDataReasonText(reason, "en")).join("; ")}`,
                              `市場資料阻止交易：${(selectedLiveTrade.metadata.market_data_quality.reasons || []).map((reason) => marketDataReasonText(reason, "zh")).join("；")}`
                            )}
                        </small>
                      ) : null}
                    </div>
                    {selectedLiveTrade.metadata?.market_regime ? (
                      <div className={`decision-guide-card ${selectedLiveTrade.metadata.market_regime.level === "stress" ? "low" : selectedLiveTrade.metadata.market_regime.level === "caution" ? "medium" : "high"}`}>
                        <span>{labelByMode(languageMode, "Wider-market risk protection", "整體市場風險保護")}</span>
                        <strong>{regimeGuide.label}</strong>
                        <small>{regimeGuide.effect}</small>
                        <small className="uncertainty-detail">{regimeGuide.reasons}</small>
                      </div>
                    ) : null}
                    <div className={`decision-guide-card ${usesValidatedModel ? "high" : "medium"}`}>
                      <span>{labelByMode(languageMode, "Model safety check", "\u6a21\u578b\u5b89\u5168\u6aa2\u67e5")}</span>
                      <strong>
                        {usesValidatedModel
                          ? labelByMode(languageMode, "Validated model", "\u5df2\u9a57\u8b49\u6a21\u578b")
                          : usesSafetyFallback
                          ? labelByMode(languageMode, "Safety fallback", "\u5b89\u5168\u5f8c\u5099\u898f\u5247")
                          : labelByMode(languageMode, "Verification unavailable", "\u672a\u6709\u9a57\u8b49\u8cc7\u6599")}
                      </strong>
                      <small>
                        {usesValidatedModel
                          ? labelByMode(
                            languageMode,
                            "Passed walk-forward accuracy, trading-cost, stability, and drawdown gates.",
                            "\u5df2\u901a\u904e\u6efe\u52d5\u6e2c\u8a66\u3001\u4ea4\u6613\u6210\u672c\u3001\u7a69\u5b9a\u6027\u53ca\u8cc7\u91d1\u56de\u64a4\u8981\u6c42\u3002"
                          )
                          : usesSafetyFallback
                          ? labelByMode(
                            languageMode,
                            "No model passed every quality gate, so an unvalidated saved model was not used.",
                            "\u672a\u6709\u6a21\u578b\u901a\u904e\u6240\u6709\u8cea\u91cf\u8981\u6c42\uff0c\u56e0\u6b64\u4e0d\u6703\u4f7f\u7528\u672a\u9a57\u8b49\u7684\u5df2\u5132\u5b58\u6a21\u578b\u3002"
                          )
                          : labelByMode(
                            languageMode,
                            "This older decision does not contain model-validation evidence.",
                            "\u6b64\u8f03\u65e9\u7684\u6c7a\u5b9a\u672a\u5305\u542b\u6a21\u578b\u9a57\u8b49\u8cc7\u6599\u3002"
                          )}
                      </small>
                    </div>
                    {benchmarkShadow?.status === "available" ? (
                      <div className="decision-guide-card medium">
                        <span>{labelByMode(languageMode, "Research model comparison", "\u7814\u7a76\u6a21\u578b\u6bd4\u8f03")}</span>
                        <strong>
                          {benchmarkShadow.signal === "outperform"
                            ? labelByMode(
                              languageMode,
                              `May beat ${benchmarkShadow.benchmark} over 5 days`,
                              `\u672a\u4f865\u65e5\u53ef\u80fd\u8dd1\u8d0f ${benchmarkShadow.benchmark}`
                            )
                            : labelByMode(
                              languageMode,
                              `Not expected to beat ${benchmarkShadow.benchmark}`,
                              `\u9810\u671f\u672a\u80fd\u8dd1\u8d0f ${benchmarkShadow.benchmark}`
                            )}
                        </strong>
                        <small>
                          {labelByMode(
                            languageMode,
                            `Chance estimated by the model: ${formatRiskValue(Number(benchmarkShadow.outperform_probability) * 100, "%")}. Research only; it cannot place trades.`,
                            `\u6a21\u578b\u4f30\u8a08\u6a5f\u7387\uff1a${formatRiskValue(Number(benchmarkShadow.outperform_probability) * 100, "%")}\u3002\u50c5\u4f5c\u7814\u7a76\uff0c\u4e0d\u6703\u4e0b\u55ae\u3002`
                          )}
                        </small>
                        <small className="uncertainty-detail">
                          {labelByMode(
                            languageMode,
                            `Past cost-adjusted signal average: ${formatRiskValue(benchmarkShadow.average_net_signal_return_pct, "%")}; worst tested path drawdown: ${formatRiskValue(benchmarkShadow.worst_path_drawdown_pct, "%")}. Past tests do not guarantee future profit.`,
                            `\u904e\u5f80\u6263\u9664\u6210\u672c\u5f8c\u7684\u8a0a\u865f\u5e73\u5747\uff1a${formatRiskValue(benchmarkShadow.average_net_signal_return_pct, "%")}\uff1b\u6700\u5dee\u6e2c\u8a66\u8def\u5f91\u56de\u64a4\uff1a${formatRiskValue(benchmarkShadow.worst_path_drawdown_pct, "%")}\u3002\u904e\u5f80\u6e2c\u8a66\u4e0d\u4fdd\u8b49\u672a\u4f86\u7372\u5229\u3002`
                          )}
                        </small>
                        <small className="uncertainty-detail">
                          {Number(benchmarkShadow.forward_evidence?.sample_count || 0) > 0
                            ? labelByMode(
                              languageMode,
                              `Forward check: ${benchmarkShadow.forward_evidence.sample_count} matured predictions, ${benchmarkShadow.forward_evidence.pending_count || 0} waiting for five-day outcomes${benchmarkShadow.forward_evidence.estimated_next_maturity_date ? ` (earliest estimate ${benchmarkShadow.forward_evidence.estimated_next_maturity_date})` : ""}, ${formatRiskValue(Number(benchmarkShadow.forward_evidence.direction_accuracy) * 100, "%")} correct.`,
                              `\u524d\u77bb\u6aa2\u67e5\uff1a${benchmarkShadow.forward_evidence.sample_count} \u500b\u5df2\u5230\u671f\u9810\u6e2c\uff0c${benchmarkShadow.forward_evidence.pending_count || 0} \u500b\u7b49\u5f85\u4e94\u500b\u4ea4\u6613\u65e5\u7d50\u679c${benchmarkShadow.forward_evidence.estimated_next_maturity_date ? `\uff08\u6700\u65e9\u4f30\u8a08 ${benchmarkShadow.forward_evidence.estimated_next_maturity_date}\uff09` : ""}\uff0c\u6b63\u78ba\u7387 ${formatRiskValue(Number(benchmarkShadow.forward_evidence.direction_accuracy) * 100, "%")}\u3002`
                            )
                            : labelByMode(
                              languageMode,
                              `Forward check: 0/${benchmarkShadow.forward_evidence?.minimum_samples_for_promotion || 20} matured; ${benchmarkShadow.forward_evidence?.pending_count || 0} waiting for five-day outcomes${benchmarkShadow.forward_evidence?.estimated_next_maturity_date ? ` (earliest estimate ${benchmarkShadow.forward_evidence.estimated_next_maturity_date}; holidays or delayed data can move it)` : ""}. Historical results are not yet confirmed live.`,
                              `\u524d\u77bb\u6aa2\u67e5\uff1a0/${benchmarkShadow.forward_evidence?.minimum_samples_for_promotion || 20} \u500b\u5df2\u5230\u671f\uff1b${benchmarkShadow.forward_evidence?.pending_count || 0} \u500b\u7b49\u5f85\u4e94\u500b\u4ea4\u6613\u65e5\u7d50\u679c${benchmarkShadow.forward_evidence?.estimated_next_maturity_date ? `\uff08\u6700\u65e9\u4f30\u8a08 ${benchmarkShadow.forward_evidence.estimated_next_maturity_date}\uff1b\u5047\u671f\u6216\u6578\u64da\u5ef6\u8aa4\u53ef\u80fd\u4f7f\u65e5\u671f\u9806\u5ef6\uff09` : ""}\u3002\u6b77\u53f2\u7d50\u679c\u5c1a\u672a\u7372\u5be6\u6642\u8b49\u5be6\u3002`
                            )}
                        </small>
                      </div>
                    ) : null}
                    <div className="decision-guide-card">
                      <span>{labelByMode(languageMode, "Plain-language reason", "簡單原因")}</span>
                      <strong>{decisionReasonText(selectedLiveTrade.reason, languageMode)}</strong>
                      <small>
                        {labelByMode(
                          languageMode,
                          "Risk rules can block a trade even when the model looks positive.",
                          "即使模型看好，風險規則亦可能阻止交易。"
                        )}
                      </small>
                    </div>
                  </div>
                  <p className="simulation-warning">
                    {labelByMode(
                      languageMode,
                      "Learning reminder: this simulation can lose money. Model confidence is not a guarantee or financial advice.",
                      "學習提示：此模擬可能虧損。模型信心並非保證或投資建議。"
                    )}
                  </p>
                </div>
              );
            })()}
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
            <strong>{currencySymbol}{formatMoney(accountSummary?.total_account_value)}</strong>
          </div>
          <div className={totalProfitLoss >= 0 ? "pnl-positive" : "pnl-negative"}>
            <span>{labelByMode(languageMode, "Profit / Loss", ZH.profitLoss)}</span>
            <strong>{currencySymbol}{formatMoney(totalProfitLoss)}{formatPercent(totalProfitLossPct)}</strong>
          </div>
          <div>
            <span>{labelByMode(languageMode, "Cash", ZH.cash)}</span>
            <strong>{currencySymbol}{formatMoney(accountSummary?.cash)}</strong>
          </div>
          <div>
            <span>{labelByMode(languageMode, "Holdings value", ZH.holdingsValue)}</span>
            <strong>{currencySymbol}{formatMoney(accountSummary?.holdings_value)}</strong>
          </div>
          <div className={accountSummary?.buying_paused ? "risk-paused" : ""}>
            <span>{labelByMode(languageMode, "Portfolio protection", "投資組合保護")}</span>
            <strong>
              {accountSummary?.buying_paused
                ? labelByMode(languageMode, "New buys paused", "暫停新買入")
                : accountSummary?.portfolio_risk_level === "caution"
                ? labelByMode(languageMode, "Smaller positions", "縮小倉位")
                : labelByMode(languageMode, "Normal", "正常")}
            </strong>
            <small>
              {formatRiskValue(accountSummary?.performance_vs_contributions_pct, "% · ")}
              {labelByMode(languageMode, "vs cash added", "相對已投入現金")}
            </small>
          </div>
        </div>
      </section>

      <HoldingsTable
        languageMode={languageMode}
        holdings={holdingsWithNames}
        market={market}
        currencySymbol={currencySymbol}
      />

      <section className="panel">
        <h3>{labelByMode(languageMode, "Contributions", ZH.contributions)}</h3>
        <div className="beginner-summary-grid">
          <div>
            <span>{labelByMode(languageMode, "Total cash added", ZH.totalCashAdded)}</span>
            <strong>{currencySymbol}{formatMoney(accountSummary?.net_deposits)}</strong>
          </div>
          <div>
            <span>{labelByMode(languageMode, "Recent monthly", ZH.recentMonthly)}</span>
            <strong>{currencySymbol}{formatMoney(contributionSummary.monthly)}</strong>
          </div>
          <div>
            <span>{labelByMode(languageMode, "Recent one-time", ZH.recentOneTime)}</span>
            <strong>{currencySymbol}{formatMoney(contributionSummary.oneTime)}</strong>
          </div>
        </div>
      </section>

      {market === "US" ? (
        <MonthlyContributionInput userId={profileId} languageMode={languageMode} onUpdated={loadGlobalViews} />
      ) : (
        <section className="panel">
          <p className="helper-text">
            {labelByMode(
              languageMode,
              "Recurring deposits remain attached to the existing USD account. Use the HKD one-time deposit below for the separate HK account.",
              "每月入金繼續屬於現有 USD 帳戶。港股獨立帳戶請使用下方 HKD 單次入金。"
            )}
          </p>
        </section>
      )}

      <section className="panel">
        <h3>{labelByMode(languageMode, "One-Time Cash Add / Withdraw", ZH.oneTimeCash)}</h3>
        <div className="settings-form">
          <label>
            {labelByMode(languageMode, `Amount (${market === "HK" ? "HKD" : "USD"})`, ZH.amountUsd)}
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
        {market === "US" ? (
          <ResetTradingAccountButton
            userId={profileId}
            market={market}
            languageMode={languageMode}
            onResetComplete={loadGlobalViews}
          />
        ) : null}
      </section>

      <RecentTradesTable languageMode={languageMode} trades={recentTradesWithNames} currencySymbol={currencySymbol} />

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

          <section className="panel">
            <div className="beginner-guide-heading">
              <div>
                <h3>{labelByMode(languageMode, "Selected Ticker Models", "所選股票模型")}</h3>
                <p className="helper-text">
                  {labelByMode(
                    languageMode,
                    `${market} ${selectedTicker}: independently trained models and market-specific five-session feedback.`,
                    `${market} ${selectedTicker}：獨立訓練模型及市場獨立五個交易日反饋。`
                  )}
                </p>
              </div>
              <button type="button" className="secondary-button" onClick={loadSelectedTickerModels}>
                {labelByMode(languageMode, "Refresh models", "更新模型")}
              </button>
            </div>
            {modelRegistryError ? <p className="holding-modal-error">{modelRegistryError}</p> : null}
            <div className="table-wrap responsive-card-table">
              <table>
                <thead>
                  <tr>
                    <th>{labelByMode(languageMode, "Period", "期間")}</th>
                    <th>{labelByMode(languageMode, "Model", "模型")}</th>
                    <th>{labelByMode(languageMode, "Status", "狀態")}</th>
                    <th>{labelByMode(languageMode, "Validation", "驗證分數")}</th>
                    <th>{labelByMode(languageMode, "5-day samples", "5 日反饋樣本")}</th>
                    <th>{labelByMode(languageMode, "Reliability", "可靠度")}</th>
                    <th>{labelByMode(languageMode, "Feedback score", "回饋評分")}</th>
                    <th>{labelByMode(languageMode, "Last trained", "最近訓練")}</th>
                  </tr>
                </thead>
                <tbody>
                  {modelRegistry.length ? modelRegistry.map((row) => (
                    <tr key={`${row.market}-${row.ticker}-${row.period}-${row.model_name}`}>
                      <td data-label="Period">{row.period}</td>
                      <td data-label="Model">{row.model_name}</td>
                      <td data-label="Status">{row.status}{row.is_validated ? " · validated" : " · waiting"}</td>
                      <td data-label="Validation">
                        {Number.isFinite(Number(row.validation_score))
                          ? `${(Number(row.validation_score) * 100).toFixed(1)}%`
                          : "N/A"}
                      </td>
                      <td data-label="5-day samples">
                        {Number(row.metrics_summary?.live_feedback?.sample_count || 0)}
                      </td>
                      <td data-label="Reliability">
                        {formatModelRate(row.metrics_summary?.live_feedback?.reliability)}
                      </td>
                      <td data-label="Feedback score">
                        {formatModelRate(row.metrics_summary?.live_feedback?.feedback_score)}
                      </td>
                      <td data-label="Last trained">{compactDateTime(row.last_trained_at_utc)}</td>
                    </tr>
                  )) : (
                    <tr>
                      <td colSpan={8}>
                        {labelByMode(
                          languageMode,
                          market === "HK"
                            ? "No registered HK model yet. Active tickers are queued automatically; refresh after background training and validation."
                            : "No registered model is available for this ticker yet.",
                          "尚未有已登記模型。港股可先執行「更新決定」，等待背景訓練後再更新此表。"
                        )}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

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
            <NewsSentimentPanel
              ticker={market === "HK" ? `${selectedTicker}.HK` : selectedTicker}
              languageMode={languageMode}
            />
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
              events={accountHistoryWithNames}
              isLoading={historyLoading}
              hasMore={historyHasMore}
              onLoadMore={() => loadAccountHistoryPage({ reset: false })}
              errorMessage={historyError}
              currencySymbol={currencySymbol}
            />
          )}

          {market === "HK" ? (
            <section className="panel">
              <h3>{labelByMode(languageMode, "HK Model History", "港股模型紀錄")}</h3>
              <p className="helper-text">
                {labelByMode(
                  languageMode,
                  "Live HK decisions and their five-session feedback appear in the tables above. Open Trading Models for the market-specific registry and validation status.",
                  "港股即時決定及其五個交易日反饋顯示於上方表格；市場獨立模型登記及驗證狀態可於交易模型頁查看。"
                )}
              </p>
              <a href="/model-lifecycle">{labelByMode(languageMode, "Open Trading Models", "開啟交易模型")}</a>
            </section>
          ) : !historicalEnabled ? (
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
              {historicalSummary?.summary ? (
                <section className="panel">
                  <h3>{labelByMode(languageMode, "Historical Risk Check", "歷史風險檢查")}</h3>
                  <p className="helper-text">
                    {labelByMode(
                      languageMode,
                      "These figures remove cash deposits from daily returns so adding money is not mistaken for investment profit.",
                      "這些數字會從每日回報中扣除新增資金，避免把入金誤當作投資盈利。"
                    )}
                  </p>
                  <div className="risk-metric-grid">
                    <div>
                      <span>{labelByMode(languageMode, "Worst historical fall", "歷史最大跌幅")}</span>
                      <strong>{formatRiskValue(historicalSummary.summary.max_drawdown_pct, "%")}</strong>
                      <small>{labelByMode(languageMode, "Largest peak-to-trough account decline. Closer to 0% is safer.", "帳戶由高位至低位的最大跌幅；越接近 0% 代表風險越低。")}</small>
                    </div>
                    <div>
                      <span>{labelByMode(languageMode, "Annualized volatility", "年化波動")}</span>
                      <strong>{formatRiskValue(historicalSummary.summary.annualized_volatility_pct, "%")}</strong>
                      <small>{labelByMode(languageMode, "How widely daily results moved. Lower usually means a steadier journey.", "每日結果的波動幅度；較低通常代表走勢較穩定。")}</small>
                    </div>
                    <div>
                      <span>{labelByMode(languageMode, "Return efficiency", "回報效率")}</span>
                      <strong>{formatRiskValue(historicalSummary.summary.sharpe_ratio)}</strong>
                      <small>{labelByMode(languageMode, "Return relative to volatility. Higher is better; negative means poor risk-adjusted results.", "相對波動的回報；越高越好，負數代表風險調整後表現欠佳。")}</small>
                    </div>
                    <div>
                      <span>{labelByMode(languageMode, "Versus benchmark", "與基準比較")}</span>
                      <strong>{formatRiskValue(historicalSummary.summary.outperformance_vs_benchmark_pct_points, " pts")}</strong>
                      <small>{labelByMode(languageMode, "Positive means the simulation beat its benchmark; negative means it lagged.", "正數代表模擬跑贏基準，負數代表落後。")}</small>
                    </div>
                  </div>
                </section>
              ) : null}
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

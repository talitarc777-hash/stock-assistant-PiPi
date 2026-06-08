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
      const [liveStatusResult, schedulerStatusResult, recentTradesResult] = await Promise.allSettled([
        fetchLiveVirtualTraderStatus(profileId, null, AUTO_TRADING_MODEL, false),
        fetchTraderSchedulerStatus(24),
        fetchVirtualAccountRecentTrades(profileId, 20),
      ]);

      if (schedulerStatusResult.status === "fulfilled") setSchedulerStatus(schedulerStatusResult.value);
      if (liveStatusResult.status === "fulfilled") applyLiveStatusPayload(liveStatusResult.value);
      if (recentTradesResult.status === "fulfilled") setRecentTrades(recentTradesResult.value.trades || []);

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

  const actionRows = liveDecisionLog.slice(0, 6);

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
          {labelByMode(languageMode, "This is simulation only. No broker orders are sent.", ZH.simulationOnly)}
        </p>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            "Trading rule: minimum quantity is 1. Each buy or sell costs HKD 50, and the cost is included in profit/loss.",
            "\u4ea4\u6613\u898f\u5247\uff1a\u6700\u4f4e\u6578\u91cf\u70ba 1\u3002\u6bcf\u6b21\u8cb7\u5165\u6216\u8ce3\u51fa\u6536\u53d6 50 \u6e2f\u5143\uff0c\u4e26\u5df2\u8a08\u5165\u76c8\u8667\u3002"
          )}
        </p>
        <div className="table-wrap beginner-action-table">
          <table>
            <thead>
              <tr>
                <th>{labelByMode(languageMode, "Ticker", ZH.ticker)}</th>
                <th>{labelByMode(languageMode, "Action", ZH.action)}</th>
                <th>{labelByMode(languageMode, "Reason", ZH.reason)}</th>
                <th>{labelByMode(languageMode, "Price", ZH.price)}</th>
              </tr>
            </thead>
            <tbody>
              {actionRows.length ? (
                actionRows.map((item) => (
                  <tr
                    key={`${item.timestamp}-${item.ticker}-${item.action}`}
                    className={selectedLiveTrade?.timestamp === item.timestamp ? "selected-row" : ""}
                    onClick={() => setSelectedLiveTrade(item)}
                  >
                    <td>{item.ticker}</td>
                    <td>{actionText(item.action, languageMode)}</td>
                    <td>{item.action_summary || item.reason}</td>
                    <td>{formatMoney(item.price)}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={4}>{getLabel(languageMode, "noRecentTraderDecisions")}</td>
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

      <section className="panel">
        <h3>{labelByMode(languageMode, "Advanced Details", "進階資料")}</h3>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            "Open this for scheduler status, charts, news sentiment, trade history, and replay data.",
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

          <RecentTradesTable languageMode={languageMode} trades={recentTrades} />

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

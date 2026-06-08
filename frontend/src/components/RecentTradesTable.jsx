import React, { useMemo, useState } from "react";

function labelByMode(mode, en, zh) {
  if (mode === "zh") return zh;
  if (mode === "en") return en;
  return `${en} / ${zh}`;
}

function formatMoney(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "N/A";
  return numeric.toFixed(2);
}

function formatEventType(eventType, languageMode) {
  const map = {
    buy_trade: ["Buy", "買入"],
    sell_trade: ["Sell", "賣出"],
  };
  const [en, zh] = map[eventType] || [eventType || "unknown", eventType || "unknown"];
  return labelByMode(languageMode, en, zh);
}

function parseTradeTime(value) {
  const timestamp = new Date(value).getTime();
  return Number.isFinite(timestamp) ? timestamp : 0;
}

export default function RecentTradesTable({ languageMode, trades = [] }) {
  const [tickerFilter, setTickerFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [timeFilter, setTimeFilter] = useState("all");
  const [sortOrder, setSortOrder] = useState("newest");

  const tickerOptions = useMemo(
    () => [...new Set(trades.map((trade) => String(trade.ticker || "").trim().toUpperCase()).filter(Boolean))].sort(),
    [trades]
  );

  const filteredTrades = useMemo(() => {
    const durationByFilter = {
      "24h": 24 * 60 * 60 * 1000,
      "7d": 7 * 24 * 60 * 60 * 1000,
      "30d": 30 * 24 * 60 * 60 * 1000,
    };
    const duration = durationByFilter[timeFilter];
    const minimumTime = duration ? Date.now() - duration : null;

    return trades
      .filter((trade) => tickerFilter === "all" || String(trade.ticker || "").toUpperCase() === tickerFilter)
      .filter((trade) => typeFilter === "all" || trade.event_type === typeFilter)
      .filter((trade) => minimumTime === null || parseTradeTime(trade.created_at) >= minimumTime)
      .sort((left, right) => {
        const difference = parseTradeTime(right.created_at) - parseTradeTime(left.created_at);
        return sortOrder === "newest" ? difference : -difference;
      });
  }, [sortOrder, tickerFilter, timeFilter, trades, typeFilter]);

  function resetFilters() {
    setTickerFilter("all");
    setTypeFilter("all");
    setTimeFilter("all");
    setSortOrder("newest");
  }

  return (
    <section className="panel">
      <h3>{labelByMode(languageMode, "Recent Trades", "最近交易")}</h3>

      <div className="trade-filter-grid">
        <label>
          {labelByMode(languageMode, "Ticker", "股票代號")}
          <select value={tickerFilter} onChange={(event) => setTickerFilter(event.target.value)}>
            <option value="all">{labelByMode(languageMode, "All tickers", "所有股票")}</option>
            {tickerOptions.map((ticker) => (
              <option key={ticker} value={ticker}>{ticker}</option>
            ))}
          </select>
        </label>

        <label>
          {labelByMode(languageMode, "Trade type", "交易類型")}
          <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            <option value="all">{labelByMode(languageMode, "Buy and sell", "買入及賣出")}</option>
            <option value="buy_trade">{labelByMode(languageMode, "Buy only", "只顯示買入")}</option>
            <option value="sell_trade">{labelByMode(languageMode, "Sell only", "只顯示賣出")}</option>
          </select>
        </label>

        <label>
          {labelByMode(languageMode, "Time", "時間")}
          <select value={timeFilter} onChange={(event) => setTimeFilter(event.target.value)}>
            <option value="all">{labelByMode(languageMode, "All loaded trades", "所有已載入交易")}</option>
            <option value="24h">{labelByMode(languageMode, "Last 24 hours", "最近 24 小時")}</option>
            <option value="7d">{labelByMode(languageMode, "Last 7 days", "最近 7 日")}</option>
            <option value="30d">{labelByMode(languageMode, "Last 30 days", "最近 30 日")}</option>
          </select>
        </label>

        <label>
          {labelByMode(languageMode, "Order", "排序")}
          <select value={sortOrder} onChange={(event) => setSortOrder(event.target.value)}>
            <option value="newest">{labelByMode(languageMode, "Newest first", "最新優先")}</option>
            <option value="oldest">{labelByMode(languageMode, "Oldest first", "最舊優先")}</option>
          </select>
        </label>
      </div>

      <div className="trade-filter-summary">
        <span>
          {labelByMode(languageMode, "Showing", "顯示")} {filteredTrades.length} / {trades.length}
        </span>
        <button type="button" onClick={resetFilters}>
          {labelByMode(languageMode, "Clear filters", "清除篩選")}
        </button>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{labelByMode(languageMode, "Date/Time", "日期/時間")}</th>
              <th>{labelByMode(languageMode, "Type", "類型")}</th>
              <th>{labelByMode(languageMode, "Ticker", "股票代號")}</th>
              <th>{labelByMode(languageMode, "Quantity", "數量")}</th>
              <th>{labelByMode(languageMode, "Price", "價格")}</th>
              <th>{labelByMode(languageMode, "Gross Value", "交易總值")}</th>
              <th>{labelByMode(languageMode, "Admin Cost", "行政費")}</th>
              <th>{labelByMode(languageMode, "Balance After", "交易後現金")}</th>
              <th>{labelByMode(languageMode, "Reason", "原因")}</th>
            </tr>
          </thead>
          <tbody>
            {filteredTrades.length ? (
              filteredTrades.map((trade) => (
                <tr key={trade.id}>
                  <td>{trade.created_at}</td>
                  <td>{formatEventType(trade.event_type, languageMode)}</td>
                  <td>{trade.ticker}</td>
                  <td>{Number(trade.quantity || 0).toFixed(4)}</td>
                  <td>{formatMoney(trade.price)}</td>
                  <td>{formatMoney(trade.gross_amount)}</td>
                  <td>{formatMoney(trade.fee_amount)}</td>
                  <td>{formatMoney(trade.cash_balance_after)}</td>
                  <td>{trade.reason || "-"}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={9}>
                  {trades.length
                    ? labelByMode(languageMode, "No trades match these filters.", "沒有交易符合目前篩選條件。")
                    : labelByMode(languageMode, "No executed trades yet.", "目前尚未有已執行交易。")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

import React, { useMemo, useState } from "react";
import {
  PRIMARY_TICKER_CLASS_CONFIG,
  STOCK_SUBCLASS_CONFIG,
  classificationLabel,
  matchesTickerClassificationFilters,
  resolveTickerClassification,
} from "../config/tickerClassification";
import TickerIdentity from "./TickerIdentity";
import { modelUsedText } from "../utils/tradeModelProvenance";
import { tickerDisplayName } from "../utils/tickerIdentity";

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

function formatQuantity(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "0";
  return numeric.toFixed(0);
}

function formatEventType(eventType, languageMode) {
  const map = {
    buy_trade: ["Buy", "\u8cb7\u5165"],
    sell_trade: ["Sell", "\u8ce3\u51fa"],
  };
  const [en, zh] = map[eventType] || [eventType || "unknown", eventType || "unknown"];
  return labelByMode(languageMode, en, zh);
}

function parseTradeTime(value) {
  const timestamp = new Date(value).getTime();
  return Number.isFinite(timestamp) ? timestamp : 0;
}

export default function RecentTradesTable({ languageMode, trades = [], currencySymbol = "$" }) {
  const [tickerFilter, setTickerFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [timeFilter, setTimeFilter] = useState("all");
  const [sortOrder, setSortOrder] = useState("newest");
  const [primaryClassFilter, setPrimaryClassFilter] = useState("all");
  const [stockSubclassFilter, setStockSubclassFilter] = useState("all");

  const tickerOptions = useMemo(
    () =>
      [...new Set(trades.map((trade) => String(trade.ticker || "").trim().toUpperCase()).filter(Boolean))].sort(),
    [trades]
  );

  const primaryClassOptions = useMemo(() => {
    const values = new Set(trades.map((trade) => resolveTickerClassification(trade).primaryClass));
    return Object.entries(PRIMARY_TICKER_CLASS_CONFIG)
      .filter(([value]) => values.has(value))
      .sort(([, left], [, right]) => left.order - right.order);
  }, [trades]);

  const stockSubclassOptions = useMemo(() => {
    const values = new Set(
      trades.map((trade) => resolveTickerClassification(trade).stockSubclass).filter(Boolean)
    );
    return Object.entries(STOCK_SUBCLASS_CONFIG)
      .filter(([value]) => values.has(value))
      .sort(([, left], [, right]) => left.order - right.order);
  }, [trades]);

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
      .filter((trade) => matchesTickerClassificationFilters(
        trade,
        primaryClassFilter,
        stockSubclassFilter
      ))
      .filter((trade) => minimumTime === null || parseTradeTime(trade.created_at) >= minimumTime)
      .sort((left, right) => {
        const difference = parseTradeTime(right.created_at) - parseTradeTime(left.created_at);
        return sortOrder === "newest" ? difference : -difference;
      });
  }, [primaryClassFilter, sortOrder, stockSubclassFilter, tickerFilter, timeFilter, trades, typeFilter]);

  function resetFilters() {
    setTickerFilter("all");
    setTypeFilter("all");
    setTimeFilter("all");
    setSortOrder("newest");
    setPrimaryClassFilter("all");
    setStockSubclassFilter("all");
  }

  return (
    <section className="panel">
      <h3>{labelByMode(languageMode, "Recent Trades", "\u6700\u8fd1\u4ea4\u6613")}</h3>

      <div className="trade-filter-grid">
        <label>
          {labelByMode(languageMode, "Ticker", "\u80a1\u7968\u4ee3\u865f")}
          <select value={tickerFilter} onChange={(event) => setTickerFilter(event.target.value)}>
            <option value="all">{labelByMode(languageMode, "All tickers", "\u5168\u90e8\u80a1\u7968")}</option>
            {tickerOptions.map((ticker) => (
              <option key={ticker} value={ticker}>
                {ticker}{(() => {
                  const row = trades.find((trade) => String(trade.ticker || "").trim().toUpperCase() === ticker);
                  const name = tickerDisplayName(row, ticker);
                  return name ? ` — ${name}` : "";
                })()}
              </option>
            ))}
          </select>
        </label>

        <label>
          {labelByMode(languageMode, "Trade type", "\u4ea4\u6613\u985e\u578b")}
          <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            <option value="all">{labelByMode(languageMode, "Buy and sell", "\u8cb7\u5165\u548c\u8ce3\u51fa")}</option>
            <option value="buy_trade">{labelByMode(languageMode, "Buy only", "\u53ea\u770b\u8cb7\u5165")}</option>
            <option value="sell_trade">{labelByMode(languageMode, "Sell only", "\u53ea\u770b\u8ce3\u51fa")}</option>
          </select>
        </label>

        <label>
          {labelByMode(languageMode, "Asset class", "資產類別")}
          <select
            value={primaryClassFilter}
            onChange={(event) => {
              const nextValue = event.target.value;
              setPrimaryClassFilter(nextValue);
              if (nextValue !== "all" && nextValue !== "stock") setStockSubclassFilter("all");
            }}
          >
            <option value="all">{labelByMode(languageMode, "All classes", "全部類別")}</option>
            {primaryClassOptions.map(([value, config]) => (
              <option key={value} value={value}>{classificationLabel(config, languageMode)}</option>
            ))}
          </select>
        </label>

        <label>
          {labelByMode(languageMode, "Stock sector", "股票行業")}
          <select
            value={stockSubclassFilter}
            onChange={(event) => setStockSubclassFilter(event.target.value)}
            disabled={primaryClassFilter !== "all" && primaryClassFilter !== "stock"}
          >
            <option value="all">{labelByMode(languageMode, "All stock sectors", "全部股票行業")}</option>
            {stockSubclassOptions.map(([value, config]) => (
              <option key={value} value={value}>{classificationLabel(config, languageMode)}</option>
            ))}
          </select>
        </label>

        <label>
          {labelByMode(languageMode, "Time", "\u6642\u9593")}
          <select value={timeFilter} onChange={(event) => setTimeFilter(event.target.value)}>
            <option value="all">{labelByMode(languageMode, "All loaded trades", "\u5168\u90e8\u5df2\u8f09\u5165\u4ea4\u6613")}</option>
            <option value="24h">{labelByMode(languageMode, "Last 24 hours", "\u6700\u8fd1 24 \u5c0f\u6642")}</option>
            <option value="7d">{labelByMode(languageMode, "Last 7 days", "\u6700\u8fd1 7 \u5929")}</option>
            <option value="30d">{labelByMode(languageMode, "Last 30 days", "\u6700\u8fd1 30 \u5929")}</option>
          </select>
        </label>

        <label>
          {labelByMode(languageMode, "Order", "\u6392\u5e8f")}
          <select value={sortOrder} onChange={(event) => setSortOrder(event.target.value)}>
            <option value="newest">{labelByMode(languageMode, "Newest first", "\u6700\u65b0\u512a\u5148")}</option>
            <option value="oldest">{labelByMode(languageMode, "Oldest first", "\u6700\u820a\u512a\u5148")}</option>
          </select>
        </label>
      </div>

      <div className="trade-filter-summary">
        <span>
          {labelByMode(languageMode, "Showing", "\u986f\u793a")} {filteredTrades.length} / {trades.length}
        </span>
        <button type="button" onClick={resetFilters}>
          {labelByMode(languageMode, "Clear filters", "\u6e05\u9664\u7be9\u9078")}
        </button>
      </div>

      <div className="table-wrap recent-trades-table responsive-card-table">
        <table>
          <thead>
            <tr>
              <th>{labelByMode(languageMode, "Date/Time", "\u65e5\u671f/\u6642\u9593")}</th>
              <th>{labelByMode(languageMode, "Type", "\u985e\u578b")}</th>
              <th>{labelByMode(languageMode, "Ticker", "\u80a1\u7968\u4ee3\u865f")}</th>
              <th>{labelByMode(languageMode, "Model used", "\u4f7f\u7528\u6a21\u578b")}</th>
              <th>{labelByMode(languageMode, "Quantity", "\u6578\u91cf")}</th>
              <th>{labelByMode(languageMode, "Remaining Qty", "\u5269\u9918\u6578\u91cf")}</th>
              <th>{labelByMode(languageMode, "Price", "\u50f9\u683c")}</th>
              <th>{labelByMode(languageMode, "Reason", "\u539f\u56e0")}</th>
            </tr>
          </thead>
          <tbody>
            {filteredTrades.length ? (
              filteredTrades.map((trade) => (
                <tr key={trade.id}>
                  <td data-label={labelByMode(languageMode, "Date/Time", "\u65e5\u671f/\u6642\u9593")}>{trade.created_at}</td>
                  <td data-label={labelByMode(languageMode, "Type", "\u985e\u578b")}>{formatEventType(trade.event_type, languageMode)}</td>
                  <td data-label={labelByMode(languageMode, "Ticker", "\u80a1\u7968\u4ee3\u865f")}>
                    <TickerIdentity ticker={trade.ticker} data={trade} languageMode={languageMode} />
                  </td>
                  <td data-label={labelByMode(languageMode, "Model used", "\u4f7f\u7528\u6a21\u578b")}>
                    {modelUsedText(trade, languageMode)}
                  </td>
                  <td data-label={labelByMode(languageMode, "Quantity", "\u6578\u91cf")}>{formatQuantity(trade.quantity)}</td>
                  <td data-label={labelByMode(languageMode, "Remaining Qty", "\u5269\u9918\u6578\u91cf")}>{formatQuantity(trade.remaining_quantity)}</td>
                  <td data-label={labelByMode(languageMode, "Price", "\u50f9\u683c")}>{currencySymbol}{formatMoney(trade.price)}</td>
                  <td data-label={labelByMode(languageMode, "Reason", "\u539f\u56e0")}>{trade.reason || "-"}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={8}>
                  {trades.length
                    ? labelByMode(languageMode, "No trades match these filters.", "\u6c92\u6709\u4ea4\u6613\u7b26\u5408\u9019\u4e9b\u7be9\u9078\u3002")
                    : labelByMode(languageMode, "No executed trades yet.", "\u5c1a\u672a\u6709\u5df2\u57f7\u884c\u7684\u4ea4\u6613\u3002")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

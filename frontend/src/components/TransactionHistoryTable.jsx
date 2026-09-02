import React, { useMemo, useState } from "react";
import { getLabel } from "../constants/i18n";
import TickerIdentity from "./TickerIdentity";

function formatMoney(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "N/A";
  return numeric.toFixed(2);
}

function formatEventType(eventType, languageMode) {
  const keyByEventType = {
    monthly_contribution: "eventMonthlyContribution",
    manual_deposit: "eventManualDeposit",
    withdrawal: "eventWithdrawal",
    buy_trade: "eventBuyTrade",
    sell_trade: "eventSellTrade",
    fee: "eventFee",
  };
  const labelKey = keyByEventType[eventType];
  if (!labelKey) return eventType || "unknown";
  return getLabel(languageMode, labelKey);
}

export default function TransactionHistoryTable({
  languageMode,
  events = [],
  isLoading = false,
  hasMore = false,
  onLoadMore = null,
  errorMessage = "",
  currencySymbol = "$",
}) {
  const [filter, setFilter] = useState("all");

  const filteredEvents = useMemo(() => {
    if (filter === "all") return events;
    if (filter === "cash") {
      return events.filter((item) =>
        ["monthly_contribution", "manual_deposit", "withdrawal", "fee"].includes(item.event_type)
      );
    }
    return events.filter((item) => item.event_type === filter);
  }, [events, filter]);

  return (
    <section className="panel">
      <h3>{getLabel(languageMode, "historyTitle")}</h3>
      <p className="helper-text">{getLabel(languageMode, "historyImmutableHint")}</p>
      <div className="settings-actions">
        <button type="button" onClick={() => setFilter("all")}>
          {getLabel(languageMode, "filterAll")}
        </button>
        <button type="button" onClick={() => setFilter("cash")}>
          {getLabel(languageMode, "filterCashEvents")}
        </button>
        <button type="button" onClick={() => setFilter("buy_trade")}>
          {getLabel(languageMode, "filterBuys")}
        </button>
        <button type="button" onClick={() => setFilter("sell_trade")}>
          {getLabel(languageMode, "filterSells")}
        </button>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{getLabel(languageMode, "dateTime")}</th>
              <th>{getLabel(languageMode, "eventType")}</th>
              <th>{getLabel(languageMode, "tickerLabel")}</th>
              <th>{getLabel(languageMode, "quantityLabel")}</th>
              <th>{getLabel(languageMode, "priceLabel")}</th>
              <th>{getLabel(languageMode, "cashChange")}</th>
              <th>{getLabel(languageMode, "balanceAfter")}</th>
              <th>{getLabel(languageMode, "noteReason")}</th>
            </tr>
          </thead>
          <tbody>
            {filteredEvents.length ? (
              filteredEvents.map((event) => (
                <tr key={event.id}>
                  <td>{event.created_at}</td>
                  <td>{formatEventType(event.event_type, languageMode)}</td>
                  <td>
                    {event.ticker ? (
                      <TickerIdentity ticker={event.ticker} data={event} languageMode={languageMode} />
                    ) : "-"}
                  </td>
                  <td>
                    {event.quantity !== null && event.quantity !== undefined
                      ? Number(event.quantity).toFixed(0)
                      : "-"}
                  </td>
                  <td>
                    {event.price !== null && event.price !== undefined
                      ? `${currencySymbol}${formatMoney(event.price)}`
                      : "-"}
                  </td>
                  <td>{currencySymbol}{formatMoney(event.cash_change)}</td>
                  <td>{currencySymbol}{formatMoney(event.cash_balance_after)}</td>
                  <td>{event.reason || "-"}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={8}>{getLabel(languageMode, "noHistoryRecords")}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {errorMessage ? (
        <div className="helper-text">
          <p>{errorMessage}</p>
          {onLoadMore ? (
            <button type="button" onClick={onLoadMore} disabled={isLoading || !hasMore}>
              {getLabel(languageMode, "loadMoreHistory")}
            </button>
          ) : null}
        </div>
      ) : null}
      {onLoadMore ? (
        <div className="settings-actions">
          <button type="button" onClick={onLoadMore} disabled={isLoading || !hasMore}>
            {isLoading
              ? getLabel(languageMode, "loading")
              : hasMore
                ? getLabel(languageMode, "loadMoreHistory")
                : getLabel(languageMode, "noMoreHistory")}
          </button>
        </div>
      ) : null}
    </section>
  );
}

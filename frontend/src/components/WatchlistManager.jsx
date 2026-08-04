import React, { useState } from "react";
import TickerClassificationTags from "./TickerClassificationTags";

import {
  addUserWatchlistTicker,
  removeUserWatchlistTicker,
} from "../services/userProfileApi";

function parseTickerInput(rawValue) {
  return rawValue
    .replace(/\n/g, ",")
    .replace(/;/g, ",")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
}

const ZH = {
  enterTicker: "\u8acb\u5148\u8f38\u5165\u80a1\u7968\u4ee3\u865f\u3002",
  updated: "\u89c0\u5bdf\u540d\u55ae\u5df2\u66f4\u65b0\u3002",
  removedPrefix: "\u5df2\u79fb\u9664",
  manager: "\u89c0\u5bdf\u540d\u55ae\u7ba1\u7406",
  shared: "\u9019\u4efd\u89c0\u5bdf\u540d\u55ae\u6703\u8207 Discord \u5171\u7528\u3002",
  example: "\u4f8b\u5982 TSLA, BRK-B",
  add: "\u52a0\u5165",
};

export default function WatchlistManager({
  userId,
  watchlist,
  languageMode,
  onUpdated,
  classificationByTicker = {},
}) {
  const [tickerInput, setTickerInput] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [isSaving, setIsSaving] = useState(false);

  async function handleAddTicker(event) {
    event.preventDefault();
    const tickers = parseTickerInput(tickerInput);
    if (tickers.length === 0) {
      setError(
        languageMode === "zh"
          ? ZH.enterTicker
          : languageMode === "en"
          ? "Please enter a ticker."
          : `Please enter a ticker. / ${ZH.enterTicker}`
      );
      return;
    }

    setIsSaving(true);
    setError("");
    setMessage("");
    try {
      for (const ticker of tickers) {
        await addUserWatchlistTicker({
          user_id: userId,
          ticker,
          last_active_source: "dashboard",
        });
      }
      setTickerInput("");
      setMessage(
        languageMode === "zh"
          ? ZH.updated
          : languageMode === "en"
          ? "Watchlist updated."
          : `Watchlist updated. / ${ZH.updated}`
      );
      await onUpdated();
    } catch (requestError) {
      setError(requestError.message || "Failed to update watchlist.");
    } finally {
      setIsSaving(false);
    }
  }

  async function handleRemoveTicker(ticker) {
    setIsSaving(true);
    setError("");
    setMessage("");
    try {
      await removeUserWatchlistTicker({
        user_id: userId,
        ticker,
        last_active_source: "dashboard",
      });
      const zhMessage = `${ZH.removedPrefix} ${ticker}\u3002`;
      setMessage(
        languageMode === "zh"
          ? zhMessage
          : languageMode === "en"
          ? `Removed ${ticker}.`
          : `Removed ${ticker}. / ${zhMessage}`
      );
      await onUpdated();
    } catch (requestError) {
      setError(requestError.message || "Failed to update watchlist.");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <section className="panel">
      <h3>
        {languageMode === "zh"
          ? ZH.manager
          : languageMode === "en"
          ? "Watchlist Manager"
          : `Watchlist Manager / ${ZH.manager}`}
      </h3>
      <p className="helper-text">
        {languageMode === "zh"
          ? ZH.shared
          : languageMode === "en"
          ? "This watchlist is shared with Discord."
          : `This watchlist is shared with Discord. / ${ZH.shared}`}
      </p>
      <form className="watchlist-form" onSubmit={handleAddTicker}>
        <input
          type="text"
          value={tickerInput}
          onChange={(event) => setTickerInput(event.target.value)}
          placeholder={
            languageMode === "zh"
              ? ZH.example
              : languageMode === "en"
              ? "For example TSLA, BRK-B"
              : `For example TSLA, BRK-B / ${ZH.example}`
          }
        />
        <button type="submit" disabled={isSaving}>
          {languageMode === "zh"
            ? ZH.add
            : languageMode === "en"
            ? "Add"
            : `Add / ${ZH.add}`}
        </button>
      </form>
      {message ? <p className="success-box">{message}</p> : null}
      {error ? <p className="error-box">{error}</p> : null}
      <div className="watchlist-chip-group">
        {watchlist.map((ticker) => (
          <button
            key={ticker}
            type="button"
            className="watchlist-chip"
            onClick={() => handleRemoveTicker(ticker)}
            disabled={isSaving}
            aria-label={`Remove ${ticker} from watchlist`}
          >
            <span className="ticker-identity">
              <span className="ticker-symbol">{ticker}</span>
              <TickerClassificationTags
                ticker={ticker}
                classification={classificationByTicker[ticker]}
                languageMode={languageMode}
                size="xs"
              />
              <span aria-hidden="true">&times;</span>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

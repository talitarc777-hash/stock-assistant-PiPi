import React, { useMemo, useState } from "react";
import TickerIdentity from "./TickerIdentity";
import { resolveTickerClassification } from "../config/tickerClassification.js";

function labelByMode(mode, en, zh) {
  if (mode === "zh") return zh;
  if (mode === "en") return en;
  return `${en} / ${zh}`;
}

function scoreMeaning(score, languageMode) {
  if (score >= 80) return labelByMode(languageMode, "Strong candidate", "強勁觀察候選");
  if (score >= 65) return labelByMode(languageMode, "Watch closely", "密切觀察");
  if (score >= 50) return labelByMode(languageMode, "Neutral", "中性");
  return labelByMode(languageMode, "Avoid for now", "暫時避開");
}

function formatPrice(value) {
  return Number.isFinite(value) ? value.toFixed(2) : "N/A";
}

export default function TopScoredTickersTable({
  rowsByMarket = { US: [], HK: [] },
  languageMode,
  isLoading,
  errorsByMarket = { US: "", HK: "" },
  onSelectTicker,
}) {
  const [activeMarket, setActiveMarket] = useState("US");
  const [activeClass, setActiveClass] = useState("stock");
  const rankLabel = labelByMode(languageMode, "Rank", "排名");
  const tickerLabel = labelByMode(languageMode, "Ticker", "股票代號");
  const scoreLabel = labelByMode(languageMode, "Score", "評分");
  const meaningLabel = labelByMode(languageMode, "Meaning", "評分含義");
  const priceLabel = labelByMode(languageMode, "Latest price", "最新價格");
  const marketRows = rowsByMarket[activeMarket] || [];
  const error = errorsByMarket[activeMarket] || "";
  const classRows = useMemo(
    () => marketRows
      .filter((item) => resolveTickerClassification(item).primaryClass === activeClass)
      .slice(0, 10),
    [activeClass, marketRows]
  );
  const activeClassLabel = activeClass === "etf"
    ? labelByMode(languageMode, "ETF", "ETF")
    : labelByMode(languageMode, "Stock", "股票");

  return (
    <section className="panel top-score-panel">
      <h3>{labelByMode(languageMode, "Top 10 Tickers by Score", "各類別評分最高的 10 隻股票")}</h3>
      <p className="helper-text">
        {labelByMode(
          languageMode,
          "Choose a market and asset type to see its 10 highest-scoring tickers. A high score describes the technical setup; it does not guarantee profit.",
          "選擇市場及資產類別，以查看該組別評分最高的 10 隻股票。高評分代表技術形態較強，並不保證獲利。"
        )}
      </p>
      <div className="top-score-filter-groups">
        <div className="top-score-filter-group">
          <span className="top-score-filter-label">{labelByMode(languageMode, "Market", "市場")}</span>
          <div className="top-score-tabs" role="tablist" aria-label={labelByMode(languageMode, "Market", "市場")}>
            {[
              ["US", labelByMode(languageMode, "US", "美股")],
              ["HK", labelByMode(languageMode, "HK", "港股")],
            ].map(([marketKey, label]) => (
              <button
                key={marketKey}
                type="button"
                role="tab"
                aria-selected={activeMarket === marketKey}
                className={activeMarket === marketKey ? "active" : ""}
                onClick={() => setActiveMarket(marketKey)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="top-score-filter-group">
          <span className="top-score-filter-label">{labelByMode(languageMode, "Asset type", "資產類別")}</span>
          <div className="top-score-tabs" role="tablist" aria-label={labelByMode(languageMode, "Ticker class", "股票類別")}>
            {[
              ["stock", labelByMode(languageMode, "Stock", "股票")],
              ["etf", labelByMode(languageMode, "ETF", "ETF")],
            ].map(([classKey, label]) => (
              <button
                key={classKey}
                type="button"
                role="tab"
                aria-selected={activeClass === classKey}
                className={activeClass === classKey ? "active" : ""}
                onClick={() => setActiveClass(classKey)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>
      {error ? <p className="helper-text warning-text">{error}</p> : null}
      {isLoading && !classRows.length ? (
        <p>{labelByMode(languageMode, "Loading top scores...", "正在載入最高評分股票...")}</p>
      ) : null}
      {!isLoading && !marketRows.length ? (
        <p>
          {labelByMode(
            languageMode,
            "No ticker scores are available yet. Refresh the dashboard or run a Virtual Trader market scan.",
            "目前尚未有股票評分。請重新整理儀表板，或執行虛擬交易員市場掃描。"
          )}
        </p>
      ) : null}
      {!isLoading && marketRows.length > 0 && !classRows.length ? (
        <p>
          {labelByMode(
            languageMode,
            `No scored ${activeClassLabel} tickers are available yet.`,
            `目前沒有可用的${activeClassLabel}評分。`
          )}
        </p>
      ) : null}
      {classRows.length ? (
        <div className="table-wrap responsive-card-table top-score-table">
          <table>
            <thead>
              <tr>
                <th>{rankLabel}</th>
                <th>{tickerLabel}</th>
                <th>{scoreLabel}</th>
                <th>{meaningLabel}</th>
                <th>{priceLabel}</th>
              </tr>
            </thead>
            <tbody>
              {classRows.map((item, index) => (
                <tr key={item.ticker}>
                  <td data-label={rankLabel}>{index + 1}</td>
                  <td data-label={tickerLabel}>
                    <button
                      type="button"
                      className="ticker-dashboard-link"
                      onClick={() => onSelectTicker?.(item.ticker, activeMarket)}
                    >
                      <TickerIdentity ticker={item.ticker} data={item} languageMode={languageMode} />
                    </button>
                  </td>
                  <td data-label={scoreLabel}><strong>{item.score.toFixed(0)}/100</strong></td>
                  <td data-label={meaningLabel}>{scoreMeaning(item.score, languageMode)}</td>
                  <td data-label={priceLabel}>{formatPrice(item.latestPrice)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

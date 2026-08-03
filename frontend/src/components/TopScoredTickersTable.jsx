import React from "react";

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

export default function TopScoredTickersTable({ rows, languageMode, isLoading, error }) {
  const rankLabel = labelByMode(languageMode, "Rank", "排名");
  const tickerLabel = labelByMode(languageMode, "Ticker", "股票代號");
  const scoreLabel = labelByMode(languageMode, "Score", "評分");
  const meaningLabel = labelByMode(languageMode, "Meaning", "評分含義");
  const priceLabel = labelByMode(languageMode, "Latest price", "最新價格");

  return (
    <section className="panel top-score-panel">
      <h3>{labelByMode(languageMode, "Top 10 Tickers by Score", "評分最高的 10 隻股票")}</h3>
      <p className="helper-text">
        {labelByMode(
          languageMode,
          "Ranked using the newest automated market scan and current watchlist scores. A high score describes the technical setup; it does not guarantee profit.",
          "按最新自動市場掃描及目前觀察名單評分排序。高評分代表技術形態較強，並不保證獲利。"
        )}
      </p>
      {error ? <p className="helper-text warning-text">{error}</p> : null}
      {isLoading && !rows.length ? (
        <p>{labelByMode(languageMode, "Loading top scores...", "正在載入最高評分股票...")}</p>
      ) : null}
      {!isLoading && !rows.length ? (
        <p>
          {labelByMode(
            languageMode,
            "No ticker scores are available yet. Refresh the dashboard or run a Virtual Trader market scan.",
            "目前尚未有股票評分。請重新整理儀表板，或執行虛擬交易員市場掃描。"
          )}
        </p>
      ) : null}
      {rows.length ? (
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
              {rows.map((item, index) => (
                <tr key={item.ticker}>
                  <td data-label={rankLabel}>{index + 1}</td>
                  <td data-label={tickerLabel}><strong>{item.ticker}</strong></td>
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

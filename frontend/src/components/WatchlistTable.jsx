import React from "react";
import { term } from "../i18n/terms";
import TickerClassificationTags from "./TickerClassificationTags";

export default function WatchlistTable({ rows, selectedTicker, onSelectTicker, languageMode }) {
  const rankLabel =
    languageMode === "zh" ? "排名" : languageMode === "en" ? "Rank" : "Rank / 排名";

  return (
    <section className="panel">
      <h3>
        {term("Watchlist", languageMode)} ({term("Ranked by Score", languageMode)})
      </h3>
      <div className="table-wrap responsive-card-table watchlist-ranking-table">
        <table>
          <thead>
            <tr>
              <th>Rank</th>
              <th>{term("Ticker", languageMode)}</th>
              <th>{term("Score", languageMode)}</th>
              <th>{term("Label", languageMode)}</th>
              <th>{term("Close", languageMode)}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((item, index) => {
              const isSelected = selectedTicker === item.ticker;
              return (
                <tr
                  key={item.ticker}
                  className={isSelected ? "selected-row" : ""}
                  onClick={() => onSelectTicker(item.ticker)}
                >
                  <td data-label={rankLabel}>{index + 1}</td>
                  <td data-label={term("Ticker", languageMode)}>
                    <span className="ticker-identity">
                      <span className="ticker-symbol">{item.ticker}</span>
                      <TickerClassificationTags
                        ticker={item.ticker}
                        classification={item}
                        languageMode={languageMode}
                        size="xs"
                      />
                    </span>
                  </td>
                  <td data-label={term("Score", languageMode)}>
                    {item.score_breakdown.total_score}
                  </td>
                  <td data-label={term("Label", languageMode)}>{item.label}</td>
                  <td data-label={term("Close", languageMode)}>
                    {item.latest_close.toFixed(2)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

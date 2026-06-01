import React from "react";
import { term } from "../i18n/terms";

export default function WatchlistTable({ rows, selectedTicker, onSelectTicker, languageMode }) {
  return (
    <section className="panel">
      <h3>
        {term("Watchlist", languageMode)} ({term("Ranked by Score", languageMode)})
      </h3>
      <div className="table-wrap">
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
                  <td>{index + 1}</td>
                  <td>{item.ticker}</td>
                  <td>{item.score_breakdown.total_score}</td>
                  <td>{item.label}</td>
                  <td>{item.latest_close.toFixed(2)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

import React, { useMemo, useState } from "react";

const GLOSSARY_ITEMS = [
  {
    key: "Paper trading",
    english: "Paper trading",
    chinese: "模擬交易",
    explanationEn: "Practising trades with virtual money. It cannot prove the same result will happen with real money.",
    explanationZh: "使用虛擬資金練習交易；不能證明真實資金會有相同結果。",
  },
  {
    key: "Equity",
    english: "Account equity",
    chinese: "帳戶資產淨值",
    explanationEn: "Current cash plus the market value of all holdings.",
    explanationZh: "目前現金加上所有持倉的市值。",
  },
  {
    key: "PnL",
    english: "Profit and loss (PnL)",
    chinese: "損益",
    explanationEn: "How much the account gained or lost from trading, separate from cash deposits.",
    explanationZh: "帳戶因交易賺取或虧損的金額，與現金入金分開計算。",
  },
  {
    key: "Model confidence",
    english: "Model confidence",
    chinese: "模型信心",
    explanationEn: "How strongly the model supports its signal. It is not the probability of making money.",
    explanationZh: "模型支持訊號的程度，並不是賺錢的機率。",
  },
  {
    key: "Position size",
    english: "Position size",
    chinese: "倉位大小",
    explanationEn: "How much of the account is invested in one ticker. Smaller positions reduce concentration risk.",
    explanationZh: "帳戶投資於單一股票的比例；較小倉位可降低集中風險。",
  },
  {
    key: "Market regime",
    english: "Market regime protection",
    chinese: "市場狀況保護",
    explanationEn: "A risk rule based on benchmark weakness, recent drawdown, and volatility. Normal uses full configured size, caution halves new positions, and stress blocks new buys.",
    explanationZh: "根據大市弱勢、近期回撤及波動程度作出的風險規則。正常時使用完整設定倉位，謹慎時新倉減半，壓力時暫停新買入。",
  },
  {
    key: "Stop loss",
    english: "Stop loss",
    chinese: "止蝕",
    explanationEn: "A rule that exits a losing position after it falls by a set amount.",
    explanationZh: "持倉下跌至指定幅度後離場的風險規則。",
  },
  {
    key: "Sharpe ratio",
    english: "Sharpe ratio",
    chinese: "夏普比率",
    explanationEn: "Return compared with volatility. Higher is generally better; a negative value indicates poor risk-adjusted results.",
    explanationZh: "回報相對波動的比率；通常越高越好，負數表示風險調整後表現欠佳。",
  },
  {
    key: "SMA",
    english: "Simple Moving Average",
    chinese: "簡單移動平均線",
    explanationEn: "Average close over a set period to show the main trend direction.",
    explanationZh: "把一段時間內的收市價取平均，用來看主要趨勢方向。",
  },
  {
    key: "EMA",
    english: "Exponential Moving Average",
    chinese: "指數移動平均線",
    explanationEn: "A moving average that gives more weight to recent prices.",
    explanationZh: "較重視近期價格變化的移動平均線。",
  },
  {
    key: "RSI",
    english: "Relative Strength Index",
    chinese: "相對強弱指數",
    explanationEn: "Momentum indicator from 0 to 100 that helps spot overbought or oversold zones.",
    explanationZh: "0 到 100 的動能指標，可用來觀察是否偏熱或偏弱。",
  },
  {
    key: "MACD",
    english: "Moving Average Convergence Divergence",
    chinese: "平滑異同移動平均線",
    explanationEn: "Compares fast and slow averages to show momentum changes.",
    explanationZh: "比較快線與慢線，幫助觀察動能轉強或轉弱。",
  },
  {
    key: "Support",
    english: "Support",
    chinese: "支撐位",
    explanationEn: "Price area where buying interest often appears.",
    explanationZh: "買盤較常出現的價位區域。",
  },
  {
    key: "Resistance",
    english: "Resistance",
    chinese: "阻力位",
    explanationEn: "Price area where selling pressure often appears.",
    explanationZh: "沽壓較常出現的價位區域。",
  },
  {
    key: "Volatility",
    english: "Volatility",
    chinese: "波動率",
    explanationEn: "How much price moves up and down over time.",
    explanationZh: "價格上下波動的幅度。",
  },
  {
    key: "Drawdown",
    english: "Drawdown",
    chinese: "回撤",
    explanationEn: "Percentage decline from a peak to a later low.",
    explanationZh: "由高位回落到之後低位的跌幅百分比。",
  },
  {
    key: "Benchmark",
    english: "Benchmark",
    chinese: "基準",
    explanationEn: "Reference ETF or index used for comparison, such as VOO.",
    explanationZh: "用來比較表現的參考 ETF 或指數，例如 VOO。",
  },
  {
    key: "Momentum",
    english: "Momentum",
    chinese: "動能",
    explanationEn: "Strength and speed of recent price movement.",
    explanationZh: "近期價格走勢的力度與速度。",
  },
  {
    key: "Trend",
    english: "Trend",
    chinese: "趨勢",
    explanationEn: "Overall direction of price: up, down, or sideways.",
    explanationZh: "價格整體方向，可以是向上、向下或橫行。",
  },
  {
    key: "Breakout",
    english: "Breakout",
    chinese: "突破",
    explanationEn: "Price moves through a key level with strength.",
    explanationZh: "價格帶著較強力度突破重要位置。",
  },
  {
    key: "Pullback",
    english: "Pullback",
    chinese: "回調",
    explanationEn: "Short counter-move against the main trend.",
    explanationZh: "沿著主趨勢走動時，途中出現的短暫逆向回落。",
  },
];

function displayByMode(english, chinese, mode) {
  if (mode === "en") return english;
  if (mode === "zh") return chinese;
  return `${english} / ${chinese}`;
}

export default function GlossaryPage({ languageMode }) {
  const [query, setQuery] = useState("");

  const filteredItems = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return GLOSSARY_ITEMS;

    return GLOSSARY_ITEMS.filter((item) => {
      return (
        item.key.toLowerCase().includes(keyword) ||
        item.english.toLowerCase().includes(keyword) ||
        item.chinese.toLowerCase().includes(keyword) ||
        item.explanationEn.toLowerCase().includes(keyword) ||
        item.explanationZh.toLowerCase().includes(keyword)
      );
    });
  }, [query]);

  return (
    <section className="panel glossary-panel">
      <h2>{displayByMode("Stock Glossary", "股票詞彙表", languageMode)}</h2>
      <p className="helper-text">
        {displayByMode(
          "Beginner-friendly definitions for common stock terms.",
          "用簡單方式解釋常見股票術語。",
          languageMode
        )}
      </p>

      <label htmlFor="glossary-search" className="glossary-search-label">
        {displayByMode("Search", "搜尋", languageMode)}
      </label>
      <input
        id="glossary-search"
        type="text"
        placeholder={displayByMode(
          "Type a term, for example RSI",
          "輸入詞語，例如 RSI",
          languageMode
        )}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        className="glossary-search-input"
      />

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{displayByMode("Term", "詞語", languageMode)}</th>
              <th>{displayByMode("Short Explanation", "簡短解釋", languageMode)}</th>
            </tr>
          </thead>
          <tbody>
            {filteredItems.map((item) => (
              <tr key={item.key}>
                <td>{displayByMode(item.english, item.chinese, languageMode)}</td>
                <td>{displayByMode(item.explanationEn, item.explanationZh, languageMode)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="helper-text glossary-count">
        {displayByMode("Results", "結果", languageMode)}: {filteredItems.length}
      </p>
    </section>
  );
}

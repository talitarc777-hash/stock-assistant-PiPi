import React from "react";

import LineChart from "./LineChart";

export default function EquityChart({
  ticker,
  points = [],
  languageMode = "both",
  title: titleOverride = null,
  subtitle: subtitleOverride = null,
}) {
  const title =
    titleOverride ||
    (languageMode === "zh"
      ? "\u865b\u64ec\u4ea4\u6613\u8cc7\u7522\u66f2\u7dda"
      : languageMode === "en"
        ? "Virtual Trader Equity Curve"
        : "Virtual Trader Equity Curve / \u865b\u64ec\u4ea4\u6613\u8cc7\u7522\u66f2\u7dda");
  const subtitle =
    subtitleOverride ||
    (languageMode === "zh"
      ? `\u80a1\u7968: ${ticker}`
      : languageMode === "en"
        ? `Ticker: ${ticker}`
        : `Ticker: ${ticker} / \u80a1\u7968: ${ticker}`);

  return (
    <LineChart
      title={title}
      subtitle={subtitle}
      points={points}
      xAxisLabel="Date"
      yAxisLabel="Portfolio Value (USD)"
      yValueKind="price"
      lines={[
        {
          key: "total_equity",
          label: "Portfolio Value",
          color: "#334155",
          strokeWidth: 2.8,
          valueKind: "price",
        },
        {
          key: "cash",
          label: "Cash",
          color: "#0891b2",
          strokeWidth: 1.9,
          valueKind: "price",
        },
        {
          key: "holdings_value",
          label: "Invested Value",
          color: "#7c3aed",
          strokeWidth: 1.9,
          valueKind: "price",
        },
        {
          key: "benchmark_equity",
          label: "VOO Buy-and-Hold",
          color: "#16a34a",
          strokeWidth: 2.2,
          valueKind: "price",
          dashArray: "5 4",
        },
      ]}
      noDataMessage="No data available"
      showRangeSelector
      defaultRange="6M"
    />
  );
}

import React from "react";

import LineChart from "./LineChart";

export default function PriceChart({
  ticker,
  periodLabel = "Last 6 Months",
  points,
  supportLevel,
  resistanceLevel,
  expectedRange,
  languageMode = "both",
}) {
  const title = languageMode === "zh" ? "價格趨勢" : languageMode === "en" ? "Price Trend" : "Price Trend / 價格趨勢";
  const subtitle =
    languageMode === "zh"
      ? `股票: ${ticker} | ${periodLabel}`
      : languageMode === "en"
        ? `Ticker: ${ticker} | ${periodLabel}`
        : `Ticker: ${ticker} / 股票: ${ticker} | ${periodLabel}`;

  return (
    <LineChart
      title={title}
      subtitle={subtitle}
      points={points}
      xAxisLabel="Date"
      yAxisLabel="Price (USD)"
      yValueKind="price"
      lines={[
        { key: "close", label: "Close Price", color: "#334155", strokeWidth: 2.6, valueKind: "price" },
        { key: "sma_20", label: "SMA20", color: "#3b82f6", strokeWidth: 1.8, valueKind: "price" },
        { key: "sma_50", label: "SMA50", color: "#22c55e", strokeWidth: 1.8, valueKind: "price" },
        { key: "sma_200", label: "SMA200", color: "#f59e0b", strokeWidth: 1.8, valueKind: "price" },
      ]}
      overlays={{
        horizontalLines: [
          {
            key: "support",
            label: "Support",
            value: supportLevel,
            color: "#0f766e",
          },
          {
            key: "resistance",
            label: "Resistance",
            value: resistanceLevel,
            color: "#b45309",
          },
        ],
        rangeBand:
          expectedRange && Number.isFinite(expectedRange.lower) && Number.isFinite(expectedRange.upper)
            ? {
                key: "expected-range",
                label: "Expected Range",
                lower: expectedRange.lower,
                upper: expectedRange.upper,
                color: "#2563eb",
              }
            : null,
      }}
      noDataMessage="No data available"
      showRangeSelector
      defaultRange="6M"
    />
  );
}

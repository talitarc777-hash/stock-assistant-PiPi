import React, { useMemo } from "react";

import LineChart from "./LineChart";

export default function PredictionChart({ ticker, points = [], languageMode = "both" }) {
  const title =
    languageMode === "zh"
      ? "預測與實際結果"
      : languageMode === "en"
        ? "Prediction vs Actual"
        : "Prediction vs Actual / 預測與實際結果";
  const subtitle =
    languageMode === "zh"
      ? `股票: ${ticker}`
      : languageMode === "en"
        ? `Ticker: ${ticker}`
        : `Ticker: ${ticker} / 股票: ${ticker}`;

  const markerPoints = useMemo(() => {
    return points
      .filter((point) => Number.isFinite(point.predicted_value))
      .map((point) => {
        const isBuySignal = Number(point.predicted_value) >= 0.5;
        return {
          key: isBuySignal ? "buy" : "sell",
          date: point.date,
          value: Number(point.predicted_value),
          color: isBuySignal ? "#2563eb" : "#dc2626",
        };
      });
  }, [points]);

  return (
    <LineChart
      title={title}
      subtitle={subtitle}
      points={points}
      xAxisLabel="Date"
      yAxisLabel="Return (%)"
      yValueKind="percent"
      lines={[
        {
          key: "predicted_value",
          label: "Prediction",
          color: "#2563eb",
          strokeWidth: 2.6,
          valueKind: "percent",
        },
        {
          key: "actual_future_result",
          label: "Actual",
          color: "#059669",
          strokeWidth: 2.4,
          valueKind: "percent",
        },
      ]}
      markers={markerPoints}
      noDataMessage="No data available"
      showRangeSelector
      defaultRange="6M"
    />
  );
}

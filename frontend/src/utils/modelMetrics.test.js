import assert from "node:assert/strict";
import test from "node:test";

import {
  buildModelPerformanceRows,
  formatModelRate,
  historicalAccuracy,
  liveMatchingRate,
  predictionRate,
} from "./modelMetrics.js";

const record = {
  model_name: "random_forest",
  metrics_summary: {
    metrics: { direction_accuracy: 0.54 },
    walk_forward_quality_gate: {
      direction_accuracy: 0.58,
      predicted_up_rate: 0.62,
    },
    live_feedback: {
      sample_count: 10,
      direction_accuracy: 0.7,
    },
  },
};

test("model metrics prefer current walk-forward and live evidence", () => {
  assert.equal(historicalAccuracy(record), 0.58);
  assert.equal(predictionRate(record), 0.62);
  assert.equal(liveMatchingRate(record), 0.7);
  assert.equal(formatModelRate(0.58), "58.0%");
  assert.equal(formatModelRate(undefined), "N/A");
  assert.equal(formatModelRate(null), "N/A");
});

test("model performance aggregates medians and sample-weighted live matching", () => {
  const second = {
    ...record,
    metrics_summary: {
      ...record.metrics_summary,
      walk_forward_quality_gate: {
        direction_accuracy: 0.66,
        predicted_up_rate: 0.42,
      },
      live_feedback: {
        sample_count: 30,
        direction_accuracy: 0.5,
      },
    },
  };
  const [summary] = buildModelPerformanceRows([record, second], ["random_forest"]);
  assert.equal(summary.recordCount, 2);
  assert.equal(summary.accuracyRate, 0.62);
  assert.equal(summary.predictionRate, 0.52);
  assert.equal(summary.matchingRate, 0.55);
  assert.equal(summary.matchingSamples, 40);
});

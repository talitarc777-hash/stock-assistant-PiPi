function finiteRate(value) {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric >= 0 && numeric <= 1 ? numeric : null;
}

function median(values) {
  const clean = values
    .map(finiteRate)
    .filter((value) => value !== null)
    .sort((left, right) => left - right);
  if (!clean.length) return null;
  const middle = Math.floor(clean.length / 2);
  return clean.length % 2
    ? clean[middle]
    : (clean[middle - 1] + clean[middle]) / 2;
}

export function historicalAccuracy(item) {
  const summary = item?.metrics_summary || {};
  const quality = summary.walk_forward_quality_gate || {};
  const metrics = summary.metrics || {};
  return finiteRate(
    quality.direction_accuracy
      ?? metrics.direction_accuracy
      ?? metrics.accuracy
  );
}

export function predictionRate(item) {
  const summary = item?.metrics_summary || {};
  const quality = summary.walk_forward_quality_gate || {};
  const metrics = summary.metrics || {};
  return finiteRate(
    quality.predicted_up_rate
      ?? metrics.positive_rate_predicted
  );
}

export function liveMatchingRate(item) {
  const feedback = item?.metrics_summary?.live_feedback || {};
  return finiteRate(feedback.direction_accuracy);
}

export function formatModelRate(value) {
  const rate = finiteRate(value);
  return rate === null ? "N/A" : `${(rate * 100).toFixed(1)}%`;
}

export function buildModelPerformanceRows(registry, modelNames) {
  return modelNames.map((modelName) => {
    const records = registry.filter((item) => item.model_name === modelName);
    const matchingRecords = records
      .map((item) => ({
        rate: liveMatchingRate(item),
        samples: Number(item?.metrics_summary?.live_feedback?.sample_count || 0),
      }))
      .filter((item) => item.rate !== null && item.samples > 0);
    const matchingSamples = matchingRecords.reduce((total, item) => total + item.samples, 0);
    const matchingRate = matchingSamples
      ? matchingRecords.reduce((total, item) => total + item.rate * item.samples, 0) / matchingSamples
      : null;

    return {
      modelName,
      recordCount: records.length,
      accuracyRate: median(records.map(historicalAccuracy)),
      predictionRate: median(records.map(predictionRate)),
      matchingRate,
      matchingSamples,
    };
  });
}

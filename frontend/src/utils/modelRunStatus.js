function label(mode, en, zh) {
  return mode === "zh" ? zh : mode === "en" ? en : `${en} / ${zh}`;
}

export function modelRunStatus(run, mode = "bilingual") {
  const skipped = Number(run.details?.evidence_gated_skipped_jobs || 0);
  const failed = Number(run.failed_models || 0);
  // Interpret historical records too: they used "failed" for mixed skipped/error jobs.
  if (run.status === "skipped_with_errors" ||
      (run.status === "failed" && Number(run.successful_models || 0) === 0 && skipped > 0 && failed > 0)) {
    const pooled = run.details?.errors?.some(message => String(message).startsWith("GLOBAL/"));
    return label(mode, `Skipped; ${failed} ${pooled ? "training" : "ticker"} error${failed === 1 ? "" : "s"}`,
      `已略過；${failed} 個${pooled ? "訓練" : "股票"}錯誤`);
  }
  const labels = {
    success: ["Completed", "已完成"],
    partial_success: ["Partly completed", "部分完成"],
    skipped_no_new_evidence: ["Skipped: insufficient new evidence", "已略過：新增證據不足"],
    failed: ["Failed", "失敗"],
    running: ["Running", "執行中"],
  };
  const value = labels[run.status];
  return value ? label(mode, ...value) : run.status || "N/A";
}

export function modelRunErrors(run) {
  const errors = Array.isArray(run.details?.errors) ? run.details.errors : [];
  const values = errors.length ? errors : run.error_message ? [run.error_message] : [];
  // Retain the ticker/reason but omit provider URLs, local paths and credentials.
  return values.map(value => String(value)
    .replace(/https?:\/\/[^\s'"<>]+/gi, "[provider URL]")
    .replace(/(?:[A-Z]:[\\/]|\/(?:home|srv|data|tmp|var|root|etc)\/|data\/models\/)[^\s'"<>]+/gi, "[artifact path]")
    .replace(/((?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+/gi, "$1[redacted]"));
}

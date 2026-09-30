export function learningReason(code, mode = "en") {
  const labels = {
    collecting_or_evaluating: ["Collecting / evaluating", "收集及評估中"],
    attention_needed: ["Attention needed", "需要注意"],
    no_matured_prospective_panel: ["Waiting for first five-session outcomes", "等待首批五個交易日結果"],
    collect_more_matched_context_and_outcomes: ["Collecting sufficient matched context and outcomes", "收集足夠配對資訊及結果"],
    forward_test_in_progress: ["Forward test accumulating; no retraining", "前瞻測試累積中，暫不重新訓練"],
    waiting_for_five_new_matured_dates: ["Waiting for five new matured dates", "等待五個新的已到期日期"],
    training_or_retry_backoff: ["Training / waiting for retry", "訓練中或等待重試"],
    ready: ["Ready for next scheduler pass", "等待下次排程啟動"],
    not_enough_matured_dates: ["Waiting for 120 matured training dates", "等待120個已到期訓練日期"],
    cohort_coverage_incomplete: ["Some cohort tickers have fewer than 90 dates", "部分研究股票少於90個日期"],
    insufficient_external_feature_coverage: ["External features lack coverage or variation", "外部特徵覆蓋或變化不足"],
    external_context_enabled_disabled: ["External context is disabled", "外部資訊已停用"],
    context_archive_enabled_disabled: ["Context archive is disabled", "資訊儲存已停用"],
    model_feedback_enabled_disabled: ["Outcome settlement is disabled", "結果評估已停用"],
    five_session_feedback_required: ["Feedback horizon must be five sessions", "回饋期必須為五個交易日"],
    research_cycle_failed: ["Research cycle failed; inspect retry status", "研究排程失敗，請查看重試狀態"],
    research_cycle_running: ["Research cycle is currently running", "研究排程正在執行"],
    US_no_recent_usable_context: ["US context coverage is stale or missing", "美股資訊過期或缺失"],
    HK_no_recent_usable_context: ["HK context coverage is stale or missing", "港股資訊過期或缺失"],
    US_research_price_snapshots_stale: ["US research price snapshots are over seven days old", "美股研究價格樣本超過七天未更新"],
    HK_research_price_snapshots_stale: ["HK research price snapshots are over seven days old", "港股研究價格樣本超過七天未更新"],
    research_heartbeat_missing_or_stale: ["Research heartbeat missing or over two hours old", "研究心跳缺失或超過兩小時"],
    no_recent_usable_context: ["No usable context collected in the last 24 hours", "過去24小時沒有可用資訊"],
    protocol_changed_requires_review: ["Experiment contract changed: review required", "實驗規格變更，需要審核"],
    lifecycle_scheduler_not_started: ["Lifecycle scheduler not started", "模型排程未啟動"],
    prospective_research_enabled_disabled: ["Enable PROSPECTIVE_RESEARCH_ENABLED on backend", "請在後端啟用 PROSPECTIVE_RESEARCH_ENABLED"],
    missing_credentials: ["Configuration missing", "缺少設定"],
    unsupported_market: ["Market unsupported", "不支援此市場"],
    budget_exhausted: ["Daily budget reached", "已達每日請求上限"],
    rate_limited: ["Provider rate limit", "供應商流量限制"],
    unverified_entity: ["Unverified company identity", "未核實公司身份"],
    usable: ["Usable", "可用"], unavailable: ["Unavailable", "不可用"],
    empty: ["No matched data", "沒有匹配資料"], disabled: ["Disabled", "已停用"],
  };
  const [en, zh] = labels[code] || [String(code || "Unknown").replaceAll("_", " "), String(code || "未知").replaceAll("_", " ")];
  return mode === "zh" ? zh : mode === "en" ? en : `${en} / ${zh}`;
}

export function percentOrMissing(value) {
  return value == null || !Number.isFinite(Number(value)) ? "N/A" : `${(Number(value) * 100).toFixed(1)}%`;
}

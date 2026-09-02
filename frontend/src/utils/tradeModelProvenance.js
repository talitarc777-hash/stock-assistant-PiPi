function labelByMode(mode, en, zh) {
  if (mode === "zh") return zh;
  if (mode === "en") return en;
  return `${en} / ${zh}`;
}

/**
 * Return the model that actually drove a live-trader decision or execution.
 * Decision rows expose it at the top level, while account-ledger rows retain
 * it inside metadata, so both shapes are handled here.
 */
export function modelUsedText(item, languageMode = "bilingual") {
  const metadata = item?.metadata || {};
  const source = String(metadata.decision_source || "").trim().toLowerCase();
  const actualName = metadata.actual_model_name;
  const storedName = metadata.model_name || item?.model_name;
  const normalizedName = String(actualName || storedName || "").trim().toLowerCase();

  if (
    source === "fallback_rule"
    || ["backup_rules", "rule_based_fallback", "fallback"].includes(normalizedName)
    || (!actualName && normalizedName === "auto_best")
  ) {
    return labelByMode(languageMode, "Backup rules", "\u5f8c\u5099\u898f\u5247");
  }

  if (!normalizedName) {
    return labelByMode(languageMode, "Not recorded", "\u672a\u8a18\u9304");
  }

  const period = metadata.model_period || item?.model_period;
  return `${actualName || storedName}${period ? ` (${period})` : ""}`;
}

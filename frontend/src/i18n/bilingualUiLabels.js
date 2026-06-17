export function labelByMode(mode, en, zh) {
  if (mode === "zh") return zh;
  if (mode === "en") return en;
  return `${en} / ${zh}`;
}

export const MONTHLY_CONTRIBUTION_LABELS = {
  title: {
    en: "Monthly Contribution Records",
    zh: "\u6bcf\u6708\u6ce8\u8cc7\u7d00\u9304",
  },
  helper: {
    en: "Records start from April 2026. Set the available money for each month in USD. Zero means no contribution for that month.",
    zh: "\u7d00\u9304\u6703\u7531 2026 \u5e74 4 \u6708\u958b\u59cb\u3002\u60a8\u53ef\u4ee5\u8f38\u5165\u6bcf\u6708\u53ef\u7528\u8cc7\u91d1\uff08\u7f8e\u5143\uff09\u3002\u5982\u67d0\u6708\u8f38\u5165 0\uff0c\u4ee3\u8868\u8a72\u6708\u4e0d\u6ce8\u8cc7\u3002",
  },
  loading: {
    en: "Loading...",
    zh: "\u8f09\u5165\u4e2d...",
  },
  month: {
    en: "Month",
    zh: "\u6708\u4efd",
  },
  amount: {
    en: "Available Money (USD)",
    zh: "\u672c\u6708\u53ef\u7528\u8cc7\u91d1\uff08\u7f8e\u5143\uff09",
  },
  saved: {
    en: "Monthly contribution records saved.",
    zh: "\u6bcf\u6708\u6ce8\u8cc7\u7d00\u9304\u5df2\u5132\u5b58\u3002",
  },
  saving: {
    en: "Saving...",
    zh: "\u5132\u5b58\u4e2d...",
  },
  save: {
    en: "Save Contribution Records",
    zh: "\u5132\u5b58\u6ce8\u8cc7\u7d00\u9304",
  },
};

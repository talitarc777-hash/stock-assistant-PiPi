function labelByMode(mode, en, zh) {
  if (mode === "zh") return zh;
  if (mode === "en") return en;
  return `${en} / ${zh}`;
}

export function marketRegimeGuide(regime, languageMode) {
  const level = String(regime?.level || "unknown").toLowerCase();
  const multiplier = Number(regime?.position_size_multiplier);
  const sizePercent = Number.isFinite(multiplier) ? Math.round(multiplier * 100) : null;
  const levels = {
    normal: ["Normal market conditions", "一般市場狀況"],
    caution: ["Cautious market conditions", "需要審慎的市場狀況"],
    stress: ["High-risk market conditions", "高風險市場狀況"],
    unknown: ["Market protection unavailable", "未有市場保護資料"],
  };
  const reasonLabels = {
    benchmark_20d_selloff: ["the wider market fell sharply over the last month", "整體市場在最近一個月大幅下跌"],
    benchmark_20d_weakness: ["the wider market was weak over the last month", "整體市場在最近一個月表現疲弱"],
    ticker_deep_drawdown: ["this stock is far below its recent peak", "這隻股票遠低於近期高位"],
    ticker_drawdown: ["this stock has fallen from its recent peak", "這隻股票已由近期高位回落"],
    extreme_volatility: ["the price is moving extremely sharply", "價格波動極為劇烈"],
    elevated_volatility: ["the price is moving more sharply than usual", "價格波動較平常劇烈"],
  };
  const reasons = (regime?.reasons || []).map((reason) => {
    const pair = reasonLabels[String(reason)] || [String(reason).replaceAll("_", " "), String(reason).replaceAll("_", " ")];
    return labelByMode(languageMode, ...pair);
  });
  let effect;
  if (regime?.new_position_allowed === false || sizePercent === 0) {
    effect = labelByMode(languageMode, "New buying is paused to limit losses.", "為限制損失，現時暫停新買入。");
  } else if (sizePercent !== null && sizePercent < 100) {
    effect = labelByMode(languageMode, `New buys are reduced to ${sizePercent}% of normal size.`, `新買入金額減至正常的 ${sizePercent}%。`);
  } else {
    effect = labelByMode(languageMode, "New buys may use the normal position size.", "新買入可使用正常倉位大小。");
  }
  return {
    label: labelByMode(languageMode, ...(levels[level] || levels.unknown)),
    effect,
    reasons: reasons.length
      ? reasons.join("; ")
      : labelByMode(languageMode, "No broad-market risk trigger is active.", "目前沒有觸發整體市場風險規則。"),
  };
}

export function marketDataReasonText(reason, languageMode) {
  const labels = {
    ohlc_below_reported_low: ["the reported daily prices contradict each other", "每日價格資料互相矛盾"],
    ohlc_above_reported_high: ["the reported daily prices contradict each other", "每日價格資料互相矛盾"],
    non_positive_price: ["a price is zero or negative", "價格為零或負數"],
    negative_volume: ["trading volume is negative", "成交量為負數"],
    price_timestamp_missing: ["the price time is missing", "缺少價格時間"],
    price_timestamp_in_future: ["the price time is in the future", "價格時間在未來"],
    price_older_than_two_business_days: ["the latest price is more than two business days old", "最新價格已超過兩個工作日"],
    feature_timestamp_missing: ["the model-input time is missing", "缺少模型輸入時間"],
    feature_data_newer_than_price_snapshot: ["model inputs are newer than the price snapshot", "模型輸入比價格快照更新"],
    feature_data_too_old: ["model inputs are too old", "模型輸入已過時"],
    same_day_close_mismatch: ["two sources report different closing prices", "兩個資料來源的收市價不一致"],
  };
  const normalized = String(reason || "");
  if (normalized.endsWith("_missing_or_invalid")) {
    const field = normalized.replace("_missing_or_invalid", "").replaceAll("_", " ");
    return labelByMode(languageMode, `${field} is missing or invalid`, `${field} 資料缺失或無效`);
  }
  const pair = labels[normalized] || [normalized.replaceAll("_", " "), normalized.replaceAll("_", " ")];
  return labelByMode(languageMode, ...pair);
}

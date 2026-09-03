import { TICKER_NAME_OVERRIDES } from "../config/tickerNames.js";

function firstName(candidates, ticker) {
  const symbol = String(ticker || "").trim();
  const value = candidates.find((candidate) => {
    const text = String(candidate || "").trim();
    return text && text.toUpperCase() !== symbol.toUpperCase();
  });
  return String(value || "").trim();
}

function normalizedTicker(ticker) {
  const value = String(ticker || "").trim().toUpperCase();
  const hkMatch = value.match(/^(\d{1,4})(?:\.HK)?$/);
  return hkMatch ? hkMatch[1].padStart(4, "0") : value;
}

export function tickerDisplayNames(data, ticker = data?.ticker) {
  const metadata = data?.metadata || {};
  const symbol = normalizedTicker(ticker);
  const override = TICKER_NAME_OVERRIDES[symbol] || {};
  const english = firstName([
    data?.ticker_name_en,
    data?.company_name_en,
    data?.security_name_en,
    metadata.ticker_name_en,
    metadata.company_name_en,
    metadata.security_name_en,
    data?.ticker_name,
    data?.company_name,
    data?.security_name,
    metadata.ticker_name,
    metadata.company_name,
    metadata.security_name,
    override.en,
  ], ticker);
  const chinese = firstName([
    data?.ticker_name_zh,
    data?.company_name_zh,
    data?.security_name_zh,
    data?.chinese_name,
    metadata.ticker_name_zh,
    metadata.company_name_zh,
    metadata.security_name_zh,
    metadata.chinese_name,
    override.zh,
  ], ticker);
  return { en: english, zh: chinese };
}

export function tickerDisplayName(data, ticker = data?.ticker, languageMode = "en") {
  const names = tickerDisplayNames(data, ticker);
  if (languageMode === "zh") return names.zh || names.en;
  if (languageMode === "both") {
    if (names.en && names.zh && names.en.toLocaleLowerCase() !== names.zh.toLocaleLowerCase()) {
      return `${names.en} / ${names.zh}`;
    }
    return names.en || names.zh;
  }
  return names.en || names.zh;
}

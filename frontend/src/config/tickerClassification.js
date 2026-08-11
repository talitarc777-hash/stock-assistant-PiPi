export const PRIMARY_TICKER_CLASS_CONFIG = Object.freeze({
  stock: { label: { en: "Stock", zh: "股票" }, variant: "blue", order: 10, description: "Listed company equity" },
  etf: { label: { en: "ETF", zh: "交易所買賣基金" }, variant: "purple", order: 20, description: "Exchange-traded fund" },
  index: { label: { en: "Index", zh: "指數" }, variant: "indigo", order: 30, description: "Market index" },
  reit: { label: { en: "REIT", zh: "房地產信託" }, variant: "amber", order: 40, description: "Real estate investment trust" },
  fixed_income: { label: { en: "Bond / Fixed Income", zh: "債券／固定收益" }, variant: "teal", order: 50, description: "Bond or fixed-income instrument" },
  commodity: { label: { en: "Commodity", zh: "商品" }, variant: "orange", order: 60, description: "Commodity exposure" },
  forex: { label: { en: "Forex", zh: "外匯" }, variant: "cyan", order: 70, description: "Foreign-exchange pair" },
  crypto: { label: { en: "Crypto", zh: "加密資產" }, variant: "gold", order: 80, description: "Cryptocurrency asset or pair" },
  derivative: { label: { en: "Derivative", zh: "衍生工具" }, variant: "red", order: 90, description: "Future, option, or other derivative" },
  cash: { label: { en: "Cash / Money Market", zh: "現金／貨幣市場" }, variant: "green", order: 100, description: "Cash or money-market instrument" },
  unknown: { label: { en: "Unknown", zh: "未分類" }, variant: "neutral", order: 110, description: "Classification is not yet reliable" },
});

export const STOCK_SUBCLASS_CONFIG = Object.freeze({
  technology: { label: { en: "Technology", zh: "科技" }, variant: "violet", order: 10 },
  financials: { label: { en: "Financials", zh: "金融" }, variant: "emerald", order: 20 },
  consumer_cyclical: { label: { en: "Consumer Cyclical", zh: "週期性消費" }, variant: "pink", order: 30 },
  consumer_defensive: { label: { en: "Consumer Defensive", zh: "防守性消費" }, variant: "lime", order: 40 },
  healthcare: { label: { en: "Healthcare", zh: "醫療保健" }, variant: "rose", order: 50 },
  industrials: { label: { en: "Industrials", zh: "工業" }, variant: "slate", order: 60 },
  energy: { label: { en: "Energy", zh: "能源" }, variant: "orange", order: 70 },
  materials: { label: { en: "Materials", zh: "原材料" }, variant: "stone", order: 80 },
  utilities: { label: { en: "Utilities", zh: "公用事業" }, variant: "sky", order: 90 },
  real_estate: { label: { en: "Real Estate", zh: "房地產" }, variant: "amber", order: 100 },
  communication_services: { label: { en: "Communication Services", zh: "通訊服務" }, variant: "fuchsia", order: 110 },
  other: { label: { en: "Other", zh: "其他" }, variant: "neutral", order: 120 },
  unknown: { label: { en: "Unknown", zh: "未分類" }, variant: "neutral", order: 130 },
});

export const TICKER_CLASSIFICATION_OVERRIDES = Object.freeze({
  AAPL: { primaryClass: "stock", stockSubclass: "technology" },
  MSFT: { primaryClass: "stock", stockSubclass: "technology" },
  NVDA: { primaryClass: "stock", stockSubclass: "technology" },
  AMD: { primaryClass: "stock", stockSubclass: "technology" },
  AVGO: { primaryClass: "stock", stockSubclass: "technology" },
  ORCL: { primaryClass: "stock", stockSubclass: "technology" },
  CRM: { primaryClass: "stock", stockSubclass: "technology" },
  ADBE: { primaryClass: "stock", stockSubclass: "technology" },
  CSCO: { primaryClass: "stock", stockSubclass: "technology" },
  ACN: { primaryClass: "stock", stockSubclass: "technology" },
  INTC: { primaryClass: "stock", stockSubclass: "technology" },
  TXN: { primaryClass: "stock", stockSubclass: "technology" },
  QCOM: { primaryClass: "stock", stockSubclass: "technology" },
  AMZN: { primaryClass: "stock", stockSubclass: "consumer_cyclical" },
  TSLA: { primaryClass: "stock", stockSubclass: "consumer_cyclical" },
  HD: { primaryClass: "stock", stockSubclass: "consumer_cyclical" },
  MCD: { primaryClass: "stock", stockSubclass: "consumer_cyclical" },
  GOOG: { primaryClass: "stock", stockSubclass: "communication_services" },
  GOOGL: { primaryClass: "stock", stockSubclass: "communication_services" },
  META: { primaryClass: "stock", stockSubclass: "communication_services" },
  DIS: { primaryClass: "stock", stockSubclass: "communication_services" },
  CMCSA: { primaryClass: "stock", stockSubclass: "communication_services" },
  JPM: { primaryClass: "stock", stockSubclass: "financials" },
  BAC: { primaryClass: "stock", stockSubclass: "financials" },
  WFC: { primaryClass: "stock", stockSubclass: "financials" },
  XOM: { primaryClass: "stock", stockSubclass: "energy" },
  MRK: { primaryClass: "stock", stockSubclass: "healthcare" },
  JNJ: { primaryClass: "stock", stockSubclass: "healthcare" },
  ABBV: { primaryClass: "stock", stockSubclass: "healthcare" },
  TMO: { primaryClass: "stock", stockSubclass: "healthcare" },
  ABT: { primaryClass: "stock", stockSubclass: "healthcare" },
  COST: { primaryClass: "stock", stockSubclass: "consumer_defensive" },
  KO: { primaryClass: "stock", stockSubclass: "consumer_defensive" },
  PEP: { primaryClass: "stock", stockSubclass: "consumer_defensive" },
  PM: { primaryClass: "stock", stockSubclass: "consumer_defensive" },
  "0700.HK": { primaryClass: "stock", stockSubclass: "communication_services" },
  "0700": { primaryClass: "stock", stockSubclass: "communication_services" },
  "9988.HK": { primaryClass: "stock", stockSubclass: "consumer_cyclical" },
  "9988": { primaryClass: "stock", stockSubclass: "consumer_cyclical" },
  VOO: { primaryClass: "etf" },
  SPY: { primaryClass: "etf" },
  QQQ: { primaryClass: "etf" },
  VTI: { primaryClass: "etf" },
  "^GSPC": { primaryClass: "index" },
  "^DJI": { primaryClass: "index" },
  "^IXIC": { primaryClass: "index" },
  "BTC-USD": { primaryClass: "crypto" },
  "ETH-USD": { primaryClass: "crypto" },
  "XAUUSD=X": { primaryClass: "commodity" },
  US10Y: { primaryClass: "fixed_income" },
  USD: { primaryClass: "cash" },
  HKD: { primaryClass: "cash" },
  CASH: { primaryClass: "cash" },
});

const SECTOR_TO_STOCK_SUBCLASS = Object.freeze({
  technology: "technology",
  "financial services": "financials",
  financials: "financials",
  "consumer cyclical": "consumer_cyclical",
  "consumer defensive": "consumer_defensive",
  healthcare: "healthcare",
  industrials: "industrials",
  energy: "energy",
  "basic materials": "materials",
  materials: "materials",
  utilities: "utilities",
  "real estate": "real_estate",
  "communication services": "communication_services",
});

const QUOTE_TYPE_TO_PRIMARY_CLASS = Object.freeze({
  EQUITY: "stock",
  ETF: "etf",
  INDEX: "index",
  CURRENCY: "forex",
  CRYPTOCURRENCY: "crypto",
  FUTURE: "derivative",
  OPTION: "derivative",
});

const CRYPTO_BASES = new Set(["BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "BNB", "AVAX", "DOT", "LTC"]);
const FOREX_CODES = new Set(["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "HKD", "CNY", "CNH", "SGD"]);

export function normalizeTickerSymbol(value) {
  const raw = String(value || "").trim().toUpperCase().replace(/\s+/g, "");
  if (!raw) return "";
  const hkMatch = raw.match(/^(\d{1,4})\.HK$/);
  if (hkMatch) return `${hkMatch[1].padStart(4, "0")}.HK`;
  const pairMatch = raw.match(/^([A-Z]{3,10})\/([A-Z]{3,4})$/);
  if (pairMatch) {
    const [, base, quote] = pairMatch;
    if (CRYPTO_BASES.has(base)) return `${base}-${quote}`;
    if (FOREX_CODES.has(base) && FOREX_CODES.has(quote)) return `${base}${quote}=X`;
  }
  if (/^[A-Z]{1,5}\.[A-Z]$/.test(raw)) return raw.replace(".", "-");
  return raw;
}

function validConfigValue(value, config) {
  const normalized = String(value || "").trim().toLowerCase().replace(/[ -]/g, "_");
  return Object.hasOwn(config, normalized) ? normalized : null;
}

function inferredPrimaryClass(ticker) {
  if (ticker.startsWith("^")) return "index";
  if (ticker.endsWith("=F")) return "derivative";
  if (ticker.endsWith("=X")) return "forex";
  const cryptoMatch = ticker.match(/^([A-Z0-9]{2,10})-(USD|USDT|EUR|GBP)$/);
  if (cryptoMatch && CRYPTO_BASES.has(cryptoMatch[1])) return "crypto";
  if (["USD", "HKD", "CASH", "MMF"].includes(ticker)) return "cash";
  return null;
}

export function resolveTickerClassification(input, explicit = {}) {
  const data = typeof input === "string" ? { ticker: input } : (input || {});
  const ticker = normalizeTickerSymbol(explicit.ticker || data.ticker || data.symbol);
  const quoteType = String(explicit.quoteType || data.quote_type || data.quoteType || "").toUpperCase();
  const sector = String(explicit.sector || data.sector || "").trim().toLowerCase();
  const industry = String(explicit.industry || data.industry || "").trim().toLowerCase();
  const backendPrimary = validConfigValue(
    explicit.primaryClass || data.primary_ticker_class || data.primaryClass,
    PRIMARY_TICKER_CLASS_CONFIG
  );
  const backendSubclass = validConfigValue(
    explicit.stockSubclass || data.stock_subclass || data.stockSubclass,
    STOCK_SUBCLASS_CONFIG
  );
  const override = TICKER_CLASSIFICATION_OVERRIDES[ticker] || {};

  let primaryClass = backendPrimary && backendPrimary !== "unknown" ? backendPrimary : null;
  let source = primaryClass ? (data.classification_source || "backend") : "";
  if (!primaryClass && QUOTE_TYPE_TO_PRIMARY_CLASS[quoteType]) {
    primaryClass = QUOTE_TYPE_TO_PRIMARY_CLASS[quoteType];
    if (primaryClass === "stock" && industry.includes("reit")) primaryClass = "reit";
    source = "market_data";
  }
  if (!primaryClass && sector) {
    primaryClass = "stock";
    source = "market_data";
  }
  if (!primaryClass && override.primaryClass) {
    primaryClass = override.primaryClass;
    source = "manual_override";
  }
  if (!primaryClass) {
    primaryClass = inferredPrimaryClass(ticker);
    if (primaryClass) source = "symbol_pattern";
  }
  primaryClass = primaryClass || "unknown";
  source = source || "unknown";

  let stockSubclass = null;
  if (primaryClass === "stock") {
    stockSubclass = backendSubclass && backendSubclass !== "unknown" ? backendSubclass : null;
    stockSubclass = stockSubclass || SECTOR_TO_STOCK_SUBCLASS[sector] || override.stockSubclass || "unknown";
  }

  return { ticker, primaryClass, stockSubclass, source };
}

export function classificationLabel(configItem, languageMode = "en") {
  if (!configItem?.label) return "Unknown";
  if (languageMode === "zh") return configItem.label.zh;
  if (languageMode === "both") return `${configItem.label.en} / ${configItem.label.zh}`;
  return configItem.label.en;
}

export function matchesTickerClassificationFilters(item, primaryFilter = "all", subclassFilter = "all") {
  const classification = resolveTickerClassification(item);
  if (primaryFilter !== "all" && classification.primaryClass !== primaryFilter) return false;
  if (subclassFilter !== "all" && classification.stockSubclass !== subclassFilter) return false;
  return true;
}

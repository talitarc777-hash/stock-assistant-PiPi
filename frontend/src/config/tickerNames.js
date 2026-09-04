// English-only pre-load fallbacks for common US examples. HK names are never
// mapped here: both HK English and Traditional Chinese names come from backend
// market-metadata providers.
export const TICKER_NAME_OVERRIDES = Object.freeze({
  VOO: { en: "Vanguard S&P 500 ETF" },
  SPY: { en: "SPDR S&P 500 ETF Trust" },
  QQQ: { en: "Invesco QQQ Trust" },
  DIA: { en: "SPDR Dow Jones Industrial Average ETF" },
  IWM: { en: "iShares Russell 2000 ETF" },
  AAPL: { en: "Apple Inc." },
  MSFT: { en: "Microsoft Corporation" },
  NVDA: { en: "NVIDIA Corporation" },
  AMZN: { en: "Amazon.com, Inc." },
  GOOGL: { en: "Alphabet Inc." },
  GOOG: { en: "Alphabet Inc." },
  META: { en: "Meta Platforms, Inc." },
  TSLA: { en: "Tesla, Inc." },
  "BRK-B": { en: "Berkshire Hathaway Inc." },
  JPM: { en: "JPMorgan Chase & Co." },
  XOM: { en: "Exxon Mobil Corporation" },
});

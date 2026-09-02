export function tickerDisplayName(data, ticker = data?.ticker) {
  const metadata = data?.metadata || {};
  const value = [
    data?.ticker_name,
    data?.company_name,
    data?.security_name,
    metadata.ticker_name,
    metadata.company_name,
    metadata.security_name,
  ].find((candidate) => String(candidate || "").trim());
  const name = String(value || "").trim();
  const symbol = String(ticker || "").trim();
  if (!name || name.toUpperCase() === symbol.toUpperCase()) return "";
  return name;
}

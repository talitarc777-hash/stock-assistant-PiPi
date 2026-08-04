import React, { useEffect, useMemo, useState } from "react";

import { fetchChartData, fetchLiveMarketSnapshot } from "../api";
import LineChart from "./LineChart";
import TickerClassificationTags from "./TickerClassificationTags";

const CHART_RANGES = {
  "3D": { period: "1mo", points: 3, en: "3 days", zh: "3 \u65e5" },
  "1W": { period: "1mo", points: 5, en: "1 week", zh: "1 \u9031" },
  "1M": { period: "1mo", points: 31, en: "1 month", zh: "1 \u500b\u6708" },
  "1Y": { period: "1y", points: 252, en: "1 year", zh: "1 \u5e74" },
};

function labelByMode(mode, en, zh) {
  if (mode === "zh") return zh;
  if (mode === "en") return en;
  return `${en} / ${zh}`;
}

function formatMoney(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "N/A";
  return numeric.toFixed(2);
}

function formatPercent(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "N/A";
  return `${numeric.toFixed(2)}%`;
}

function formatLargeNumber(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "N/A";
  return new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(numeric);
}

function businessSummaryText(profile, languageMode) {
  const en =
    profile?.business_summary
    || "A business description is not available from the market-data provider.";
  const zh =
    profile?.business_summary_zh
    || "\u5e02\u5834\u8cc7\u6599\u4f9b\u61c9\u5546\u672a\u63d0\u4f9b\u53ef\u7528\u7684\u4e2d\u6587\u516c\u53f8\u696d\u52d9\u63cf\u8ff0\u3002";
  return labelByMode(languageMode, en, zh);
}

export default function HoldingsTable({ languageMode, holdings = [] }) {
  const [selectedHolding, setSelectedHolding] = useState(null);
  const [companyProfile, setCompanyProfile] = useState(null);
  const [isLoadingProfile, setIsLoadingProfile] = useState(false);
  const [profileError, setProfileError] = useState("");
  const [chartRange, setChartRange] = useState("1M");
  const [chartCache, setChartCache] = useState({});
  const [isLoadingChart, setIsLoadingChart] = useState(false);
  const [chartError, setChartError] = useState("");

  useEffect(() => {
    if (!selectedHolding) return undefined;
    function closeOnEscape(event) {
      if (event.key === "Escape") setSelectedHolding(null);
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [selectedHolding]);

  async function openHoldingSummary(holding) {
    setSelectedHolding(holding);
    setCompanyProfile(null);
    setChartRange("1M");
    setChartCache({});
    setProfileError("");
    setChartError("");
    setIsLoadingProfile(true);
    setIsLoadingChart(true);

    const [profileResult, chartResult] = await Promise.allSettled([
      fetchLiveMarketSnapshot(holding.ticker),
      fetchChartData(holding.ticker, CHART_RANGES["1M"].period),
    ]);

    if (profileResult.status === "fulfilled") {
      setCompanyProfile(profileResult.value);
    } else {
      setProfileError(profileResult.reason?.message || "Company information could not be loaded.");
    }
    if (chartResult.status === "fulfilled") {
      setChartCache({ "1M": chartResult.value.series || [] });
    } else {
      setChartError(chartResult.reason?.message || "Price history could not be loaded.");
    }
    setIsLoadingProfile(false);
    setIsLoadingChart(false);
  }

  async function selectChartRange(rangeKey) {
    setChartRange(rangeKey);
    setChartError("");
    if (!selectedHolding || chartCache[rangeKey]) return;
    if (CHART_RANGES[rangeKey].period === "1mo" && chartCache["1M"]) {
      setChartCache((current) => ({
        ...current,
        [rangeKey]: current["1M"],
      }));
      return;
    }

    setIsLoadingChart(true);
    try {
      const payload = await fetchChartData(
        selectedHolding.ticker,
        CHART_RANGES[rangeKey].period
      );
      setChartCache((current) => ({
        ...current,
        [rangeKey]: payload.series || [],
      }));
    } catch (error) {
      setChartError(error.message || "Price history could not be loaded.");
    } finally {
      setIsLoadingChart(false);
    }
  }

  function closeSummary() {
    setSelectedHolding(null);
    setCompanyProfile(null);
    setProfileError("");
    setChartCache({});
    setChartError("");
  }

  const chartPoints = useMemo(() => {
    const source = chartCache[chartRange] || [];
    const pointLimit = CHART_RANGES[chartRange].points;
    return source.slice(Math.max(0, source.length - pointLimit));
  }, [chartCache, chartRange]);

  return (
    <section className="panel">
      <h3>{labelByMode(languageMode, "Current Holdings", "\u76ee\u524d\u6301\u5009")}</h3>
      <p className="helper-text">
        {labelByMode(
          languageMode,
          "Select a ticker to see what the company does and its current valuation.",
          "\u6309\u4e00\u4e0b\u80a1\u7968\u4ee3\u865f\uff0c\u67e5\u770b\u516c\u53f8\u696d\u52d9\u53ca\u76ee\u524d\u4f30\u503c\u3002"
        )}
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{labelByMode(languageMode, "Ticker", "\u80a1\u7968\u4ee3\u865f")}</th>
              <th>{labelByMode(languageMode, "Quantity", "\u6301\u6709\u6578\u91cf")}</th>
              <th>{labelByMode(languageMode, "Average Cost", "\u5e73\u5747\u6210\u672c")}</th>
              <th>{labelByMode(languageMode, "Current Price", "\u73fe\u50f9")}</th>
              <th>{labelByMode(languageMode, "Market Value", "\u5e02\u503c")}</th>
              <th>{labelByMode(languageMode, "Unrealized PnL", "\u672a\u5be6\u73fe\u76c8\u8667")}</th>
              <th>{labelByMode(languageMode, "Unrealized PnL %", "\u672a\u5be6\u73fe\u76c8\u8667\u7387")}</th>
            </tr>
          </thead>
          <tbody>
            {holdings.length ? (
              holdings.map((holding) => (
                <tr key={holding.ticker}>
                  <td>
                    <button
                      type="button"
                    className="holding-ticker-button"
                    onClick={() => openHoldingSummary(holding)}
                  >
                      <span className="ticker-identity">
                        <span className="ticker-symbol">{holding.ticker}</span>
                        <TickerClassificationTags
                          ticker={holding.ticker}
                          classification={holding}
                          languageMode={languageMode}
                          size="xs"
                        />
                      </span>
                    </button>
                  </td>
                  <td>{Number(holding.quantity || 0).toFixed(0)}</td>
                  <td>{formatMoney(holding.avg_entry_price)}</td>
                  <td>{formatMoney(holding.current_price)}</td>
                  <td>{formatMoney(holding.market_value)}</td>
                  <td>{formatMoney(holding.unrealized_pnl)}</td>
                  <td>{formatPercent(holding.unrealized_pnl_pct)}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={7}>
                  {labelByMode(languageMode, "No open holdings yet.", "\u76ee\u524d\u5c1a\u672a\u6709\u6301\u5009\u3002")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {selectedHolding ? (
        <div className="holding-modal-backdrop" role="presentation" onMouseDown={closeSummary}>
          <div
            className="holding-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="holding-modal-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="holding-modal-header">
              <div>
                <h3 id="holding-modal-title">
                  {companyProfile?.company_name || selectedHolding.ticker}
                </h3>
                <p className="ticker-identity">
                  <span className="ticker-symbol">{selectedHolding.ticker}</span>
                  <TickerClassificationTags
                    ticker={selectedHolding.ticker}
                    classification={companyProfile || selectedHolding}
                    languageMode={languageMode}
                  />
                </p>
              </div>
              <button
                type="button"
                className="holding-modal-close"
                onClick={closeSummary}
                aria-label={labelByMode(languageMode, "Close", "\u95dc\u9589")}
              >
                &times;
              </button>
            </div>

            {isLoadingProfile ? (
              <p>{labelByMode(languageMode, "Loading company summary...", "\u6b63\u5728\u8f09\u5165\u516c\u53f8\u6458\u8981...")}</p>
            ) : null}

            {profileError ? <p className="holding-modal-error">{profileError}</p> : null}

            {!isLoadingProfile && companyProfile ? (
              <>
                <div className="holding-profile-grid">
                  <div>
                    <span>{labelByMode(languageMode, "Sector", "\u884c\u696d\u985e\u5225")}</span>
                    <strong>{companyProfile.sector || "N/A"}</strong>
                  </div>
                  <div>
                    <span>{labelByMode(languageMode, "Industry", "\u696d\u52d9\u884c\u696d")}</span>
                    <strong>{companyProfile.industry || "N/A"}</strong>
                  </div>
                  <div>
                    <span>{labelByMode(languageMode, "P/E ratio", "\u5e02\u76c8\u7387")}</span>
                    <strong>{formatMoney(companyProfile.pe_ratio)}</strong>
                  </div>
                  <div>
                    <span>{labelByMode(languageMode, "Market cap", "\u5e02\u503c")}</span>
                    <strong>{formatLargeNumber(companyProfile.market_cap)}</strong>
                  </div>
                  <div>
                    <span>{labelByMode(languageMode, "Current price", "\u73fe\u50f9")}</span>
                    <strong>{formatMoney(companyProfile.close)}</strong>
                  </div>
                  <div>
                    <span>{labelByMode(languageMode, "Daily change", "\u55ae\u65e5\u8b8a\u52d5")}</span>
                    <strong>{formatPercent(companyProfile.daily_change_pct)}</strong>
                  </div>
                </div>

                <div className="holding-chart-section">
                  <div className="holding-chart-heading">
                    <h4>{labelByMode(languageMode, "Price history", "\u904e\u5f80\u50f9\u683c")}</h4>
                    <div className="holding-chart-tabs" role="tablist">
                      {Object.entries(CHART_RANGES).map(([key, option]) => (
                        <button
                          key={key}
                          type="button"
                          role="tab"
                          aria-selected={chartRange === key}
                          className={chartRange === key ? "active" : ""}
                          onClick={() => selectChartRange(key)}
                        >
                          {labelByMode(languageMode, option.en, option.zh)}
                        </button>
                      ))}
                    </div>
                  </div>

                  {isLoadingChart ? (
                    <p>{labelByMode(languageMode, "Loading price history...", "\u6b63\u5728\u8f09\u5165\u904e\u5f80\u50f9\u683c...")}</p>
                  ) : null}
                  {chartError ? <p className="holding-modal-error">{chartError}</p> : null}
                  {!isLoadingChart && !chartError ? (
                    <LineChart
                      title=""
                      subtitle=""
                      points={chartPoints}
                      lines={[
                        {
                          key: "close",
                          label: labelByMode(languageMode, "Closing price", "\u6536\u5e02\u50f9"),
                          color: "#2563eb",
                          strokeWidth: 2.5,
                          valueKind: "price",
                        },
                      ]}
                      height={230}
                      xAxisLabel={labelByMode(languageMode, "Date", "\u65e5\u671f")}
                      yAxisLabel={labelByMode(languageMode, "Price", "\u50f9\u683c")}
                      yValueKind="price"
                      noDataMessage={labelByMode(languageMode, "No price data available.", "\u6c92\u6709\u53ef\u7528\u50f9\u683c\u8cc7\u6599\u3002")}
                      showRangeSelector={false}
                    />
                  ) : null}
                </div>

                <div className="holding-business-summary">
                  <h4>{labelByMode(languageMode, "What the company does", "\u516c\u53f8\u696d\u52d9\u6027\u8cea")}</h4>
                  <p>{businessSummaryText(companyProfile, languageMode)}</p>
                </div>

                <p className="helper-text">
                  {labelByMode(
                    languageMode,
                    "Market information may be delayed and is provided for simulation context.",
                    "\u5e02\u5834\u8cc7\u6599\u53ef\u80fd\u5ef6\u9072\uff0c\u50c5\u4f5c\u6a21\u64ec\u4ea4\u6613\u53c3\u8003\u3002"
                  )}
                </p>
              </>
            ) : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}

import React, { useEffect, useMemo, useState } from "react";

import { fetchChartData, fetchLiveMarketSnapshot } from "../api";
import LineChart from "./LineChart";
import TickerIdentity from "./TickerIdentity";

const PRICE_HISTORY_RANGES = {
  "5D": { period: "5d", points: 5, en: "5 days", zh: "5 日" },
  "1W": { period: "7d", points: 7, en: "1 week", zh: "1 週" },
  "1M": { period: "1mo", points: 31, en: "1 month", zh: "1 個月" },
  "3M": { period: "3mo", points: 63, en: "3 months", zh: "3 個月" },
  "6M": { period: "6mo", points: 126, en: "6 months", zh: "6 個月" },
  "1Y": { period: "1y", points: 252, en: "1 year", zh: "1 年" },
};

function labelByMode(mode, en, zh) {
  if (mode === "zh") return zh;
  if (mode === "en") return en;
  return `${en} / ${zh}`;
}

function firstSentence(value) {
  const text = String(value || "").trim().replace(/\s+/g, " ");
  if (!text) return "";
  const match = text.match(/^.*?(?:[.!?。！？](?:\s|$)|$)/);
  return (match?.[0] || text).trim();
}

function displayTickerName(profile, classification, ticker) {
  return (
    profile?.company_name
    || profile?.security_name
    || classification?.company_name
    || classification?.security_name
    || ticker
  );
}

function classificationNature(profile, classification, languageMode) {
  const companyName = displayTickerName(profile, classification, classification?.ticker || "This asset");
  const englishSummary = firstSentence(profile?.business_summary);
  const chineseSummary = firstSentence(profile?.business_summary_zh);
  const sector = profile?.sector || profile?.industry;
  const englishFallback = sector
    ? `${companyName} is a ${sector} company.`
    : "The market-data provider has not supplied a company description for this asset.";
  const chineseFallback = sector
    ? `${companyName} 屬於${sector}類別公司。`
    : "市場資料供應商尚未提供這項資產的公司描述。";
  const english = englishSummary || englishFallback;
  const chinese = chineseSummary || chineseFallback;
  return labelByMode(languageMode, english, chinese);
}

function formatPercent(value) {
  if (!Number.isFinite(value)) return "N/A";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export default function TickerHistorySummary({
  ticker,
  classification = null,
  languageMode = "both",
  market = "US",
}) {
  const [profile, setProfile] = useState(null);
  const [chartCache, setChartCache] = useState({});
  const [activeRange, setActiveRange] = useState("5D");
  const [isLoadingProfile, setIsLoadingProfile] = useState(false);
  const [isLoadingChart, setIsLoadingChart] = useState(false);
  const [profileError, setProfileError] = useState("");
  const [chartError, setChartError] = useState("");

  useEffect(() => {
    if (!ticker) return undefined;
    let cancelled = false;
    setProfile(null);
    setChartCache({});
    setActiveRange("5D");
    setProfileError("");
    setChartError("");
    setIsLoadingProfile(true);
    setIsLoadingChart(true);

    Promise.allSettled([
      fetchLiveMarketSnapshot(ticker, "3mo", market),
      fetchChartData(ticker, PRICE_HISTORY_RANGES["5D"].period, market),
    ]).then(([profileResult, chartResult]) => {
      if (cancelled) return;
      if (profileResult.status === "fulfilled") {
        setProfile(profileResult.value);
      } else {
        setProfileError(profileResult.reason?.message || "Company information could not be loaded.");
      }
      if (chartResult.status === "fulfilled") {
        setChartCache({ "5D": chartResult.value.series || [] });
      } else {
        setChartError(chartResult.reason?.message || "Price history could not be loaded.");
      }
      setIsLoadingProfile(false);
      setIsLoadingChart(false);
    });

    return () => {
      cancelled = true;
    };
  }, [market, ticker]);

  async function selectRange(rangeKey) {
    setActiveRange(rangeKey);
    setChartError("");
    if (chartCache[rangeKey]) return;
    setIsLoadingChart(true);
    try {
      const payload = await fetchChartData(
        ticker,
        PRICE_HISTORY_RANGES[rangeKey].period,
        market
      );
      setChartCache((current) => ({ ...current, [rangeKey]: payload.series || [] }));
    } catch (error) {
      setChartError(error.message || "Price history could not be loaded.");
    } finally {
      setIsLoadingChart(false);
    }
  }

  const chartPoints = useMemo(() => {
    const source = chartCache[activeRange] || [];
    const pointLimit = PRICE_HISTORY_RANGES[activeRange].points;
    return source.slice(Math.max(0, source.length - pointLimit)).map((point) => ({
      date: point.date,
      close: Number.isFinite(Number(point.close)) ? Number(point.close) : Number.NaN,
    }));
  }, [activeRange, chartCache]);

  const trendSummary = useMemo(() => {
    const closes = chartPoints.map((point) => point.close).filter(Number.isFinite);
    if (closes.length < 2) return null;
    const first = closes[0];
    const latest = closes[closes.length - 1];
    return {
      change: latest - first,
      changePct: first ? ((latest - first) / first) * 100 : Number.NaN,
    };
  }, [chartPoints]);

  if (!ticker) return null;

  return (
    <section className="ticker-history-summary">
      <div className="ticker-nature-summary">
        <h4>{labelByMode(languageMode, "What this company does", "公司業務性質")}</h4>
        <p className="ticker-nature-identity">
          <TickerIdentity
            ticker={ticker}
            data={profile || classification || { ticker }}
            languageMode={languageMode}
          />
        </p>
        {isLoadingProfile ? (
          <p className="helper-text">{labelByMode(languageMode, "Loading company description...", "正在載入公司描述...")}</p>
        ) : null}
        {profileError ? <p className="holding-modal-error">{profileError}</p> : null}
        {!isLoadingProfile ? (
          <p>{classificationNature(profile, classification || { ticker }, languageMode)}</p>
        ) : null}
      </div>

      <div className="ticker-history-heading">
        <h4>{labelByMode(languageMode, "Price history", "過往價格")}</h4>
        <div className="ticker-history-tabs" role="tablist" aria-label="Price history range">
          {Object.entries(PRICE_HISTORY_RANGES).map(([key, option]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={activeRange === key}
              className={activeRange === key ? "active" : ""}
              onClick={() => selectRange(key)}
            >
              {labelByMode(languageMode, option.en, option.zh)}
            </button>
          ))}
        </div>
      </div>

      {trendSummary ? (
        <p className={trendSummary.change >= 0 ? "ticker-trend-up" : "ticker-trend-down"}>
          {labelByMode(
            languageMode,
            `${PRICE_HISTORY_RANGES[activeRange].en}: ${trendSummary.change >= 0 ? "up" : "down"} ${formatPercent(Math.abs(trendSummary.changePct))}.`,
            `${PRICE_HISTORY_RANGES[activeRange].zh}：${trendSummary.change >= 0 ? "上升" : "下跌"} ${formatPercent(Math.abs(trendSummary.changePct))}。`
          )}
        </p>
      ) : null}

      {isLoadingChart ? (
        <p className="helper-text">{labelByMode(languageMode, "Loading price history...", "正在載入過往價格...")}</p>
      ) : null}
      {chartError ? <p className="holding-modal-error">{chartError}</p> : null}
      {!isLoadingChart && !chartError ? (
        <LineChart
          title=""
          subtitle={`${ticker} | ${PRICE_HISTORY_RANGES[activeRange].en}`}
          points={chartPoints}
          lines={[{
            key: "close",
            label: labelByMode(languageMode, "Closing price", "收市價"),
            color: "#2563eb",
            strokeWidth: 2.5,
            valueKind: "price",
          }]}
          height={220}
          xAxisLabel={labelByMode(languageMode, "Date", "日期")}
          yAxisLabel={labelByMode(languageMode, "Price", "價格")}
          yValueKind="price"
          noDataMessage={labelByMode(languageMode, "No price data available.", "沒有可用價格資料。")}
          showRangeSelector={false}
        />
      ) : null}
    </section>
  );
}

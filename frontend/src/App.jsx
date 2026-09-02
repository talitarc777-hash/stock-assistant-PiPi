import React, { useEffect, useMemo, useRef, useState } from "react";

import {
  fetchAnalyze,
  fetchChartData,
  fetchDashboardTopScores,
  fetchForecast,
  fetchLiveVirtualTraderTrades,
  fetchWatchlistAnalyze,
} from "./api";
import LineChart from "./components/LineChart";
import PriceChart from "./components/PriceChart";
import TickerIdentity from "./components/TickerIdentity";
import TopScoredTickersTable from "./components/TopScoredTickersTable";
import TickerHistorySummary from "./components/TickerHistorySummary";
import WatchlistManager from "./components/WatchlistManager";
import WatchlistTable from "./components/WatchlistTable";
import GlossaryPage from "./pages/GlossaryPage";
import ModelLifecyclePage from "./pages/ModelLifecyclePage";
import SettingsPage from "./pages/SettingsPage";
import VirtualTraderPage from "./pages/VirtualTraderPage";
import {
  fetchUserAlertScan,
  fetchUserProfile,
  fetchUserWatchlist,
  updateUserProfileSettings,
} from "./services/userProfileApi";
import {
  getStoredProfileId,
  normalizeProfileId,
  setStoredProfileId,
} from "./services/profileStorage";
import { rankTopScoredTickersByMarket } from "./utils/topScoredTickers";
import { tickerDisplayName } from "./utils/tickerIdentity";
import "./styles.css";

const DEFAULT_PERIOD = "5y";
const DASHBOARD_PATH = "/";
const GLOSSARY_PATH = "/glossary";
const MODEL_LIFECYCLE_PATH = "/model-lifecycle";
const SETTINGS_PATH = "/settings";
const VIRTUAL_TRADER_PATH = "/virtual-trader";
const SHARED_PROFILE_REFRESH_MS = 5000;
const LANGUAGE_STORAGE_KEY = "stock-assistant-language-mode";

const ZH = {
  currentAlerts: "\u76ee\u524d\u63d0\u793a",
  noAlerts: "\u76ee\u524d\u6c92\u6709\u65b0\u7684\u63d0\u793a\u3002",
  loading: "\u8f09\u5165\u4e2d...",
  suppressedPrefix: "\u5df2\u7565\u904e",
  suppressedSuffix: "\u500b\u91cd\u8907\u63d0\u793a\u3002",
  dashboard: "\u5100\u8868\u677f",
  dashboardIntro: "\u7531 FastAPI \u5f8c\u7aef\u63d0\u4f9b\u7684\u5171\u7528\u500b\u4eba\u8a2d\u5b9a\u6aa2\u8996\u3002",
  ticker: "\u80a1\u7968\u4ee3\u865f",
  refresh: "\u91cd\u65b0\u6574\u7406",
  tickerDetail: "\u80a1\u7968\u8a73\u60c5",
  latestClose: "\u6700\u65b0\u6536\u5e02\u50f9",
  score: "\u8a55\u5206",
  label: "\u6a19\u7c64",
  actionSummary: "\u64cd\u4f5c\u6458\u8981",
  benchmarkStrength: "\u57fa\u6e96\u76f8\u5c0d\u5f37\u5ea6",
  explanation: "\u89e3\u91cb",
  forecast: "\u5c55\u671b",
  scenarioOnly: "\u53ea\u5c6c\u60c5\u666f\u5206\u6790\uff0c\u4e26\u975e\u4fdd\u8b49\u9810\u6e2c\u3002",
  trendRegime: "\u8da8\u52e2\u72c0\u614b",
  outlook5d: "5 \u65e5\u5c55\u671b",
  outlook20d: "20 \u65e5\u5c55\u671b",
  expectedRange: "\u9810\u671f\u5340\u9593",
  support: "\u652f\u6490\u4f4d",
  resistance: "\u963b\u529b\u4f4d",
  confidenceScore: "\u4fe1\u5fc3\u8a55\u5206",
  priceAndSma: "\u50f9\u683c\u8207 SMA",
  close: "\u6536\u5e02\u50f9",
  scoreOverTime: "\u8a55\u5206\u8d70\u52e2",
  totalScore: "\u7e3d\u8a55\u5206",
  settings: "\u8a2d\u5b9a",
  glossary: "\u8a5e\u5f59\u8868",
  modelLifecycle: "\u4ea4\u6613\u6a21\u578b",
  virtualTrader: "\u865b\u64ec\u4ea4\u6613\u54e1",
  language: "\u8a9e\u8a00",
  chinese: "\u4e2d\u6587",
};

function normalizePath(pathname) {
  if (pathname === GLOSSARY_PATH) return GLOSSARY_PATH;
  if (pathname === "/model-evaluation") return MODEL_LIFECYCLE_PATH;
  if (pathname === MODEL_LIFECYCLE_PATH) return MODEL_LIFECYCLE_PATH;
  if (pathname === SETTINGS_PATH) return SETTINGS_PATH;
  if (pathname === VIRTUAL_TRADER_PATH) return VIRTUAL_TRADER_PATH;
  return DASHBOARD_PATH;
}

function getInitialLanguageMode() {
  const saved = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
  return saved === "en" || saved === "zh" || saved === "both" ? saved : "both";
}

function profileLanguageToMode(language) {
  if (language === "en" || language === "zh") return language;
  return "both";
}

function modeToProfileLanguage(mode) {
  if (mode === "en" || mode === "zh") return mode;
  return "bilingual";
}

function toNumeric(value) {
  if (value === null || value === undefined) return Number.NaN;
  const num = Number(value);
  return Number.isFinite(num) ? num : Number.NaN;
}

function navigateTo(path, setRoutePath) {
  const normalized = normalizePath(path);
  if (window.location.pathname !== normalized) {
    window.history.pushState({}, "", normalized);
  }
  setRoutePath(normalized);
}

function getActionSummaryByMode(analyzeData, mode) {
  if (!analyzeData) return "";
  if (mode === "zh") return analyzeData.action_summary_zh || analyzeData.action_summary;
  if (mode === "both") return analyzeData.action_summary_bilingual || analyzeData.action_summary;
  return analyzeData.action_summary_en || analyzeData.action_summary;
}

function getExplanationBulletsByMode(analyzeData, mode) {
  if (!analyzeData) return [];
  if (mode === "zh") return analyzeData.explanation_bullets_zh || analyzeData.explanation_bullets || [];
  if (mode === "both") {
    return analyzeData.explanation_bullets_bilingual || analyzeData.explanation_bullets || [];
  }
  return analyzeData.explanation_bullets_en || analyzeData.explanation_bullets || [];
}

function formatBilingualLabel(mode, en, zh) {
  if (mode === "zh") return zh;
  if (mode === "en") return en;
  return `${en} / ${zh}`;
}

function CurrentAlertsPanel({ languageMode, alertScan, isLoading }) {
  const title = formatBilingualLabel(languageMode, "Current Alerts", ZH.currentAlerts);
  const noAlerts = formatBilingualLabel(languageMode, "No new alerts right now.", ZH.noAlerts);

  function formatAlertMessage(item) {
    if (languageMode === "zh") return item.message_zh;
    if (languageMode === "both") return `${item.message_en} / ${item.message_zh}`;
    return item.message_en;
  }

  return (
    <section className="panel">
      <h3>{title}</h3>
      {isLoading ? <p>{formatBilingualLabel(languageMode, "Loading...", ZH.loading)}</p> : null}
      {!isLoading && (!alertScan || !alertScan.alerts.length) ? <p>{noAlerts}</p> : null}
      {!isLoading && alertScan?.alerts?.length ? (
        <ul className="bullet-list">
          {alertScan.alerts.map((item) => (
            <li key={`${item.ticker}-${item.rule}`}>{formatAlertMessage(item)}</li>
          ))}
        </ul>
      ) : null}
      {!isLoading && alertScan?.suppressed_count ? (
        <p className="helper-text">
          {formatBilingualLabel(
            languageMode,
            `${alertScan.suppressed_count} repeated alerts were suppressed.`,
            `${ZH.suppressedPrefix} ${alertScan.suppressed_count} ${ZH.suppressedSuffix}`
          )}
        </p>
      ) : null}
    </section>
  );
}

function DashboardPage({ languageMode, profileId, currentWatchlist, onProfileUpdated }) {
  const [watchlistRows, setWatchlistRows] = useState([]);
  const [marketDecisionRows, setMarketDecisionRows] = useState({ US: [], HK: [] });
  const [marketScoreRows, setMarketScoreRows] = useState({ US: [], HK: [] });
  const [selectedTicker, setSelectedTicker] = useState("");
  const [selectedMarket, setSelectedMarket] = useState("US");
  const [analyzeData, setAnalyzeData] = useState(null);
  const [chartData, setChartData] = useState(null);
  const [forecastData, setForecastData] = useState(null);
  const [detailLoadState, setDetailLoadState] = useState("idle");
  const [alertScan, setAlertScan] = useState(null);
  const [isLoadingWatchlist, setIsLoadingWatchlist] = useState(false);
  const [isLoadingTopScores, setIsLoadingTopScores] = useState(false);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [isLoadingAlerts, setIsLoadingAlerts] = useState(false);
  const [error, setError] = useState("");
  const [topScoresError, setTopScoresError] = useState({ US: "", HK: "" });
  const tickerDetailRef = useRef(null);

  async function loadWatchlist() {
    if (!currentWatchlist.length) {
      setWatchlistRows([]);
      if (selectedMarket === "US") setSelectedTicker("");
      return;
    }

    setIsLoadingWatchlist(true);
    setError("");
    try {
      const response = await fetchWatchlistAnalyze(currentWatchlist, DEFAULT_PERIOD);
      const rankedRows = response.ranked_results || [];
      const failedRows = response.failed_tickers || [];
      setWatchlistRows(rankedRows);
      if (rankedRows.length === 0 && failedRows.length > 0) {
        const firstFailure = failedRows[0];
        setError(
          `Watchlist analysis returned no valid rows. Example failure: ${firstFailure.ticker} - ${firstFailure.error}`
        );
      }
      if (rankedRows.length > 0) {
        const tickers = rankedRows.map((row) => row.ticker);
        setSelectedTicker((currentTicker) => {
          if (selectedMarket === "HK") return currentTicker;
          return tickers.includes(currentTicker) ? currentTicker : tickers[0];
        });
      } else {
        // Keep detail selection anchored to shared watchlist even when ranking fails.
        setSelectedTicker((currentTicker) => {
          if (selectedMarket === "HK") return currentTicker;
          return currentWatchlist.includes(currentTicker) ? currentTicker : (currentWatchlist[0] || "");
        });
      }
    } catch (requestError) {
      setError(requestError.message || "Failed to load watchlist.");
    } finally {
      setIsLoadingWatchlist(false);
    }
  }

  async function loadTickerDetail(ticker, market = selectedMarket) {
    if (!ticker) {
      setAnalyzeData(null);
      setChartData(null);
      setForecastData(null);
      setDetailLoadState("idle");
      setIsLoadingDetail(false);
      return;
    }
    // Always clear previous ticker detail first so partial failures never show stale mixed state.
    setAnalyzeData(null);
    setChartData(null);
    setForecastData(null);
    setDetailLoadState("loading");
    setIsLoadingDetail(true);
    setError("");
    try {
      const [analysisResult, chartResult, forecastResult] = await Promise.allSettled([
        fetchAnalyze(ticker, DEFAULT_PERIOD, market),
        fetchChartData(ticker, DEFAULT_PERIOD, market),
        fetchForecast(ticker, "2y", market),
      ]);
      setAnalyzeData(analysisResult.status === "fulfilled" ? analysisResult.value : null);
      setChartData(chartResult.status === "fulfilled" ? chartResult.value : null);
      setForecastData(forecastResult.status === "fulfilled" ? forecastResult.value : null);
      const failedCount = [analysisResult, chartResult, forecastResult].filter(
        (item) => item.status === "rejected"
      ).length;
      if (failedCount > 0) {
        setDetailLoadState(failedCount === 3 ? "failed" : "partial");
        setError(
          failedCount === 3
            ? "We could not load this ticker right now. Please retry in a moment."
            : "Some sections are still unavailable, but the rest of the page is ready."
        );
      } else {
        setDetailLoadState("ready");
      }
    } catch (requestError) {
      setAnalyzeData(null);
      setChartData(null);
      setForecastData(null);
      setDetailLoadState("failed");
      setError(requestError.message || "Failed to load ticker detail.");
    } finally {
      setIsLoadingDetail(false);
    }
  }

  async function loadTopScores() {
    setIsLoadingTopScores(true);
    setTopScoresError({ US: "", HK: "" });
    const results = await Promise.allSettled(
      [
        fetchLiveVirtualTraderTrades(profileId, null, 200, "US"),
        fetchDashboardTopScores(profileId, "HK", "all", DEFAULT_PERIOD, 200),
      ]
    );
    const nextRows = { US: [], HK: [] };
    const nextScoreRows = { US: [], HK: [] };
    const nextErrors = { US: "", HK: "" };
    results.forEach((result, index) => {
      const market = index === 0 ? "US" : "HK";
      if (result.status === "fulfilled") {
        if (market === "US") {
          nextRows.US = result.value.trades || [];
        } else {
          nextScoreRows.HK = result.value.rows || [];
          const skipped = result.value.diagnostics?.skipped || [];
          if (skipped.length) {
            const first = skipped[0];
            nextErrors.HK = formatBilingualLabel(
              languageMode,
              `${skipped.length} active HK ticker(s) could not be scored. ${first.ticker}: ${first.reason}`,
              `${skipped.length} 個啟用中的港股暫時未能評分。${first.ticker}: ${first.reason}`
            );
          }
        }
        return;
      }
      nextErrors[market] = formatBilingualLabel(
        languageMode,
        `The latest ${market} market scan is unavailable${market === "US" ? "; current watchlist scores are shown instead" : ""}.`,
        `暫時無法載入最新${market === "US" ? "美股" : "港股"}市場掃描${market === "US" ? "；現改為顯示目前觀察清單評分" : ""}。`
      );
    });
    setMarketDecisionRows(nextRows);
    setMarketScoreRows(nextScoreRows);
    setTopScoresError(nextErrors);
    setIsLoadingTopScores(false);
  }

  async function loadAlerts() {
    setIsLoadingAlerts(true);
    try {
      const payload = await fetchUserAlertScan(profileId);
      setAlertScan(payload);
    } catch {
      setAlertScan(null);
    } finally {
      setIsLoadingAlerts(false);
    }
  }

  useEffect(() => {
    loadWatchlist();
    loadTopScores();
    loadAlerts();
  }, [currentWatchlist.join(","), profileId]);

  useEffect(() => {
    loadTickerDetail(selectedTicker, selectedMarket);
  }, [selectedMarket, selectedTicker]);

  const chartSeries = useMemo(() => {
    if (!chartData?.series) return [];
    return chartData.series.map((point) => ({
      date: point.date,
      close: toNumeric(point.close),
      sma_20: toNumeric(point.sma_20),
      sma_50: toNumeric(point.sma_50),
      sma_200: toNumeric(point.sma_200),
      rsi_14: toNumeric(point.rsi_14),
      macd_line: toNumeric(point.macd_line),
      macd_signal: toNumeric(point.macd_signal),
    }));
  }, [chartData]);

  const scoreSeries = useMemo(() => {
    if (!chartData?.score_series) return [];
    return chartData.score_series.map((point) => ({
      date: point.date,
      total_score: toNumeric(point.total_score),
    }));
  }, [chartData]);

  const explanationBullets = useMemo(
    () => getExplanationBulletsByMode(analyzeData, languageMode),
    [analyzeData, languageMode]
  );

  const actionSummaryDisplay = useMemo(
    () => getActionSummaryByMode(analyzeData, languageMode),
    [analyzeData, languageMode]
  );

  const topScoredTickers = useMemo(
    () => rankTopScoredTickersByMarket(
      marketDecisionRows,
      { US: watchlistRows, HK: marketScoreRows.HK },
      200
    ),
    [marketDecisionRows, marketScoreRows, watchlistRows]
  );

  const selectedTickerData = useMemo(
    () => analyzeData
      || watchlistRows.find((row) => row.ticker === selectedTicker)
      || (topScoredTickers[selectedMarket] || []).find((row) => row.ticker === selectedTicker)
      || null,
    [analyzeData, selectedMarket, selectedTicker, topScoredTickers, watchlistRows]
  );

  function selectTopScoredTicker(ticker, market) {
    const normalizedTicker = String(ticker || "").trim().toUpperCase();
    setSelectedMarket(market === "HK" ? "HK" : "US");
    setSelectedTicker(normalizedTicker.replace(/\.HK$/i, ""));
    window.requestAnimationFrame(() => {
      tickerDetailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function selectWatchlistTicker(ticker) {
    setSelectedMarket("US");
    setSelectedTicker(ticker);
  }

  const watchlistClassificationByTicker = useMemo(
    () => Object.fromEntries(watchlistRows.map((item) => [item.ticker, item])),
    [watchlistRows]
  );

  return (
    <>
      <header className="app-header">
        <div>
          <h1>{formatBilingualLabel(languageMode, "Dashboard", ZH.dashboard)}</h1>
          <p>
            {formatBilingualLabel(
              languageMode,
              "Shared profile view powered by the FastAPI backend.",
              ZH.dashboardIntro
            )}
          </p>
        </div>
        <div className="header-controls">
          <label htmlFor="ticker-select">{formatBilingualLabel(languageMode, "Ticker", ZH.ticker)}</label>
          <select
            id="ticker-select"
            value={selectedTicker}
            onChange={(event) => selectWatchlistTicker(event.target.value)}
          >
            {selectedTicker && !watchlistRows.some((row) => row.ticker === selectedTicker) ? (
              <option value={selectedTicker}>
                {selectedTicker}{tickerDisplayName(selectedTickerData, selectedTicker) ? ` — ${tickerDisplayName(selectedTickerData, selectedTicker)}` : ""}
              </option>
            ) : null}
            {watchlistRows.map((row) => (
              <option key={row.ticker} value={row.ticker}>
                {row.ticker}{tickerDisplayName(row, row.ticker) ? ` — ${tickerDisplayName(row, row.ticker)}` : ""}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => Promise.all([loadWatchlist(), loadTopScores()])}
            disabled={isLoadingWatchlist || isLoadingTopScores}
          >
            {isLoadingWatchlist || isLoadingTopScores
              ? `${formatBilingualLabel(languageMode, "Refresh", ZH.refresh)}...`
              : formatBilingualLabel(languageMode, "Refresh", ZH.refresh)}
          </button>
        </div>
      </header>

      {error ? (
        <div className="error-box">
          <p>{error}</p>
          <button
            type="button"
            onClick={async () => {
              await loadWatchlist();
              await loadTopScores();
              await loadAlerts();
              await loadTickerDetail(selectedTicker, selectedMarket);
            }}
          >
            {formatBilingualLabel(languageMode, "Retry page data", "重新整理頁面資料")}
          </button>
        </div>
      ) : null}

      <TopScoredTickersTable
        rowsByMarket={topScoredTickers}
        languageMode={languageMode}
        isLoading={isLoadingWatchlist || isLoadingTopScores}
        errorsByMarket={topScoresError}
        onSelectTicker={selectTopScoredTicker}
      />

      <div className="layout-grid">
        <div>
          <WatchlistTable
            rows={watchlistRows}
            selectedTicker={selectedTicker}
            onSelectTicker={selectWatchlistTicker}
            languageMode={languageMode}
          />
          <WatchlistManager
            userId={profileId}
            watchlist={currentWatchlist}
            languageMode={languageMode}
            classificationByTicker={watchlistClassificationByTicker}
            onUpdated={() => onProfileUpdated(profileId)}
          />
          <CurrentAlertsPanel
            languageMode={languageMode}
            alertScan={alertScan}
            isLoading={isLoadingAlerts}
          />
        </div>

        <section className="panel" ref={tickerDetailRef}>
          <h3>{formatBilingualLabel(languageMode, "Ticker Detail", ZH.tickerDetail)}</h3>
          {detailLoadState === "partial" ? (
            <div className="helper-text">
              <p>
                {formatBilingualLabel(
                  languageMode,
                  "Some detail sections loaded, and some are still unavailable.",
                  "部分詳細資料已載入，但仍有部分區塊暫時無法顯示。"
                )}
              </p>
              <button type="button" onClick={() => loadTickerDetail(selectedTicker, selectedMarket)}>
                {formatBilingualLabel(languageMode, "Retry details", "重新整理詳細資料")}
              </button>
            </div>
          ) : detailLoadState === "failed" ? (
            <div className="helper-text">
              <p>
                {formatBilingualLabel(
                  languageMode,
                  "This section could not load right now. You can retry without leaving the page.",
                  "這個區塊目前無法載入，你可以直接在這裡重新整理，不用離開頁面。"
                )}
              </p>
              <button type="button" onClick={() => loadTickerDetail(selectedTicker, selectedMarket)}>
                {formatBilingualLabel(languageMode, "Retry details", "重新整理詳細資料")}
              </button>
            </div>
          ) : null}
          {!selectedTicker ? (
            <p>
              {formatBilingualLabel(
                languageMode,
                "No ticker selected. Add symbols to your watchlist.",
                "尚未選擇股票，請先在觀察清單新增股票。"
              )}
            </p>
          ) : isLoadingDetail ? (
            <p>
              {formatBilingualLabel(
                languageMode,
                "Loading ticker data. We will show each section as it becomes available.",
                "正在載入股票資料，完成的區塊會先顯示。"
              )}
            </p>
          ) : !analyzeData ? (
            <p>{error || "Failed to load ticker detail."}</p>
          ) : (
            <>
              <div className="detail-grid">
                <p>
                  <strong>{formatBilingualLabel(languageMode, "Ticker", ZH.ticker)}:</strong>{" "}
                  <TickerIdentity ticker={analyzeData.ticker} data={analyzeData} languageMode={languageMode} />
                </p>
                <p>
                  <strong>{formatBilingualLabel(languageMode, "Latest Close", ZH.latestClose)}:</strong>{" "}
                  {analyzeData.latest_close.toFixed(2)}
                </p>
                <p>
                  <strong>{formatBilingualLabel(languageMode, "Score", ZH.score)}:</strong>{" "}
                  {analyzeData.score_breakdown.total_score}
                </p>
                <p>
                  <strong>{formatBilingualLabel(languageMode, "Label", ZH.label)}:</strong> {analyzeData.label}
                </p>
                <p>
                  <strong>{formatBilingualLabel(languageMode, "Action Summary", ZH.actionSummary)}:</strong>{" "}
                  {actionSummaryDisplay}
                </p>
                <p>
                  <strong>{formatBilingualLabel(languageMode, "Benchmark Strength", ZH.benchmarkStrength)}:</strong>{" "}
                  {analyzeData.benchmark_relative?.benchmark_strength_score ?? "N/A"}
                </p>
              </div>
              <TickerHistorySummary
                ticker={analyzeData.ticker}
                classification={analyzeData}
                languageMode={languageMode}
                market={selectedMarket}
              />
              <h4>{formatBilingualLabel(languageMode, "Explanation", ZH.explanation)}</h4>
              <ul className="bullet-list">
                {explanationBullets.map((bullet) => (
                  <li key={bullet}>{bullet}</li>
                ))}
              </ul>
              <section className="forecast-card">
                <h4>{formatBilingualLabel(languageMode, "Forecast", ZH.forecast)}</h4>
                <p className="helper-text">
                  {formatBilingualLabel(
                    languageMode,
                    "Scenario-based forecast only.",
                    ZH.scenarioOnly
                  )}
                </p>
                {!forecastData ? (
                  <p>{formatBilingualLabel(languageMode, "Loading...", ZH.loading)}</p>
                ) : (
                  <div className="forecast-grid">
                    <p>
                      <strong>{formatBilingualLabel(languageMode, "Trend Regime", ZH.trendRegime)}:</strong>{" "}
                      {forecastData.trend_regime_en} / {forecastData.trend_regime_zh}
                    </p>
                    <p>
                      <strong>{formatBilingualLabel(languageMode, "5-Day Outlook", ZH.outlook5d)}:</strong>{" "}
                      {forecastData.outlook_5d}
                    </p>
                    <p>
                      <strong>{formatBilingualLabel(languageMode, "20-Day Outlook", ZH.outlook20d)}:</strong>{" "}
                      {forecastData.outlook_20d}
                    </p>
                    <p>
                      <strong>{formatBilingualLabel(languageMode, "Expected Range", ZH.expectedRange)}:</strong>{" "}
                      {forecastData.expected_range?.lower?.toFixed(2)} - {forecastData.expected_range?.upper?.toFixed(2)}
                    </p>
                    <p>
                      <strong>{formatBilingualLabel(languageMode, "Support", ZH.support)}:</strong>{" "}
                      {forecastData.levels?.support_level?.toFixed(2)}
                    </p>
                    <p>
                      <strong>{formatBilingualLabel(languageMode, "Resistance", ZH.resistance)}:</strong>{" "}
                      {forecastData.levels?.resistance_level?.toFixed(2)}
                    </p>
                    <p>
                      <strong>{formatBilingualLabel(languageMode, "Confidence Score", ZH.confidenceScore)}:</strong>{" "}
                      {forecastData.confidence_score}/100
                    </p>
                  </div>
                )}
              </section>
            </>
          )}
        </section>
      </div>

      <PriceChart
        ticker={selectedTicker || "N/A"}
        periodLabel="Last 6 Months"
        points={chartSeries}
        supportLevel={toNumeric(forecastData?.levels?.support_level)}
        resistanceLevel={toNumeric(forecastData?.levels?.resistance_level)}
        expectedRange={
          forecastData?.expected_range
            ? {
                lower: toNumeric(forecastData.expected_range.lower),
                upper: toNumeric(forecastData.expected_range.upper),
              }
            : null
        }
        languageMode={languageMode}
      />

      <div className="chart-grid">
        <LineChart
          title={formatBilingualLabel(languageMode, "Technical Indicators - RSI (14)", "技術指標 - RSI (14)")}
          subtitle={`Ticker: ${selectedTicker || "N/A"} | Last 6 Months`}
          points={chartSeries}
          xAxisLabel="Date"
          yAxisLabel="Score"
          yValueKind="score"
          lines={[{ key: "rsi_14", label: "RSI14", color: "#7c3aed", strokeWidth: 2.2, valueKind: "score" }]}
          noDataMessage="No data available"
          height={180}
        />
        <LineChart
          title={formatBilingualLabel(languageMode, "Technical Indicators - MACD", "技術指標 - MACD")}
          subtitle={`Ticker: ${selectedTicker || "N/A"} | Last 6 Months`}
          points={chartSeries}
          xAxisLabel="Date"
          yAxisLabel="Score"
          yValueKind="score"
          lines={[
            { key: "macd_line", label: "MACD", color: "#0f766e", strokeWidth: 2.1, valueKind: "score" },
            { key: "macd_signal", label: "Signal", color: "#dc2626", strokeWidth: 1.9, valueKind: "score" },
          ]}
          noDataMessage="No data available"
          height={180}
        />
      </div>

      <LineChart
        title={formatBilingualLabel(languageMode, "Score Over Time", ZH.scoreOverTime)}
        subtitle={`Ticker: ${selectedTicker || "N/A"} | Last 6 Months`}
        points={scoreSeries}
        xAxisLabel="Date"
        yAxisLabel="Score"
        yValueKind="score"
        lines={[
          {
            key: "total_score",
            label: formatBilingualLabel(languageMode, "Total Score", ZH.totalScore),
            color: "#374151",
            strokeWidth: 2.4,
            valueKind: "score",
          },
        ]}
        noDataMessage="No data available"
        height={180}
      />
    </>
  );
}

export default function App() {
  const [routePath, setRoutePath] = useState(() => normalizePath(window.location.pathname));
  const [profileId, setProfileId] = useState(getStoredProfileId);
  const [languageMode, setLanguageMode] = useState(getInitialLanguageMode);
  const [profile, setProfile] = useState(null);
  const [currentWatchlist, setCurrentWatchlist] = useState([]);
  const [profileError, setProfileError] = useState("");
  const sharedProfileRefreshInFlight = useRef(false);

  useEffect(() => {
    const onPopState = () => setRoutePath(normalizePath(window.location.pathname));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, languageMode);
  }, [languageMode]);

  useEffect(() => {
    setStoredProfileId(profileId);
  }, [profileId]);

  async function loadSharedProfile(
    nextProfileId = profileId,
    { quiet = false, source = "dashboard" } = {}
  ) {
    if (!quiet) setProfileError("");
    try {
      const [nextProfile, watchlistResponse] = await Promise.all([
        fetchUserProfile(nextProfileId, source),
        fetchUserWatchlist(nextProfileId),
      ]);
      setProfile((current) => (
        current?.updated_at === nextProfile.updated_at ? current : nextProfile
      ));
      const nextWatchlist = watchlistResponse.watchlist || [];
      setCurrentWatchlist((current) => (
        current.join(",") === nextWatchlist.join(",") ? current : nextWatchlist
      ));
      setLanguageMode(profileLanguageToMode(nextProfile.preferred_language));
    } catch (requestError) {
      if (!quiet) {
        setProfileError(requestError.message || "Failed to load shared profile.");
      }
    }
  }

  useEffect(() => {
    loadSharedProfile(profileId);
  }, [profileId]);

  useEffect(() => {
    if (!profileId) return undefined;
    const refreshWhenVisible = async () => {
      if (document.hidden || sharedProfileRefreshInFlight.current) return;
      sharedProfileRefreshInFlight.current = true;
      try {
        await loadSharedProfile(profileId, { quiet: true, source: null });
      } finally {
        sharedProfileRefreshInFlight.current = false;
      }
    };
    const timer = window.setInterval(refreshWhenVisible, SHARED_PROFILE_REFRESH_MS);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    window.addEventListener("focus", refreshWhenVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
      window.removeEventListener("focus", refreshWhenVisible);
    };
  }, [profileId]);

  async function handleProfileIdChange(nextProfileId) {
    const cleanId = normalizeProfileId(nextProfileId);
    setProfileId(cleanId);
  }

  async function handleLanguageChange(nextMode) {
    setLanguageMode(nextMode);
    try {
      await updateUserProfileSettings({
        user_id: profileId,
        preferred_language: modeToProfileLanguage(nextMode),
        last_active_source: "dashboard",
      });
      await loadSharedProfile(profileId);
    } catch (requestError) {
      setProfileError(requestError.message || "Failed to update language.");
    }
  }

  return (
    <main className="app-shell">
      <header className="panel global-header">
        <nav className="top-nav">
          <button
            type="button"
            className={routePath === DASHBOARD_PATH ? "nav-link active" : "nav-link"}
            onClick={() => navigateTo(DASHBOARD_PATH, setRoutePath)}
          >
            {formatBilingualLabel(languageMode, "Dashboard", ZH.dashboard)}
          </button>
          <button
            type="button"
            className={routePath === VIRTUAL_TRADER_PATH ? "nav-link active" : "nav-link"}
            onClick={() => navigateTo(VIRTUAL_TRADER_PATH, setRoutePath)}
          >
            {formatBilingualLabel(languageMode, "Virtual Trader", ZH.virtualTrader)}
          </button>
          <button
            type="button"
            className={routePath === MODEL_LIFECYCLE_PATH ? "nav-link active" : "nav-link"}
            onClick={() => navigateTo(MODEL_LIFECYCLE_PATH, setRoutePath)}
          >
            {formatBilingualLabel(languageMode, "Trading Models", ZH.modelLifecycle)}
          </button>
          <button
            type="button"
            className={routePath === SETTINGS_PATH ? "nav-link active" : "nav-link"}
            onClick={() => navigateTo(SETTINGS_PATH, setRoutePath)}
          >
            {formatBilingualLabel(languageMode, "Settings", ZH.settings)}
          </button>
          <button
            type="button"
            className={routePath === GLOSSARY_PATH ? "nav-link active" : "nav-link"}
            onClick={() => navigateTo(GLOSSARY_PATH, setRoutePath)}
          >
            {formatBilingualLabel(languageMode, "Glossary", ZH.glossary)}
          </button>
        </nav>
        <div className="header-controls">
          <label htmlFor="global-lang-select">{formatBilingualLabel(languageMode, "Language", ZH.language)}</label>
          <select
            id="global-lang-select"
            value={languageMode}
            onChange={(event) => handleLanguageChange(event.target.value)}
          >
            <option value="en">English</option>
            <option value="zh">{ZH.chinese}</option>
            <option value="both">English + {ZH.chinese}</option>
          </select>
        </div>
      </header>

      {profileError ? <p className="error-box">{profileError}</p> : null}

      {routePath === GLOSSARY_PATH ? (
        <GlossaryPage languageMode={languageMode} />
      ) : routePath === MODEL_LIFECYCLE_PATH ? (
        <ModelLifecyclePage languageMode={languageMode} />
      ) : routePath === SETTINGS_PATH ? (
        <SettingsPage
          profileId={profileId}
          onProfileIdChange={handleProfileIdChange}
          profile={profile}
          languageMode={languageMode}
          onProfileUpdated={loadSharedProfile}
          currentWatchlist={currentWatchlist}
        />
      ) : routePath === VIRTUAL_TRADER_PATH ? (
        <VirtualTraderPage
          languageMode={languageMode}
          currentWatchlist={currentWatchlist}
          profileId={profileId}
          onWatchlistSynced={setCurrentWatchlist}
        />
      ) : (
        <DashboardPage
          languageMode={languageMode}
          profileId={profileId}
          currentWatchlist={currentWatchlist}
          onProfileUpdated={loadSharedProfile}
        />
      )}
    </main>
  );
}

import React, { useEffect, useRef, useState } from "react";
import { fetchLearningHealth } from "../api";
import { learningReason, percentOrMissing } from "../utils/learningHealth";

export default function LearningHealthPanel({ languageMode }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const busy = useRef(false);
  const mounted = useRef(false);
  const t = (en, zh) => languageMode === "zh" ? zh : languageMode === "en" ? en : `${en} / ${zh}`;
  async function refresh() {
    if (busy.current) return;
    busy.current = true;
    setLoading(true);
    try {
      const value = await fetchLearningHealth();
      if (mounted.current) { setData(value); setError(false); }
    } catch { if (mounted.current) setError(true); }
    finally { busy.current = false; if (mounted.current) setLoading(false); }
  }
  useEffect(() => {
    mounted.current = true;
    refresh();
    const timer = setInterval(() => { if (!document.hidden) refresh(); }, 60000);
    return () => { mounted.current = false; clearInterval(timer); };
  }, []);
  return <section className="panel learning-panel" aria-label={t("Continuous learning operations", "持續學習運作狀況")}>
    <h3>{t("Continuous learning operations", "持續學習運作狀況")}</h3>
    <button type="button" onClick={refresh} disabled={loading}>{t("Refresh learning status", "更新學習狀態")}</button>
    {error && <p role="alert">{t("Status unavailable. Check backend connectivity and deployment. Previous data below may be stale.", "無法讀取狀態。請檢查後端連線及部署；以下舊資料可能已過期。")}</p>}
    {!data && !error && <p>{t("Loading…", "載入中…")}</p>}
    {data && <>
      <p><strong>{learningReason(data.status, languageMode)}</strong> · {t("Last check", "上次檢查")}: {data.checked_at_utc ? new Date(data.checked_at_utc).toLocaleString() : "N/A"}</p>
      <p>{t("Context models are research-only. No automatic promotion or forced trades. Status refreshes every minute while this page is visible.", "資訊模型僅供研究，不會自動升級或強制交易。此頁顯示時每分鐘更新狀態。")}</p>
      {data.blockers?.length > 0 && <ul>{data.blockers.map(b => <li key={b}>{learningReason(b, languageMode)}</li>)}</ul>}
      <div className="model-health-grid">
        {["US", "HK"].map(market => {
          const r = data.research?.readiness?.[market] || {};
          const plan = data.plans?.[market] || {};
          const quality = data.coverage?.quality?.[market] || {};
          const total = data.coverage?.markets?.find(v => v.market === market);
          return <article key={market}>
            <h4>{market}</h4>
            <p>{learningReason(r.waiting_reason, languageMode)}</p>
            <dl>
              <div><dt>{t("Archived observations (all time)", "累積資訊觀測")}</dt><dd>{total?.observations ?? 0}</dd></div>
              <div><dt>{t("Fresh snapshots (<24h)", "24小時內資訊")}</dt><dd>{quality.fresh_snapshots ?? 0}</dd></div>
              <div><dt>{t("Research snapshots", "研究樣本")}</dt><dd>{r.snapshot_counts?.observations ?? 0}</dd></div>
              <div><dt>{t("Matured training dates", "已到期訓練日期")}</dt><dd>{r.dates ?? 0} / {r.required_training_dates ?? 120}</dd></div>
              <div><dt>{t("Next fit eligible", "可啟動下次訓練")}</dt><dd>{r.next_training_eligible ? t("Yes", "是") : t("Not yet", "未符合")}</dd></div>
              <div><dt>{t("New matured dates", "新增已到期日期")}</dt><dd>{r.new_matured_dates ?? 0}</dd></div>
            </dl>
            <p>{t("Collection capacity", "收集容量")}: {plan.hourly_batch ?? 0}/{t("hour", "小時")} · {plan.universe_tickers ?? 0} {t("tickers", "股票")} · ~{plan.estimated_rotation_hours ?? 0}h {t("per rotation, excluding delays", "完成一輪，不含延誤")}</p>
            <p>{t("Research target: four tickers once per completed market day.", "研究目標：每個完整交易日收集四隻股票。")}</p>
            <p>{t("Dataset fingerprint", "資料指紋")}: <code className="learning-fingerprint">{r.dataset_fingerprint || t("Waiting for matured data", "等待到期資料")}</code></p>
            <p>{t("Changed inputs", "資料有變動")}: {r.new_information ? t("Yes (not necessarily eligible to refit)", "是（不代表符合重新訓練條件）") : t("No", "否")}</p>
            <details><summary>{t("Feature and ticker readiness", "特徵及股票準備狀態")}</summary>
              <p>{t("At least 80% overall / 50% per ticker coverage and three distinct values are required.", "需要整體80%、每隻股票50%覆蓋，以及至少三個不同數值。")}</p>
              {Object.entries(r.feature_coverage || {}).map(([name, value]) => <p key={name}>{name.replaceAll("_", " ")}: {percentOrMissing(value.coverage)} · {value.distinct_values} {t("distinct values", "不同數值")}</p>)}
              {Object.entries(r.ticker_dates || {}).map(([ticker, dates]) => <p key={ticker}>{ticker}: {dates}/90 {t("dates", "日期")}</p>)}
            </details>
          </article>;
        })}
      </div>
      <h4>{t("External source coverage", "外部來源覆蓋")}</h4>
      <p>{t("Usable counts describe the latest collection attempts, not provider uptime or predictive quality. Market/benchmark price features remain in the canonical price pipeline.", "可用數量指最近收集結果，不代表供應商正常率或預測品質。市場及基準價格特徵仍由原有價格流程提供。")}</p>
      <div className="table-wrap"><table><thead><tr>
        <th>{t("Source", "來源")}</th><th>{t("Configured", "已設定")}</th><th>US</th><th>HK</th><th>{t("Missing / unavailable reasons", "缺少或不可用原因")}</th>
      </tr></thead><tbody>{Object.entries(data.sources || {}).map(([source, config]) => {
        const jobs = data.collection || [];
        const reasons = [...new Set(jobs.map(j => j.sources?.[source]).filter(s => s && s !== "usable"))];
        return <tr key={source}><td>{source.replaceAll("_", " ")}</td><td>{config.configured ? t("Yes", "是") : t("No", "否")}</td>
          {["US", "HK"].map(m => <td key={m}>{config.markets?.includes(m) ? `${jobs.filter(j => j.market === m && j.sources?.[source] === "usable").length} / ${jobs.filter(j => j.market === m).length}` : t("Not supported", "不支援")}</td>)}
          <td>{reasons.map(s => learningReason(s, languageMode)).join("; ") || t("No attempts yet / no reported gaps", "尚未嘗試或未報告缺漏")}</td></tr>;
      })}</tbody></table></div>
      <h4>{t("Price-only vs context experiments", "純價格與加入資訊實驗")}</h4>
      <p>{t("Current generation", "目前研究版本")}: <code className="learning-fingerprint">{data.research?.protocol_id}</code> · {t("Preserved prior generations", "保留的舊研究版本")}: {data.research?.previous_protocols?.length ?? 0}</p>
      <p>{t("120 training dates + 120 forward dates remain required. Interim results every 20 matured dates are descriptive only: no tuning, early success claim or promotion. This is a monitored fixed-model test, not a blind holdout.", "仍要求120個訓練日期及120個前瞻日期。每20個到期日期的中期結果僅供觀察，不會調參、提早判定成功或升級。這是固定模型的受監測測試，並非盲測。")}</p>
      {!data.research?.rounds?.length && <p>{t("No experiment trained yet; collection and eligibility checks run automatically.", "尚未訓練實驗；收集及資格檢查會自動執行。")}</p>}
      <div className="table-wrap"><table><thead><tr><th>{t("Market / round", "市場／輪次")}</th><th>{t("State", "狀態")}</th><th>{t("Price-only dates / matured rows", "純價格日期／到期樣本")}</th><th>{t("Context dates / matured rows", "資訊日期／到期樣本")}</th><th>{t("Comparison", "比較")}</th></tr></thead>
        <tbody>{(data.research?.rounds || []).map(r => {
          const result = r.result_json?.metrics ? r.result_json : r.monitoring?.result;
          return <tr key={`${r.market}-${r.round_number}`}><td>{r.market} / {r.round_number}</td><td>{r.status}</td>
            {["price_only", "price_context"].map(a => <td key={a}>{r.arms?.[a]?.prediction_dates ?? 0}/{r.required_forward_dates} · {r.arms?.[a]?.matured ?? 0}</td>)}
            <td>{result ? <>{result.interim_only ? t("Provisional", "暫定") : t("Completed window", "完成期間")} · {t("Direction accuracy", "方向準確率")}: {percentOrMissing(result.metrics?.price_only?.direction_accuracy)} → {percentOrMissing(result.metrics?.price_context?.direction_accuracy)}<br />{t("Net return per opportunity (%)", "每次機會淨回報（%）")}: {result.metrics?.price_only?.net_return_per_opportunity_pct?.toFixed(2)} → {result.metrics?.price_context?.net_return_per_opportunity_pct?.toFixed(2)}</> : (r.result_json?.reason || t("Waiting for matched matured evidence", "等待配對到期證據"))}</td></tr>;
        })}</tbody></table></div>
      <details><summary>{t("Collection attempts and retraining decisions", "收集嘗試及重新訓練決定")}</summary>
        <p>{(data.training_admission || []).map(r => `${r.last_reason}: ${r.count}`).join(" · ") || t("No automatic fit attempts yet", "尚未有自動訓練嘗試")}</p>
        <div className="table-wrap learning-attempts"><table><thead><tr><th>{t("Ticker", "股票")}</th><th>{t("Status", "狀態")}</th><th>{t("Next attempt", "下次嘗試")}</th></tr></thead><tbody>{(data.collection || []).map(j => <tr key={`${j.market}-${j.ticker}`}><td>{j.market} {j.ticker}</td><td>{learningReason(j.status, languageMode)}</td><td>{j.next_attempt_utc ? new Date(j.next_attempt_utc).toLocaleString() : "N/A"}</td></tr>)}</tbody></table></div>
      </details>
    </>}
  </section>;
}

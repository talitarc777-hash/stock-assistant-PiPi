import React, { useEffect, useState } from "react";

import WatchlistManager from "../components/WatchlistManager";
import { labelByMode } from "../i18n/bilingualUiLabels";
import {
  createDiscordLinkCode,
  fetchDiscordReadiness,
  fetchDiscordLinkStatus,
  fetchUserAlertSettings,
  unlinkDiscordProfile,
  updateUserAlertSettings,
  updateUserProfileSettings,
} from "../services/userProfileApi";

function watchlistToText(values) {
  return (values || []).join(", ");
}

function textToWatchlist(value) {
  return value
    .replace(/\n/g, ",")
    .replace(/;/g, ",")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

const ZH = {
  settingsSaved: "\u8a2d\u5b9a\u5df2\u5132\u5b58\u3002",
  sharedProfile: "\u9019\u500b\u500b\u4eba\u8a2d\u5b9a\u6703\u8207 Discord \u6a5f\u68b0\u4eba\u5171\u7528\u3002",
  settings: "\u8a2d\u5b9a",
  profileId: "\u500b\u4eba\u8cc7\u6599 ID",
  language: "\u8a9e\u8a00",
  chinese: "\u4e2d\u6587",
  compact: "\u7cbe\u7c21\u6a21\u5f0f",
  alertsEnabled: "\u555f\u7528\u63d0\u793a",
  alertHigh: "\u9ad8\u4f4d\u63d0\u793a\u9580\u6abb",
  alertLow: "\u4f4e\u4f4d\u63d0\u793a\u9580\u6abb",
  alertWatchlist: "\u63d0\u793a\u89c0\u5bdf\u540d\u55ae",
  save: "\u5132\u5b58",
  profileIdHint:
    "\u5982\u679c\u60a8\u60f3\u5728\u4e0d\u540c\u88dd\u7f6e\u4e0a\u540c\u6b65\u8a2d\u5b9a\u8207\u89c0\u5bdf\u540d\u55ae\uff0c\u8acb\u4f7f\u7528\u76f8\u540c\u7684 Profile ID\u3002",
  alertWatchlistHint:
    "\u5982\u679c\u60a8\u60f3\u76f4\u63a5\u4f7f\u7528\u4e3b\u89c0\u5bdf\u540d\u55ae\u4f5c\u70ba\u63d0\u793a\u540d\u55ae\uff0c\u53ef\u4ee5\u4fdd\u6301\u7a7a\u767d\u3002",
};

export default function SettingsPage({
  profileId,
  onProfileIdChange,
  profile,
  languageMode,
  onProfileUpdated,
  currentWatchlist,
}) {
  const [localProfileId, setLocalProfileId] = useState(profileId);
  const [language, setLanguage] = useState("bilingual");
  const [compactMode, setCompactMode] = useState(false);
  const [alertEnabled, setAlertEnabled] = useState(true);
  const [alertHigh, setAlertHigh] = useState(80);
  const [alertLow, setAlertLow] = useState(45);
  const [alertWatchlist, setAlertWatchlist] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [remoteLoadError, setRemoteLoadError] = useState("");
  const [remoteRefreshToken, setRemoteRefreshToken] = useState(0);
  const [isSaving, setIsSaving] = useState(false);
  const [discordLink, setDiscordLink] = useState(null);
  const [discordReadiness, setDiscordReadiness] = useState(null);
  const [discordCode, setDiscordCode] = useState(null);
  const [discordLinkBusy, setDiscordLinkBusy] = useState(false);
  const [discordLinkError, setDiscordLinkError] = useState("");

  useEffect(() => {
    setLocalProfileId(profileId);
  }, [profileId, remoteRefreshToken]);

  useEffect(() => {
    if (!profile) return;
    setLanguage(profile.preferred_language || "bilingual");
    setCompactMode(Boolean(profile.compact_mode));
    setAlertEnabled(Boolean(profile.alert_enabled));
    setAlertHigh(profile.alert_threshold_high ?? 80);
    setAlertLow(profile.alert_threshold_low ?? 45);
    setAlertWatchlist(watchlistToText(profile.alert_watchlist || []));
  }, [profile]);

  useEffect(() => {
    let isActive = true;
    async function loadRemoteSettings() {
      setRemoteLoadError("");
      try {
        const alertSettings = await fetchUserAlertSettings(profileId);
        if (!isActive) return;
        setAlertEnabled(Boolean(alertSettings.alert_enabled));
        setAlertHigh(alertSettings.alert_threshold_high ?? 80);
        setAlertLow(alertSettings.alert_threshold_low ?? 45);
        setAlertWatchlist(watchlistToText(alertSettings.alert_watchlist || []));
      } catch (requestError) {
        // Keep the page usable even if the backend is temporarily busy.
        if (!isActive) return;
        setRemoteLoadError(requestError.message || "Some settings could not be loaded right now.");
      }
    }
    if (profileId) {
      loadRemoteSettings();
    }
    return () => {
      isActive = false;
    };
  }, [profileId]);

  useEffect(() => {
    let isActive = true;
    async function loadDiscordLink() {
      setDiscordLinkError("");
      setDiscordCode(null);
      const [linkResult, readinessResult] = await Promise.allSettled([
        fetchDiscordLinkStatus(profileId),
        fetchDiscordReadiness(),
      ]);
      if (!isActive) return;
      if (linkResult.status === "fulfilled") {
        setDiscordLink(linkResult.value);
      } else {
        setDiscordLinkError(linkResult.reason?.message || "Discord link status could not be loaded.");
      }
      if (readinessResult.status === "fulfilled") {
        setDiscordReadiness(readinessResult.value);
      }
    }
    if (profileId) loadDiscordLink();
    return () => {
      isActive = false;
    };
  }, [profileId]);

  async function handleCreateDiscordCode() {
    setDiscordLinkBusy(true);
    setDiscordLinkError("");
    try {
      setDiscordCode(await createDiscordLinkCode(profileId));
    } catch (requestError) {
      setDiscordLinkError(requestError.message || "Could not create a Discord link code.");
    } finally {
      setDiscordLinkBusy(false);
    }
  }

  async function handleUnlinkDiscord() {
    setDiscordLinkBusy(true);
    setDiscordLinkError("");
    try {
      const status = await unlinkDiscordProfile(profileId);
      setDiscordLink(status);
      setDiscordCode(null);
    } catch (requestError) {
      setDiscordLinkError(requestError.message || "Could not unlink Discord.");
    } finally {
      setDiscordLinkBusy(false);
    }
  }

  async function handleSave(event) {
    event.preventDefault();
    setIsSaving(true);
    setMessage("");
    setError("");
    try {
      const nextProfileId = localProfileId.trim() || profileId;
      if (nextProfileId !== profileId) {
        onProfileIdChange(nextProfileId);
      }

      await updateUserProfileSettings({
        user_id: nextProfileId,
        preferred_language: language,
        compact_mode: compactMode,
        last_active_source: "dashboard",
      });

      await updateUserAlertSettings({
        user_id: nextProfileId,
        alert_enabled: alertEnabled,
        alert_threshold_high: Number(alertHigh),
        alert_threshold_low: Number(alertLow),
        alert_watchlist: textToWatchlist(alertWatchlist),
        preferred_delivery_source: "discord",
        last_active_source: "dashboard",
      });

      await onProfileUpdated(nextProfileId);
      setMessage(labelByMode(languageMode, "Settings saved.", ZH.settingsSaved));
    } catch (requestError) {
      setError(requestError.message || "Failed to save settings.");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="settings-grid">
      <section className="panel">
        <h2>{labelByMode(languageMode, "Settings", ZH.settings)}</h2>
        <p className="helper-text">
          {labelByMode(
            languageMode,
            "The dashboard and Discord bot share this profile. Use the same Profile ID if you want both sides to stay in sync.",
            ZH.sharedProfile
          )}
        </p>
        {remoteLoadError ? (
          <div className="helper-text">
            <p>{remoteLoadError}</p>
            <button
              type="button"
              onClick={() => setRemoteRefreshToken((value) => value + 1)}
            >
              {labelByMode(languageMode, "Retry loading settings", "重新載入設定")}
            </button>
          </div>
        ) : null}
        <form className="settings-form" onSubmit={handleSave}>
          <label>
            {labelByMode(languageMode, "Profile ID", ZH.profileId)}
            <input
              type="text"
              value={localProfileId}
              onChange={(event) => setLocalProfileId(event.target.value)}
            />
          </label>
          <p className="helper-text">
            {labelByMode(
              languageMode,
              "Use the same Profile ID on each device if you want the same settings and watchlist everywhere.",
              ZH.profileIdHint
            )}
          </p>

          <label>
            {labelByMode(languageMode, "Language", ZH.language)}
            <select value={language} onChange={(event) => setLanguage(event.target.value)}>
              <option value="en">English</option>
              <option value="zh">{ZH.chinese}</option>
              <option value="bilingual">English + {ZH.chinese}</option>
            </select>
          </label>

          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={compactMode}
              onChange={(event) => setCompactMode(event.target.checked)}
            />
            {labelByMode(languageMode, "Compact mode", ZH.compact)}
          </label>

          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={alertEnabled}
              onChange={(event) => setAlertEnabled(event.target.checked)}
            />
            {labelByMode(languageMode, "Alerts enabled", ZH.alertsEnabled)}
          </label>
          <p className="helper-text">
            {labelByMode(
              languageMode,
              "Discord alerts include tickers meeting your high overall-score threshold, unusual real-market buying or selling pressure, and sudden price moves. The overall score is a 0-100 screening score, not a profit probability. Simulated Virtual Trader orders do not send notifications.",
              "Discord 提示包括達到整體高評分門檻的股票、真實市場的異常買盤或賣盤壓力，以及價格急變。整體評分是 0 至 100 的篩選分數，並非獲利機率。模擬交易工具的買賣不會發送通知。"
            )}
          </p>

          <label>
            {labelByMode(languageMode, "Alert threshold high", ZH.alertHigh)}
            <input
              type="number"
              min="0"
              max="100"
              value={alertHigh}
              onChange={(event) => setAlertHigh(event.target.value)}
            />
          </label>
          <p className="helper-text">
            {labelByMode(
              languageMode,
              "A Discord alert is sent when a watched ticker reaches this score (80 or above by default). It is limited to one alert per ticker, threshold, and market-data date.",
              "當觀察股票達到此評分時會發送 Discord 提示（預設為 80 分或以上）。每隻股票、每個門檻及每個市場數據日期最多發送一次。"
            )}
          </p>

          <label>
            {labelByMode(languageMode, "Alert threshold low", ZH.alertLow)}
            <input
              type="number"
              min="0"
              max="100"
              value={alertLow}
              onChange={(event) => setAlertLow(event.target.value)}
            />
          </label>

          <label>
            {labelByMode(languageMode, "Alert watchlist", ZH.alertWatchlist)}
            <textarea
              rows="3"
              value={alertWatchlist}
              onChange={(event) => setAlertWatchlist(event.target.value)}
              placeholder="TSLA, NVDA, BRK-B"
            />
          </label>
          <p className="helper-text">
            {labelByMode(
              languageMode,
              "Leave this blank if you want alerts to use your main watchlist.",
              ZH.alertWatchlistHint
            )}
          </p>

          <button type="submit" disabled={isSaving}>
            {isSaving ? "Saving..." : labelByMode(languageMode, "Save", ZH.save)}
          </button>
        </form>
        {message ? <p className="success-box">{message}</p> : null}
        {error ? <p className="error-box">{error}</p> : null}
      </section>

      <section className="panel discord-link-card">
        <div className="discord-link-heading">
          <div>
            <h2>{labelByMode(languageMode, "Connect Discord", "連接 Discord")}</h2>
            <p className="helper-text">
              {labelByMode(
                languageMode,
                "Link once so Discord commands use this web profile's virtual account, watchlist, settings, and alerts.",
                "連接一次後，Discord 指令會使用此網頁帳戶的模擬交易帳戶、觀察名單、設定及提示。"
              )}
            </p>
          </div>
          <span className={`discord-link-status ${discordLink?.linked ? "linked" : ""}`}>
            {discordLink?.linked
              ? labelByMode(languageMode, "Connected", "已連接")
              : labelByMode(languageMode, "Not connected", "未連接")}
          </span>
        </div>

        {discordReadiness ? (
          <div className={discordReadiness.fully_configured ? "success-box" : "warning-box"} role="status">
            <strong>
              {discordReadiness.fully_configured
                ? labelByMode(languageMode, "Discord service is ready", "Discord 服務已準備就緒")
                : labelByMode(languageMode, "Discord service needs administrator setup", "Discord 服務需要管理員設定")}
            </strong>
            <p>
              {labelByMode(languageMode, "Bot commands", "機械人指令")}: {discordReadiness.bot_commands_configured
                ? labelByMode(languageMode, "ready", "已準備")
                : labelByMode(languageMode, "not configured", "尚未設定")}
              {" · "}
              {labelByMode(languageMode, "Automatic alerts", "自動提示")}: {discordReadiness.proactive_alerts_configured
                ? labelByMode(languageMode, "ready", "已準備")
                : labelByMode(languageMode, "not configured", "尚未設定")}
            </p>
            <p>
              {labelByMode(languageMode, "Deployed command build", "已部署指令版本")}: {discordReadiness.bot_build_id || "unknown"}
              {" · "}
              <code>!link</code>: {discordReadiness.link_command_supported_by_build
                ? labelByMode(languageMode, "included", "已包含")
                : labelByMode(languageMode, "missing", "缺少")}
            </p>
            {!discordReadiness.fully_configured ? (
              <small>
                {labelByMode(
                  languageMode,
                  `Administrator: add ${discordReadiness.missing_environment_variables.join(" and ")} to the server's private .env file, then restart the affected service. Never paste these secrets into this page or Discord chat.`,
                  `管理員：請在伺服器的私人 .env 檔案加入 ${discordReadiness.missing_environment_variables.join(" 及 ")}，然後重新啟動相關服務。切勿將密鑰貼在此頁或 Discord 對話。`
                )}
              </small>
            ) : (
              <small>{labelByMode(languageMode, "You can now link an account and verify shared data with !syncstatus.", "現在可以連接帳戶，並使用 !syncstatus 核對共享資料。")}</small>
            )}
            {discordReadiness.supported_commands?.length ? (
              <small>
                {labelByMode(languageMode, "Supported by this build", "此版本支援")}: {discordReadiness.supported_commands.join(", ")}
              </small>
            ) : null}
          </div>
        ) : null}

        {discordLink?.linked ? (
          <>
            <p>
              {labelByMode(languageMode, "Discord account", "Discord 帳戶")}:{" "}
              <strong>{discordLink.discord_display_name || discordLink.discord_user_id}</strong>
            </p>
            <p className="helper-text">
              {labelByMode(
                languageMode,
                "Verify the shared web data anytime in Discord with !syncstatus.",
                "可隨時在 Discord 輸入 !syncstatus，核對網頁共享資料。"
              )}
            </p>
            <button type="button" className="secondary-button" onClick={handleUnlinkDiscord} disabled={discordLinkBusy}>
              {labelByMode(languageMode, "Disconnect Discord", "中斷 Discord 連接")}
            </button>
          </>
        ) : (
          <>
            <ol className="discord-link-steps">
              <li>{labelByMode(languageMode, "Generate a private one-time code below.", "在下方產生私人一次性代碼。")}</li>
              <li>{labelByMode(languageMode, "Send the shown command to the Discord bot within 10 minutes.", "在 10 分鐘內將顯示的指令傳送給 Discord 機械人。")}</li>
              <li>{labelByMode(languageMode, "Refresh this page to confirm the connection.", "重新載入此頁以確認連接。")}</li>
            </ol>
            <button type="button" onClick={handleCreateDiscordCode} disabled={discordLinkBusy}>
              {discordLinkBusy
                ? labelByMode(languageMode, "Generating...", "產生中...")
                : labelByMode(languageMode, "Generate link code", "產生連接代碼")}
            </button>
          </>
        )}

        {discordCode ? (
          <div className="discord-link-command" role="status">
            <span>{labelByMode(languageMode, "Send this command privately to the bot:", "將此指令私下傳送給機械人：")}</span>
            <code>!link {discordCode.code}</code>
            <small>{labelByMode(languageMode, "Expires in 10 minutes and works once.", "代碼在 10 分鐘後失效，而且只可使用一次。")}</small>
          </div>
        ) : null}
        {discordLinkError ? <p className="error-box">{discordLinkError}</p> : null}
      </section>

      <WatchlistManager
        userId={profileId}
        watchlist={currentWatchlist}
        languageMode={languageMode}
        onUpdated={() => onProfileUpdated(profileId)}
      />

    </div>
  );
}

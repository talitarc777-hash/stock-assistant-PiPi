import React, { useState } from "react";

import { postVirtualTradingActivityReset } from "../api";

function labelByMode(mode, en, zh) {
  if (mode === "zh") return zh;
  if (mode === "en") return en;
  return `${en} / ${zh}`;
}

export default function ResetTradingAccountButton({
  userId,
  market = "US",
  languageMode,
  onResetComplete,
}) {
  const [isResetting, setIsResetting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function handleReset() {
    const warning = labelByMode(
      languageMode,
      "Clear all simulated trades and holdings? Your deposits and monthly contribution settings will be preserved.",
      "\u78ba\u5b9a\u6e05\u9664\u6240\u6709\u6a21\u64ec\u4ea4\u6613\u53ca\u6301\u5009\uff1f\u5b58\u6b3e\u53ca\u6bcf\u6708\u6ce8\u8cc7\u8a2d\u5b9a\u5c07\u6703\u4fdd\u7559\u3002"
    );
    if (!window.confirm(warning)) {
      return;
    }

    setIsResetting(true);
    setMessage("");
    setError("");
    try {
      const response = await postVirtualTradingActivityReset(userId, market);
      setMessage(
        labelByMode(
          languageMode,
          `Trades and holdings cleared for profile ${response.user_id}. Funding was preserved.`,
          `Profile ${response.user_id} \u7684\u4ea4\u6613\u53ca\u6301\u5009\u5df2\u6e05\u9664\uff0c\u8cc7\u91d1\u8a18\u9304\u5df2\u4fdd\u7559\u3002`
        )
      );
      if (onResetComplete) {
        await onResetComplete();
      }
    } catch (requestError) {
      setError(
        requestError.message ||
          labelByMode(
            languageMode,
            "Clear failed. Please try again in a moment.",
            "\u6e05\u9664\u5931\u6557\uff0c\u8acb\u7a0d\u5f8c\u518d\u8a66\u3002"
          )
      );
    } finally {
      setIsResetting(false);
    }
  }

  return (
    <div className="settings-actions">
      <button type="button" onClick={handleReset} disabled={isResetting}>
        {isResetting
          ? labelByMode(languageMode, "Clearing trades...", "\u6b63\u5728\u6e05\u9664\u4ea4\u6613...")
          : labelByMode(languageMode, "Clear Trades & Holdings", "\u6e05\u9664\u4ea4\u6613\u53ca\u6301\u5009")}
      </button>
      {message ? <p className="success-box">{message}</p> : null}
      {error ? <p className="error-box">{error}</p> : null}
    </div>
  );
}

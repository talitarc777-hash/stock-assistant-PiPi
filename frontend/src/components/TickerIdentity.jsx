import React from "react";

import TickerClassificationTags from "./TickerClassificationTags";
import { tickerDisplayName } from "../utils/tickerIdentity";

export default function TickerIdentity({
  ticker,
  data = null,
  languageMode = "both",
  size = "xs",
  showClassification = true,
}) {
  const name = tickerDisplayName(data, ticker);
  return (
    <span className="ticker-identity">
      <span className="ticker-symbol">{ticker}</span>
      {name ? <span className="ticker-name" title={name}>— {name}</span> : null}
      {showClassification ? (
        <TickerClassificationTags
          ticker={ticker}
          classification={data || { ticker }}
          languageMode={languageMode}
          size={size}
        />
      ) : null}
    </span>
  );
}

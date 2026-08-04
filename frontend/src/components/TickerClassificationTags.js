import React from "react";

import {
  PRIMARY_TICKER_CLASS_CONFIG,
  STOCK_SUBCLASS_CONFIG,
  classificationLabel,
  resolveTickerClassification,
} from "../config/tickerClassification.js";

export default function TickerClassificationTags({
  ticker,
  classification = null,
  primaryClass = null,
  stockSubclass = null,
  languageMode = "en",
  size = "sm",
}) {
  const resolved = resolveTickerClassification(classification || ticker, {
    ticker,
    primaryClass,
    stockSubclass,
  });
  const primaryConfig = PRIMARY_TICKER_CLASS_CONFIG[resolved.primaryClass]
    || PRIMARY_TICKER_CLASS_CONFIG.unknown;
  const tagItems = [{ key: resolved.primaryClass, config: primaryConfig, dimension: "Primary class" }];

  if (resolved.primaryClass === "stock") {
    const subclassConfig = STOCK_SUBCLASS_CONFIG[resolved.stockSubclass]
      || STOCK_SUBCLASS_CONFIG.unknown;
    tagItems.push({
      key: resolved.stockSubclass || "unknown",
      config: subclassConfig,
      dimension: "Stock sector",
    });
  }

  const labels = tagItems.map((item) => classificationLabel(item.config, languageMode));
  return React.createElement(
    "span",
    {
      className: `ticker-classification-tags ticker-classification-tags--${size === "xs" ? "xs" : "sm"}`,
      "aria-label": `Ticker classification: ${labels.join(", ")}`,
      "data-testid": "ticker-classification-tags",
    },
    tagItems.map((item) => {
      const label = classificationLabel(item.config, languageMode);
      return React.createElement(
        "span",
        {
          className: `ticker-classification-tag ticker-classification-tag--${item.config.variant}`,
          key: `${item.dimension}-${item.key}`,
          title: `${item.dimension}: ${label}`,
        },
        label
      );
    })
  );
}

"""Reusable Pydantic fields for classified ticker API responses."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator

from app.services.ticker_classification import classify_ticker


PrimaryTickerClass = Literal[
    "stock",
    "etf",
    "index",
    "reit",
    "fixed_income",
    "commodity",
    "forex",
    "crypto",
    "derivative",
    "cash",
    "unknown",
]

StockSubclass = Literal[
    "technology",
    "financials",
    "consumer_cyclical",
    "consumer_defensive",
    "healthcare",
    "industrials",
    "energy",
    "materials",
    "utilities",
    "real_estate",
    "communication_services",
    "other",
    "unknown",
]


class ClassifiedTickerResponse(BaseModel):
    """Backward-compatible classification fields populated from ``ticker``."""

    ticker: str
    primary_ticker_class: PrimaryTickerClass = "unknown"
    stock_subclass: StockSubclass | None = None
    classification_source: str = "unknown"

    @model_validator(mode="after")
    def populate_ticker_classification(self) -> "ClassifiedTickerResponse":
        metadata = {
            "primary_ticker_class": self.primary_ticker_class,
            "stock_subclass": self.stock_subclass,
            "quote_type": getattr(self, "quote_type", None),
            "sector": getattr(self, "sector", None),
            "industry": getattr(self, "industry", None),
        }
        classification = classify_ticker(self.ticker, market_metadata=metadata)
        self.ticker = classification.ticker
        self.primary_ticker_class = classification.primary_ticker_class  # type: ignore[assignment]
        self.stock_subclass = classification.stock_subclass  # type: ignore[assignment]
        self.classification_source = classification.classification_source
        return self


class OptionalClassifiedTickerResponse(BaseModel):
    """Classification fields for ledger rows that may not involve a ticker."""

    ticker: str | None = None
    primary_ticker_class: PrimaryTickerClass = "unknown"
    stock_subclass: StockSubclass | None = None
    classification_source: str = "unknown"

    @model_validator(mode="after")
    def populate_optional_ticker_classification(self) -> "OptionalClassifiedTickerResponse":
        if not self.ticker:
            self.stock_subclass = None
            return self
        classification = classify_ticker(
            self.ticker,
            market_metadata={
                "primary_ticker_class": self.primary_ticker_class,
                "stock_subclass": self.stock_subclass,
            },
        )
        self.ticker = classification.ticker
        self.primary_ticker_class = classification.primary_ticker_class  # type: ignore[assignment]
        self.stock_subclass = classification.stock_subclass  # type: ignore[assignment]
        self.classification_source = classification.classification_source
        return self

"""Validated order-level signal contract for the clean-room project."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal


Side = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class TradePlan:
    symbol: str
    side: Side
    timeframe: str
    generated_at_utc: datetime
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    invalid_after_utc: datetime
    max_holding_hours: int
    review_interval_hours: int
    capital_at_risk_fraction: float
    early_exit_condition: str
    paper_only: bool = True

    def validate(self) -> None:
        if not self.symbol or not self.timeframe:
            raise ValueError("symbol and timeframe are required")
        if self.generated_at_utc.tzinfo is None or self.invalid_after_utc.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        if self.invalid_after_utc <= self.generated_at_utc:
            raise ValueError("signal expiry must be after generation")
        if self.max_holding_hours <= 0 or self.review_interval_hours <= 0:
            raise ValueError("holding and review intervals must be positive")
        if not 0.0 < self.capital_at_risk_fraction <= 0.02:
            raise ValueError("capital at risk must be in (0, 2%]")
        if not self.early_exit_condition.strip():
            raise ValueError("early-exit condition is required")
        prices = (
            self.entry,
            self.stop_loss,
            self.take_profit_1,
            self.take_profit_2,
            self.take_profit_3,
        )
        if any(price <= 0.0 for price in prices):
            raise ValueError("all prices must be positive")
        if self.side == "LONG":
            valid_order = (
                self.stop_loss
                < self.entry
                < self.take_profit_1
                < self.take_profit_2
                < self.take_profit_3
            )
        elif self.side == "SHORT":
            valid_order = (
                self.stop_loss
                > self.entry
                > self.take_profit_1
                > self.take_profit_2
                > self.take_profit_3
            )
        else:
            raise ValueError(f"unsupported side: {self.side}")
        if not valid_order:
            raise ValueError("entry, stop, and targets are inconsistent with side")

    def reward_to_risk(self) -> tuple[float, float, float]:
        self.validate()
        risk = abs(self.entry - self.stop_loss)
        return tuple(
            abs(target - self.entry) / risk
            for target in (
                self.take_profit_1,
                self.take_profit_2,
                self.take_profit_3,
            )
        )

    def as_payload(self) -> dict[str, Any]:
        self.validate()
        payload = asdict(self)
        payload["generated_at_utc"] = self.generated_at_utc.isoformat()
        payload["invalid_after_utc"] = self.invalid_after_utc.isoformat()
        payload["reward_to_risk"] = self.reward_to_risk()
        return payload

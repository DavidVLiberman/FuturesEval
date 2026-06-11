"""Opening Range Breakout (ORB) strategy for MES.

Why ORB for a Lucid evaluation:
- It produces a small number of trades per day (1-2), which suits the
  consistency rule and a self-imposed daily stop.
- Risk is defined before entry (the opposite side of the opening range),
  so position size can be computed to hit an exact dollar risk — critical
  when the binding constraint is a $1,500 trailing drawdown.
- It is flat well before Lucid's 4:45 PM ET auto-flatten.

Mechanics (defaults; see StrategyConfig):
- Opening range = high/low of 09:30-09:45 ET.
- Long when a 5m bar *closes* above the OR high; short when one closes
  below the OR low. One trade per direction, max 2 trades/day.
- Initial stop at the opposite side of the range; optional move to
  breakeven at +1R; target at +1.5R; time exit 15:55 ET.
- Day filters: skip if the OR is wider than 65% of the 14-day average
  session range (stop too wide) or narrower than 8 ticks (noise).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from .config import StrategyConfig, ContractSpec


@dataclass
class Signal:
    timestamp: datetime
    side: int          # +1 long, -1 short
    entry: float
    stop: float
    target: float


@dataclass
class SessionPlan:
    or_high: float | None = None
    or_low: float | None = None
    tradeable: bool = False
    skip_reason: str = ""


def average_session_range(sessions: dict, lookback: int = 14) -> float | None:
    """Mean (high-low) of the last `lookback` completed sessions."""
    ranges = [s["high"].max() - s["low"].min() for s in sessions]
    if len(ranges) < lookback:
        return None
    return float(sum(ranges[-lookback:]) / lookback)


class ORBStrategy:
    def __init__(self, cfg: StrategyConfig, spec: ContractSpec):
        self.cfg = cfg
        self.spec = spec

    def build_plan(self, session: pd.DataFrame,
                   avg_range: float | None) -> SessionPlan:
        """Compute the opening range and decide whether today is tradeable."""
        cfg = self.cfg
        n_or_bars = cfg.opening_range_minutes // cfg.bar_minutes
        if len(session) <= n_or_bars:
            return SessionPlan(skip_reason="not enough bars")
        or_bars = session.iloc[:n_or_bars]
        plan = SessionPlan(
            or_high=float(or_bars["high"].max()),
            or_low=float(or_bars["low"].min()),
        )
        or_range = plan.or_high - plan.or_low
        if or_range < cfg.min_or_ticks * self.spec.tick_size:
            plan.skip_reason = "opening range too narrow"
            return plan
        if avg_range is not None and or_range > cfg.max_or_to_avg_range * avg_range:
            plan.skip_reason = "opening range too wide vs average"
            return plan
        plan.tradeable = True
        return plan

    def check_entry(self, plan: SessionPlan, bar: pd.Series,
                    ts: pd.Timestamp, taken_sides: set[int]) -> Signal | None:
        """Evaluate one completed bar for a breakout entry."""
        cfg = self.cfg
        if not plan.tradeable:
            return None
        if ts.timetz().replace(tzinfo=None) >= cfg.entry_cutoff_et:
            return None
        close = float(bar["close"])
        if close > plan.or_high and (1 not in taken_sides
                                     or not cfg.one_trade_per_direction):
            risk = close - plan.or_low
            return Signal(ts, +1, close, plan.or_low,
                          close + cfg.reward_risk * risk)
        if close < plan.or_low and (-1 not in taken_sides
                                    or not cfg.one_trade_per_direction):
            risk = plan.or_high - close
            return Signal(ts, -1, close, plan.or_high,
                          close - cfg.reward_risk * risk)
        return None

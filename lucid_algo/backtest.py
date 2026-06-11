"""Event-driven bar backtester that simulates the Lucid evaluation itself.

The loop trades the ORB strategy through the PropRiskManager, so every
fill is gated and sized exactly as it would be live: trades are skipped
once the daily stop/profit lock hits, size throttles as the MLL buffer
shrinks, and the run ends the day the evaluation is passed or failed.

Fill model (conservative):
- Entries at the signal bar close +/- slippage.
- If a bar touches both stop and target, the stop is assumed to fill.
- Time exit at the session-exit bar close.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from .config import Config
from .risk import PropRiskManager
from .strategy import ORBStrategy, Signal, average_session_range


@dataclass
class Trade:
    entry_time: datetime
    exit_time: datetime
    side: int
    contracts: int
    entry: float
    exit: float
    pnl: float
    exit_reason: str
    r_multiple: float


@dataclass
class BacktestResult:
    trades: list[Trade]
    daily_pnl: pd.Series
    status: dict
    passed_on_day: int | None     # 1-based trading-session count, None if not
    config: Config = field(repr=False, default=None)

    @property
    def equity_curve(self) -> pd.Series:
        return self.daily_pnl.cumsum() + self.config.rules.account_size

    def summary(self) -> dict:
        wins = [t for t in self.trades if t.pnl > 0]
        losses = [t for t in self.trades if t.pnl <= 0]
        gross_win = sum(t.pnl for t in wins)
        gross_loss = -sum(t.pnl for t in losses)
        return {
            **self.status,
            "n_trades": len(self.trades),
            "win_rate": round(len(wins) / len(self.trades), 3) if self.trades else None,
            "avg_win": round(gross_win / len(wins), 2) if wins else None,
            "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "passed_on_day": self.passed_on_day,
            "max_day_loss": round(self.daily_pnl.min(), 2) if len(self.daily_pnl) else 0.0,
        }


@dataclass
class _Position:
    signal: Signal
    contracts: int
    stop: float
    breakeven_armed: bool = False


class EvaluationBacktester:
    def __init__(self, config: Config):
        self.cfg = config
        self.strategy = ORBStrategy(config.strategy, config.contract)
        self.risk = PropRiskManager(config)

    def run(self, bars: pd.DataFrame) -> BacktestResult:
        cfg, spec = self.cfg, self.cfg.contract
        trades: list[Trade] = []
        daily: dict[date, float] = {}
        past_sessions: list[pd.DataFrame] = []
        passed_on_day: int | None = None
        slip = spec.slippage_ticks * spec.tick_size

        for day, session in bars.groupby(bars.index.date):
            if self.risk.state.failed or passed_on_day is not None:
                break
            self.risk.start_day(day)
            avg_range = average_session_range(past_sessions)
            plan = self.strategy.build_plan(session, avg_range)
            position: _Position | None = None
            taken_sides: set[int] = set()
            n_or = cfg.strategy.opening_range_minutes // cfg.strategy.bar_minutes

            for i in range(n_or, len(session)):
                ts = session.index[i]
                bar = session.iloc[i]
                bar_t = ts.timetz().replace(tzinfo=None)
                is_exit_time = bar_t >= cfg.strategy.session_exit_et

                if position is not None:
                    closed = self._manage(position, bar, ts, is_exit_time,
                                          slip, trades)
                    if closed:
                        position = None
                if self.risk.state.failed:
                    break

                if position is None and not is_exit_time:
                    sig = self.strategy.check_entry(plan, bar, ts, taken_sides)
                    if sig is not None:
                        allowed, _ = self.risk.can_open_trade()
                        if allowed:
                            stop_pts = abs(sig.entry - sig.stop)
                            n = self.risk.position_size(stop_pts)
                            if n > 0:
                                sig.entry += sig.side * slip
                                position = _Position(sig, n, sig.stop)
                        taken_sides.add(sig.side)

            day_rec = self.risk.state.current_day
            daily[day] = day_rec.pnl if day_rec else 0.0
            self.risk.end_day()
            past_sessions.append(session)
            if self.risk.evaluation_passed() and passed_on_day is None:
                passed_on_day = len(daily)

        idx = pd.DatetimeIndex([pd.Timestamp(d) for d in daily])
        return BacktestResult(
            trades=trades,
            daily_pnl=pd.Series(list(daily.values()), index=idx, name="pnl"),
            status=self.risk.status(),
            passed_on_day=passed_on_day,
            config=self.cfg,
        )

    def _manage(self, pos: _Position, bar: pd.Series, ts: pd.Timestamp,
                is_exit_time: bool, slip: float, trades: list[Trade]) -> bool:
        """Update one open position for one bar. True if closed."""
        cfg, spec = self.cfg, self.cfg.contract
        sig, side = pos.signal, pos.signal.side
        high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
        risk_pts = abs(sig.entry - sig.stop)

        exit_px = exit_reason = None
        # Stop first when both levels are inside the bar (conservative).
        if side == 1 and low <= pos.stop:
            exit_px, exit_reason = pos.stop - slip, "stop"
        elif side == -1 and high >= pos.stop:
            exit_px, exit_reason = pos.stop + slip, "stop"
        elif side == 1 and high >= sig.target:
            exit_px, exit_reason = sig.target, "target"
        elif side == -1 and low <= sig.target:
            exit_px, exit_reason = sig.target, "target"
        elif is_exit_time:
            exit_px, exit_reason = close - side * slip, "time"

        if exit_px is None:
            be_r = cfg.strategy.move_stop_to_breakeven_at_r
            if be_r is not None and not pos.breakeven_armed:
                trigger = sig.entry + side * be_r * risk_pts
                if (side == 1 and high >= trigger) or (side == -1 and low <= trigger):
                    pos.stop = sig.entry
                    pos.breakeven_armed = True
            return False

        gross = side * (exit_px - sig.entry) * spec.point_value * pos.contracts
        costs = 2 * spec.commission_per_side * pos.contracts
        pnl = gross - costs
        denom = risk_pts * spec.point_value * pos.contracts
        trades.append(Trade(
            entry_time=sig.timestamp, exit_time=ts, side=side,
            contracts=pos.contracts, entry=sig.entry, exit=exit_px,
            pnl=pnl, exit_reason=exit_reason,
            r_multiple=round(pnl / denom, 2) if denom else 0.0,
        ))
        self.risk.record_trade(pnl)
        return True

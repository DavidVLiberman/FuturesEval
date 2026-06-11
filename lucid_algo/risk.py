"""Prop-firm risk engine tailored to Lucid Trading's evaluation rules.

This module is deliberately independent of the strategy: it tracks account
state (balance, end-of-day trailing Max Loss Limit, per-day P&L, best day,
trading days), decides whether a new trade may be opened, and sizes
positions. Everything the firm enforces is enforced here first, with
tighter self-imposed limits layered on top so the firm's limits are never
the binding constraint.
"""

from dataclasses import dataclass, field
from datetime import date

from .config import Config


@dataclass
class DayRecord:
    day: date
    pnl: float = 0.0
    trades: int = 0


@dataclass
class AccountState:
    balance: float
    mll: float                      # current Max Loss Limit (absolute level)
    days: list[DayRecord] = field(default_factory=list)
    current_day: DayRecord | None = None
    failed: bool = False
    fail_reason: str | None = None

    @property
    def total_profit(self) -> float:
        closed = sum(d.pnl for d in self.days)
        open_day = self.current_day.pnl if self.current_day else 0.0
        return closed + open_day

    @property
    def best_day(self) -> float:
        candidates = [d.pnl for d in self.days]
        if self.current_day is not None:
            candidates.append(self.current_day.pnl)
        return max(candidates, default=0.0)

    @property
    def trading_days(self) -> int:
        n = sum(1 for d in self.days if d.trades > 0)
        if self.current_day is not None and self.current_day.trades > 0:
            n += 1
        return n


class PropRiskManager:
    def __init__(self, config: Config):
        self.cfg = config
        rules = config.rules
        self.state = AccountState(
            balance=rules.account_size,
            mll=rules.account_size - rules.max_loss_limit,
        )

    # -- session lifecycle ---------------------------------------------------

    def start_day(self, day: date) -> None:
        self.state.current_day = DayRecord(day=day)

    def end_day(self) -> None:
        """Close out the session: book the day, check breach, trail the MLL."""
        st, rules = self.state, self.cfg.rules
        if st.current_day is None:
            return
        st.days.append(st.current_day)
        st.current_day = None

        if st.balance <= st.mll:
            st.failed = True
            st.fail_reason = (
                f"EOD balance {st.balance:,.2f} breached MLL {st.mll:,.2f}"
            )
            return

        if rules.eod_trailing:
            # MLL trails the end-of-day balance and locks once the MLL
            # itself reaches the starting balance + lock buffer.
            mll_cap = rules.account_size + rules.mll_lock_buffer
            trailed = min(st.balance - rules.max_loss_limit, mll_cap)
            st.mll = max(st.mll, trailed)

    def record_trade(self, pnl: float) -> None:
        st = self.state
        assert st.current_day is not None, "start_day() must be called first"
        st.current_day.pnl += pnl
        st.current_day.trades += 1
        st.balance += pnl
        if self.cfg.rules.intraday_breach_fails and st.balance <= st.mll:
            st.failed = True
            st.fail_reason = (
                f"Intraday balance {st.balance:,.2f} breached MLL {st.mll:,.2f}"
            )

    # -- gating --------------------------------------------------------------

    def can_open_trade(self) -> tuple[bool, str]:
        """May a new trade be opened right now? Returns (allowed, reason)."""
        st, risk, rules = self.state, self.cfg.risk, self.cfg.rules
        day = st.current_day
        if st.failed:
            return False, "account failed"
        if self.evaluation_passed():
            return False, "evaluation already passed"
        if day is None:
            return False, "no active session"
        if day.trades >= risk.max_trades_per_day:
            return False, "max trades per day reached"
        if day.pnl <= -risk.daily_loss_stop:
            return False, "self-imposed daily loss stop hit"
        if day.pnl >= self._profit_lock():
            return False, "daily profit lock reached"
        # Never start a trade whose full stop-out could breach the MLL at EOD.
        buffer = st.balance - st.mll
        if buffer <= self.current_risk_per_trade() + self.cfg.contract.round_trip_cost():
            return False, "insufficient buffer above Max Loss Limit"
        if rules.daily_loss_limit is not None and day.pnl <= -rules.daily_loss_limit:
            return False, "firm daily loss limit hit"
        return True, "ok"

    def _profit_lock(self) -> float:
        """Daily profit level at which we stop trading for the day.

        Two purposes: (1) bank green days instead of giving them back, and
        (2) keep the best day inside the consistency rule. With the default
        $550 lock and a $1,250 target, the worst-case best-day share is 44%.
        """
        risk, rules = self.cfg.risk, self.cfg.rules
        lock = risk.daily_profit_lock
        if rules.consistency_pct is not None:
            total = max(self.state.total_profit, 0.0)
            # Largest day d must satisfy d <= pct * (total + d), i.e.
            # d <= pct/(1-pct) * total_of_other_days. Near the target, allow
            # up to headroom * the consistency cap implied by the target.
            cap = rules.consistency_pct * rules.profit_target
            lock = min(lock, risk.consistency_headroom * cap)
        return lock

    # -- sizing --------------------------------------------------------------

    def current_risk_per_trade(self) -> float:
        """Risk per trade in USD, throttled as the MLL buffer shrinks."""
        st, risk = self.state, self.cfg.risk
        buffer = st.balance - st.mll
        if buffer < risk.min_size_buffer:
            return 0.0
        if buffer < risk.half_size_buffer:
            return risk.risk_per_trade / 2.0
        return risk.risk_per_trade

    def position_size(self, stop_distance_points: float) -> int:
        """Number of contracts for a stop this many points away. 0 = skip."""
        spec = self.cfg.contract
        risk_usd = self.current_risk_per_trade()
        if risk_usd <= 0 or stop_distance_points <= 0:
            return 0
        per_contract = stop_distance_points * spec.point_value + spec.round_trip_cost()
        contracts = int(risk_usd // per_contract)
        cap = (self.cfg.rules.max_micros if spec.is_micro
               else self.cfg.rules.max_minis)
        return max(0, min(contracts, cap))

    # -- evaluation status ----------------------------------------------------

    def evaluation_passed(self) -> bool:
        st, rules = self.state, self.cfg.rules
        if st.failed or st.total_profit < rules.profit_target:
            return False
        if st.trading_days < rules.min_trading_days:
            return False
        if rules.consistency_pct is not None and st.total_profit > 0:
            if st.best_day > rules.consistency_pct * st.total_profit:
                return False
        return True

    def status(self) -> dict:
        st = self.state
        return {
            "balance": round(st.balance, 2),
            "mll": round(st.mll, 2),
            "buffer": round(st.balance - st.mll, 2),
            "total_profit": round(st.total_profit, 2),
            "best_day": round(st.best_day, 2),
            "trading_days": st.trading_days,
            "passed": self.evaluation_passed(),
            "failed": st.failed,
            "fail_reason": st.fail_reason,
        }

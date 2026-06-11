"""Monte Carlo evaluation simulator.

Answers the question the backtest alone can't: *given* a strategy with a
certain win rate and payoff, what risk-per-trade maximizes the probability
of passing the Lucid evaluation before busting it?

It simulates thousands of evaluations trade-by-trade under the full rule
set (EOD trailing MLL, consistency cap, daily stop / profit lock) and
reports pass probability and time-to-pass. Use sweep_risk() to pick the
risk size; the optimum is usually far below what feels comfortable.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Config


@dataclass
class MCParams:
    win_rate: float = 0.42          # ORB-with-breakeven typical neighborhood
    avg_win_r: float = 1.45         # mean winner in R (after costs)
    avg_loss_r: float = 0.95        # mean loser in R (breakeven exits shrink it)
    scratch_rate: float = 0.10      # fraction of trades exiting ~flat
    trades_per_day_max: int = 2
    p_trade_day: float = 0.75       # chance a day produces any signal
    max_days: int = 90
    n_sims: int = 20_000
    seed: int = 11


@dataclass
class MCResult:
    pass_rate: float
    fail_rate: float
    timeout_rate: float
    median_days_to_pass: float | None
    p90_days_to_pass: float | None
    risk_per_trade: float

    def __str__(self) -> str:
        med = f"{self.median_days_to_pass:.0f}" if self.median_days_to_pass else "-"
        p90 = f"{self.p90_days_to_pass:.0f}" if self.p90_days_to_pass else "-"
        return (f"risk ${self.risk_per_trade:>6.0f}: "
                f"pass {self.pass_rate:6.1%}  fail {self.fail_rate:6.1%}  "
                f"timeout {self.timeout_rate:6.1%}  "
                f"days to pass: median {med}, p90 {p90}")


def simulate(config: Config, params: MCParams,
             risk_per_trade: float | None = None) -> MCResult:
    rules, riskcfg = config.rules, config.risk
    risk = risk_per_trade if risk_per_trade is not None else riskcfg.risk_per_trade
    rng = np.random.default_rng(params.seed)

    passes, fails, days_to_pass = 0, 0, []
    for _ in range(params.n_sims):
        balance = rules.account_size
        mll = rules.account_size - rules.max_loss_limit
        total, best_day, trading_days = 0.0, 0.0, 0
        outcome = "timeout"

        for day in range(1, params.max_days + 1):
            if rng.random() > params.p_trade_day:
                continue
            day_pnl, traded = 0.0, False
            for _t in range(params.trades_per_day_max):
                if day_pnl <= -riskcfg.daily_loss_stop:
                    break
                if day_pnl >= riskcfg.daily_profit_lock:
                    break
                if balance + day_pnl - risk <= mll:
                    break  # buffer too thin to take the trade
                u = rng.random()
                if u < params.scratch_rate:
                    pnl = rng.normal(0, 0.05) * risk
                elif u < params.scratch_rate + params.win_rate * (1 - params.scratch_rate):
                    pnl = params.avg_win_r * risk * rng.lognormal(0, 0.25) / np.exp(0.25**2 / 2)
                else:
                    pnl = -params.avg_loss_r * risk * min(rng.lognormal(0, 0.1), 1.3)
                day_pnl += pnl
                traded = True

            balance += day_pnl
            total += day_pnl
            if traded:
                trading_days += 1
            best_day = max(best_day, day_pnl)

            if balance <= mll:
                outcome = "fail"
                break
            if rules.eod_trailing:
                mll_cap = rules.account_size + rules.mll_lock_buffer
                mll = max(mll, min(balance - rules.max_loss_limit, mll_cap))

            consistent = (rules.consistency_pct is None
                          or total <= 0
                          or best_day <= rules.consistency_pct * total)
            if (total >= rules.profit_target
                    and trading_days >= rules.min_trading_days and consistent):
                outcome = "pass"
                days_to_pass.append(day)
                break

        if outcome == "pass":
            passes += 1
        elif outcome == "fail":
            fails += 1

    n = params.n_sims
    dtp = np.array(days_to_pass) if days_to_pass else None
    return MCResult(
        pass_rate=passes / n,
        fail_rate=fails / n,
        timeout_rate=1 - (passes + fails) / n,
        median_days_to_pass=float(np.median(dtp)) if dtp is not None else None,
        p90_days_to_pass=float(np.percentile(dtp, 90)) if dtp is not None else None,
        risk_per_trade=risk,
    )


def sweep_risk(config: Config, params: MCParams,
               risks: list[float] = (75, 100, 125, 150, 200, 250, 300)) -> list[MCResult]:
    return [simulate(config, params, r) for r in risks]

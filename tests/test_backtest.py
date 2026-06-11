"""End-to-end tests: synthetic data -> strategy -> risk engine -> result."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lucid_algo.backtest import EvaluationBacktester
from lucid_algo.config import Config
from lucid_algo.data import make_synthetic_mes
from lucid_algo.montecarlo import MCParams, simulate


def test_backtest_runs_and_respects_limits():
    bars = make_synthetic_mes(n_days=60, seed=3)
    config = Config()
    result = EvaluationBacktester(config).run(bars)

    assert len(result.daily_pnl) > 0
    # Self-imposed limits: never more than max trades/day
    by_day = {}
    for t in result.trades:
        by_day.setdefault(t.entry_time.date(), 0)
        by_day[t.entry_time.date()] += 1
    assert all(n <= config.risk.max_trades_per_day for n in by_day.values())
    # Contract cap respected
    assert all(t.contracts <= config.rules.max_micros for t in result.trades)
    # Risk per trade respected within slippage tolerance:
    # worst loss should not exceed risk + generous slippage allowance
    worst = min((t.pnl for t in result.trades), default=0)
    assert worst >= -(config.risk.risk_per_trade * 1.5)


def test_backtest_stops_after_pass_or_fail():
    bars = make_synthetic_mes(n_days=120, seed=5)
    result = EvaluationBacktester(Config()).run(bars)
    if result.passed_on_day is not None:
        assert result.status["passed"]
        assert len(result.daily_pnl) == result.passed_on_day
    if result.status["failed"]:
        assert result.status["fail_reason"]


def test_montecarlo_sane():
    params = MCParams(n_sims=2_000, seed=1)
    res = simulate(Config(), params, risk_per_trade=125)
    assert 0 <= res.pass_rate <= 1
    assert abs(res.pass_rate + res.fail_rate + res.timeout_rate - 1) < 1e-9
    # A positive-expectancy edge at sane risk should pass far more than fail
    assert res.pass_rate > res.fail_rate


def test_montecarlo_negative_edge_mostly_fails():
    params = MCParams(win_rate=0.25, avg_win_r=1.0, avg_loss_r=1.0,
                      n_sims=2_000, seed=2)
    res = simulate(Config(), params, risk_per_trade=300)
    assert res.fail_rate > res.pass_rate

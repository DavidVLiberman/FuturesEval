"""Tests for the prop-firm risk engine against LucidFlex 25K rules."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lucid_algo.config import Config, LUCID_FLEX_25K
from lucid_algo.risk import PropRiskManager


def make_rm() -> PropRiskManager:
    return PropRiskManager(Config(rules=LUCID_FLEX_25K))


def test_initial_mll():
    rm = make_rm()
    assert rm.state.balance == 25_000
    assert rm.state.mll == 23_500


def test_eod_trailing_mll_moves_up_and_locks():
    rm = make_rm()
    rm.start_day(date(2026, 1, 5))
    rm.record_trade(400.0)
    rm.end_day()
    assert rm.state.mll == 25_400 - 1_500  # trails EOD balance

    # Big run-up: MLL locks once it reaches start balance + buffer
    rm.start_day(date(2026, 1, 6))
    rm.record_trade(2_000.0)
    rm.end_day()
    assert rm.state.mll == 25_000 + 100


def test_mll_never_moves_down():
    rm = make_rm()
    rm.start_day(date(2026, 1, 5))
    rm.record_trade(400.0)
    rm.end_day()
    mll_after_up = rm.state.mll
    rm.start_day(date(2026, 1, 6))
    rm.record_trade(-300.0)
    rm.end_day()
    assert rm.state.mll == mll_after_up


def test_eod_breach_fails_account():
    rm = make_rm()
    rm.start_day(date(2026, 1, 5))
    rm.record_trade(-1_600.0)
    assert not rm.state.failed  # EOD drawdown: no intraday fail
    rm.end_day()
    assert rm.state.failed


def test_daily_loss_stop_blocks_trading():
    rm = make_rm()
    rm.start_day(date(2026, 1, 5))
    rm.record_trade(-260.0)
    allowed, reason = rm.can_open_trade()
    assert not allowed and "daily loss stop" in reason


def test_max_trades_per_day():
    rm = make_rm()
    rm.start_day(date(2026, 1, 5))
    rm.record_trade(10.0)
    rm.record_trade(10.0)
    allowed, reason = rm.can_open_trade()
    assert not allowed and "max trades" in reason


def test_profit_lock_respects_consistency():
    rm = make_rm()
    rm.start_day(date(2026, 1, 5))
    rm.record_trade(560.0)
    allowed, reason = rm.can_open_trade()
    assert not allowed and "profit lock" in reason
    # Profit lock must keep best day under 50% of the $1,250 target
    assert rm._profit_lock() <= 0.5 * 1_250


def test_position_size_throttles_near_mll():
    rm = make_rm()
    rm.start_day(date(2026, 1, 5))
    full = rm.position_size(stop_distance_points=5.0)  # $25/contract on MES
    assert full >= 1
    rm.record_trade(-900.0)  # buffer now $600 -> half size
    half = rm.position_size(stop_distance_points=5.0)
    assert half <= max(1, full // 2)
    rm.record_trade(-300.0)  # buffer now $300 -> below min buffer, no trade
    assert rm.position_size(stop_distance_points=5.0) == 0


def test_contract_cap():
    rm = make_rm()
    rm.start_day(date(2026, 1, 5))
    # Tiny stop would imply huge size; must cap at 20 micros
    assert rm.position_size(stop_distance_points=0.5) <= 20


def test_evaluation_pass_logic():
    rm = make_rm()
    # Day 1: +600, day 2: +400, day 3: +400 -> total 1400, best day 600
    for d, pnl in [(5, 600.0), (6, 400.0), (7, 400.0)]:
        rm.start_day(date(2026, 1, d))
        rm.record_trade(pnl)
        rm.end_day()
    assert rm.evaluation_passed()  # 600 <= 0.5 * 1400


def test_consistency_blocks_pass():
    rm = make_rm()
    for d, pnl in [(5, 1_000.0), (6, 300.0)]:
        rm.start_day(date(2026, 1, d))
        rm.record_trade(pnl)
        rm.end_day()
    # total 1300 >= target, but best day 1000 > 50% of 1300
    assert not rm.evaluation_passed()


def test_min_trading_days():
    rm = make_rm()
    rm.start_day(date(2026, 1, 5))
    rm.record_trade(1_300.0)
    rm.end_day()
    cfg = Config(rules=LUCID_FLEX_25K)
    # one trading day only -> cannot pass regardless of profit
    assert rm.state.trading_days == 1
    assert not rm.evaluation_passed()

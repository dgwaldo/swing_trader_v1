import unittest
from dataclasses import replace

import pandas as pd

from swingtrader.backtest import _historical_strategy, run_backtest
from swingtrader.config import TradingConfig
from swingtrader.scanner import TradeCandidate


def candidate(symbol="TEST"):
    return TradeCandidate(
        symbol=symbol,
        score=70,
        prob_gain_10d=0.4,
        prob_loss_5d=0.2,
        risk_rating="Medium",
        entry=10.0,
        stop=9.0,
        target_1pct=10.1,
        target_1=11.5,
        target_2=13.0,
        shares=10,
        risk_dollars=10.0,
        reward_risk=1.5,
        main_catalyst="Technical Trend",
        setup="Breakout",
        reasons=[],
    )


class BacktestTests(unittest.TestCase):
    def setUp(self):
        self.cfg = replace(
            TradingConfig(),
            account_size=1000,
            max_position_fraction=0.9,
            minimum_price=1,
            maximum_price=100,
            max_combined_risk_fraction=1,
            target_percent=1,
        )

    def test_signal_is_filled_at_next_open_and_stop_gap_fills_at_open(self):
        bars = pd.DataFrame(
            {
                "Open": [10.0, 10.0, 8.0],
                "High": [10.2, 10.05, 8.1],
                "Low": [9.9, 9.8, 7.9],
                "Close": [10.0, 10.1, 8.0],
                "Volume": [1_000_000] * 3,
            },
            index=pd.date_range("2025-01-02", periods=3),
        )

        result = run_backtest(
            {"TEST": bars}, self.cfg, slippage_bps=0,
            strategy=lambda _symbol, history, _cfg: candidate() if len(history) == 1 else None,
        )

        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.entry_date, "2025-01-03")
        self.assertEqual(trade.exit_date, "2025-01-04")
        self.assertEqual(trade.exit_reason, "stop_gap")
        self.assertEqual(trade.exit_price, 8.0)

    def test_future_bars_cannot_change_prior_strategy_inputs(self):
        dates = pd.date_range("2025-01-02", periods=4)
        past = pd.DataFrame(
            {"Open": [10.0, 10.0], "High": [10.2, 10.3], "Low": [9.9, 9.8],
             "Close": [10.0, 10.1], "Volume": [1_000_000] * 2},
            index=dates[:2],
        )
        future_a = pd.DataFrame(
            {"Open": [10.0, 10.0], "High": [10.2, 10.2], "Low": [9.9, 9.9],
             "Close": [10.0, 10.0], "Volume": [1_000_000] * 2},
            index=dates[2:],
        )
        future_b = future_a.copy()
        future_b.loc[:, "Close"] = [50.0, 2.0]
        future_b.loc[:, "High"] = [55.0, 3.0]
        future_b.loc[:, "Low"] = [9.0, 1.0]
        observed = []

        def strategy(_symbol, history, _cfg):
            observed.append(history.copy())
            return None

        run_backtest({"TEST": pd.concat([past, future_a])}, self.cfg, strategy=strategy)
        first_run = observed
        observed = []
        run_backtest({"TEST": pd.concat([past, future_b])}, self.cfg, strategy=strategy)

        pd.testing.assert_frame_equal(first_run[1], observed[1])
        self.assertEqual(first_run[1].index[-1], dates[1])

    def test_real_strategy_candidate_is_unchanged_by_bars_after_signal_date(self):
        dates = pd.date_range("2025-01-02", periods=231)
        close = pd.Series([10.0 + index * 0.05 for index in range(229)], index=dates[:229])
        history = pd.DataFrame(
            {
                "Open": close,
                "High": close,
                "Low": close - 0.1,
                "Close": close,
                "Volume": [1_000_000] * len(close),
            },
            index=dates[:229],
        )
        future_a = pd.DataFrame(
            {"Open": [12.2, 12.3], "High": [12.4, 12.5], "Low": [12.1, 12.2],
             "Close": [12.3, 12.4], "Volume": [1_000_000] * 2},
            index=dates[229:],
        )
        future_b = pd.DataFrame(
            {"Open": [40.0, 2.0], "High": [45.0, 3.0], "Low": [35.0, 1.0],
             "Close": [42.0, 1.5], "Volume": [1_000_000] * 2},
            index=dates[229:],
        )
        signal_date = dates[228]

        def candidate_at_signal(future):
            observed = []

            def strategy(symbol, bars, cfg):
                result = _historical_strategy(symbol, bars, cfg)
                if bars.index[-1] == signal_date:
                    observed.append((bars.copy(), result))
                return result

            run_backtest({"TEST": pd.concat([history, future])}, self.cfg, strategy=strategy)
            return observed[0]

        first_bars, first_candidate = candidate_at_signal(future_a)
        second_bars, second_candidate = candidate_at_signal(future_b)

        pd.testing.assert_frame_equal(first_bars, history)
        pd.testing.assert_frame_equal(second_bars, history)
        self.assertIsNotNone(first_candidate)
        self.assertEqual(first_candidate, second_candidate)

    def test_daily_bar_touching_stop_and_target_uses_stop_first(self):
        bars = pd.DataFrame(
            {
                "Open": [10.0, 10.0, 10.0],
                "High": [10.2, 10.05, 10.5],
                "Low": [9.9, 9.8, 8.0],
                "Close": [10.0, 10.0, 9.0],
                "Volume": [1_000_000] * 3,
            },
            index=pd.date_range("2025-01-02", periods=3),
        )

        result = run_backtest(
            {"TEST": bars}, self.cfg,
            strategy=lambda _symbol, history, _cfg: candidate() if len(history) == 1 else None,
        )

        self.assertEqual(result.trades[0].exit_reason, "stop")
        self.assertLess(result.trades[0].realized_pnl, 0)

    def test_target_touch_policy_requires_trade_through_by_default(self):
        bars = pd.DataFrame(
            {
                "Open": [10.0, 10.0, 10.0],
                "High": [10.2, 10.05, 10.1],
                "Low": [9.9, 9.8, 9.9],
                "Close": [10.0, 10.0, 10.02],
                "Volume": [1_000_000] * 3,
            },
            index=pd.date_range("2025-01-02", periods=3),
        )
        strategy = lambda _symbol, history, _cfg: candidate() if len(history) == 1 else None

        conservative = run_backtest({"TEST": bars}, self.cfg, slippage_bps=0, strategy=strategy)
        touch_fill = run_backtest(
            {"TEST": bars}, self.cfg, slippage_bps=0,
            target_fill_mode="touch", strategy=strategy,
        )

        self.assertEqual(conservative.trades[0].exit_reason, "end_of_test")
        self.assertEqual(touch_fill.trades[0].exit_reason, "target")
        self.assertAlmostEqual(touch_fill.trades[0].exit_price, 10.1)

    def test_missing_symbol_session_uses_last_close_and_final_equity_is_liquidated(self):
        test_bars = pd.DataFrame(
            {"Open": [10.0, 10.0], "High": [10.2, 10.05], "Low": [9.9, 9.8],
             "Close": [10.0, 10.0], "Volume": [1_000_000] * 2},
            index=pd.date_range("2025-01-02", periods=2),
        )
        other_bars = pd.DataFrame(
            {"Open": [20.0], "High": [20.0], "Low": [20.0], "Close": [20.0], "Volume": [1_000_000]},
            index=pd.DatetimeIndex([pd.Timestamp("2025-01-06")]),
        )

        result = run_backtest(
            {"TEST": test_bars, "OTHER": other_bars}, self.cfg, slippage_bps=0,
            strategy=lambda symbol, history, _cfg: candidate(symbol) if symbol == "TEST" and len(history) == 1 else None,
        )

        self.assertEqual(result.trades[0].exit_date, "2025-01-03")
        self.assertEqual(result.trades[0].exit_reason, "end_of_test")
        self.assertAlmostEqual(float(result.equity_curve.iloc[-1]), result.ending_equity)


if __name__ == "__main__":
    unittest.main()
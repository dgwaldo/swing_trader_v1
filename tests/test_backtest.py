import json
import sqlite3
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from swingtrader.backtest import (
    _historical_strategy,
    _market_regimes,
    configuration_hash,
    format_spread_sensitivity_report,
    run_backtest,
)
from swingtrader.backtest_store import persist_sensitivity_results
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

    def test_spy_regime_labels_are_causal_and_include_high_volatility(self):
        dates = pd.date_range("2024-01-02", periods=230)
        closes = [100.0 + index * 0.1 for index in range(230)]
        benchmark = pd.DataFrame(
            {"Close": closes}, index=dates,
        )
        signal_date = dates[220]
        regimes = _market_regimes(benchmark)
        future_changed = benchmark.copy()
        future_changed.loc[dates[221]:, "Close"] = [50.0 + i * 0.5 for i in range(9)]
        changed_regimes = _market_regimes(future_changed)

        self.assertEqual(regimes[dates[100]], "UNKNOWN")
        self.assertEqual(regimes[signal_date], "BULL")
        self.assertEqual(
            {day: regimes[day] for day in dates[:221]},
            {day: changed_regimes[day] for day in dates[:221]},
        )

        volatile = benchmark.copy()
        volatile_closes = list(volatile["Close"])
        for index in range(200, 230):
            volatile_closes[index] = volatile_closes[index - 1] * (1.06 if index % 2 else 0.94)
        volatile["Close"] = volatile_closes
        self.assertEqual(_market_regimes(volatile)[dates[-1]], "HIGH_VOLATILITY")

    def test_trade_carries_signal_regime_and_portfolio_exposure_metrics(self):
        benchmark_dates = pd.date_range("2024-01-02", periods=230)
        benchmark_close = pd.Series(
            [100.0 + index * 0.1 for index in range(230)], index=benchmark_dates,
        )
        benchmark = pd.DataFrame(
            {"Open": benchmark_close, "High": benchmark_close, "Low": benchmark_close,
             "Close": benchmark_close, "Volume": [1_000_000] * len(benchmark_close)},
            index=benchmark_dates,
        )
        stock_bars = pd.DataFrame(
            {"Open": [10.0, 10.0, 8.0], "High": [10.2, 10.05, 8.1],
             "Low": [9.9, 9.8, 7.9], "Close": [10.0, 10.0, 8.0],
             "Volume": [1_000_000] * 3},
            index=benchmark_dates[-3:],
        )

        result = run_backtest(
            {"TEST": stock_bars},
            self.cfg,
            slippage_bps=0,
            benchmark=benchmark,
            strategy=lambda _symbol, history, _cfg: candidate() if len(history) == 1 else None,
        )

        self.assertEqual(result.trades[0].market_regime, "BULL")
        self.assertGreater(result.average_gross_exposure, 0)
        self.assertGreater(result.turnover, 0)
        self.assertEqual(result.market_regime_metrics["BULL"]["trade_count"], 1)

    def test_sector_exposure_and_trailing_pairwise_correlation(self):
        dates = pd.date_range("2025-01-02", periods=25)
        close = pd.Series([10.0 + index * 0.001 for index in range(25)], index=dates)
        bars = pd.DataFrame(
            {"Open": close, "High": close + 0.02, "Low": close - 0.02,
             "Close": close, "Volume": [1_000_000] * len(close)},
            index=dates,
        )

        def strategy(symbol, history, _cfg):
            return candidate(symbol) if history.index[-1] == dates[20] else None

        result = run_backtest(
            {"AAA": bars, "BBB": bars},
            self.cfg,
            slippage_bps=0,
            strategy=strategy,
            sector_map={"AAA": "Financials", "BBB": "Financials"},
        )

        self.assertEqual(len(result.trades), 2)
        self.assertEqual({trade.sector for trade in result.trades}, {"Financials"})
        self.assertGreater(result.sector_exposure_metrics["Financials"]["peak_exposure"], 0)
        self.assertAlmostEqual(result.average_pairwise_correlation, 1.0)
        self.assertAlmostEqual(result.max_pairwise_correlation, 1.0)

    def test_relative_strength_uses_only_signal_date_stock_spy_and_sector_bars(self):
        dates = pd.date_range("2025-01-02", periods=25)
        stock_close = pd.Series([10.0 + index * 0.5 for index in range(25)], index=dates)
        spy_close = pd.Series([100.0 + index for index in range(25)], index=dates)
        sector_close = pd.Series([100.0 + index * 0.5 for index in range(25)], index=dates)
        stock = pd.DataFrame(
            {"Open": stock_close, "High": stock_close + 0.01, "Low": stock_close - 0.01,
             "Close": stock_close, "Volume": [1_000_000] * 25}, index=dates,
        )
        spy = pd.DataFrame({"Open": spy_close, "Close": spy_close}, index=dates)
        xlf = pd.DataFrame({"Close": sector_close}, index=dates)
        future_stock = stock.copy()
        future_stock.loc[dates[21]:, ["Open", "High", "Low", "Close"]] *= 2
        future_spy = spy.copy()
        future_spy.loc[dates[21]:, ["Open", "Close"]] *= 3
        future_xlf = xlf.copy()
        future_xlf.loc[dates[21]:, "Close"] *= 0.5

        def run(stock_history, spy_history, sector_history):
            return run_backtest(
                {"AAA": stock_history},
                self.cfg,
                slippage_bps=0,
                benchmark=spy_history,
                sector_map={"AAA": "Financials"},
                sector_benchmarks={"XLF": sector_history},
                strategy=lambda _symbol, history, _cfg: candidate("AAA") if len(history) == 21 else None,
            )

        original_result = run(stock, spy, xlf)
        original = original_result.trades[0]
        future_changed = run(future_stock, future_spy, future_xlf).trades[0]

        self.assertEqual(original.sector_etf, "XLF")
        self.assertAlmostEqual(original.stock_return_20d, 1.0)
        self.assertAlmostEqual(original.market_return_20d, 0.2)
        self.assertAlmostEqual(original.sector_return_20d, 0.1)
        self.assertAlmostEqual(original.relative_strength_vs_market, 0.8)
        self.assertAlmostEqual(original.relative_strength_vs_sector, 0.9)
        self.assertEqual(original.relative_strength_vs_market, future_changed.relative_strength_vs_market)
        self.assertEqual(original.relative_strength_vs_sector, future_changed.relative_strength_vs_sector)

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "relative-strength.sqlite3"
            run_ids = persist_sensitivity_results(
                [(0.0, original_result)],
                symbols=["AAA"],
                start=dates[0].date().isoformat(),
                end=dates[-1].date().isoformat(),
                slippage_bps=0,
                commission_per_share=0,
                target_fill_mode="trade_through",
                target_trade_through_bps=5,
                path=database_path,
            )
            with closing(sqlite3.connect(database_path)) as database:
                saved_trade = database.execute(
                    "SELECT sector_etf, stock_return_20d, market_return_20d, sector_return_20d, "
                    "relative_strength_vs_market, relative_strength_vs_sector "
                    "FROM backtest_trades WHERE run_id = ?",
                    (run_ids[0],),
                ).fetchone()
                saved_sector_etfs = database.execute(
                    "SELECT sector_etf_mapping_json FROM backtest_runs WHERE run_id = ?",
                    (run_ids[0],),
                ).fetchone()[0]

        self.assertEqual(saved_trade[0], "XLF")
        for actual, expected in zip(saved_trade[1:], (1.0, 0.2, 0.1, 0.8, 0.9)):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(json.loads(saved_sector_etfs), {"Financials": "XLF"})

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

    def test_spread_and_commission_are_charged_on_market_fills(self):
        bars = pd.DataFrame(
            {
                "Open": [10.0, 10.0, 8.0],
                "High": [10.2, 10.05, 8.1],
                "Low": [9.9, 9.8, 7.9],
                "Close": [10.0, 10.0, 8.0],
                "Volume": [1_000_000] * 3,
            },
            index=pd.date_range("2025-01-02", periods=3),
        )
        strategy = lambda _symbol, history, _cfg: candidate() if len(history) == 1 else None

        result = run_backtest(
            {"TEST": bars}, self.cfg, slippage_bps=0, spread_bps=20,
            commission_per_share=0.005, strategy=strategy,
        )

        trade = result.trades[0]
        self.assertAlmostEqual(trade.entry_price, 10.01)
        self.assertAlmostEqual(trade.exit_price, 7.992)
        self.assertAlmostEqual(trade.realized_pnl, -20.28)
        self.assertEqual(trade.holding_sessions, 2)
        self.assertAlmostEqual(result.ending_equity, 979.72)
        self.assertAlmostEqual(result.average_loss, -20.28)
        self.assertAlmostEqual(result.expectancy_dollars, -20.28)
        self.assertEqual(result.max_consecutive_losses, 1)
        self.assertGreater(result.annualized_volatility, 0)
        self.assertIsNotNone(result.sharpe_ratio)
        self.assertIsNotNone(result.sortino_ratio)

    def test_spread_raises_target_trade_through_requirement(self):
        bars = pd.DataFrame(
            {
                "Open": [10.0, 10.0, 10.0],
                "High": [10.2, 10.05, 10.11],
                "Low": [9.9, 9.8, 9.9],
                "Close": [10.0, 10.0, 10.02],
                "Volume": [1_000_000] * 3,
            },
            index=pd.date_range("2025-01-02", periods=3),
        )
        strategy = lambda _symbol, history, _cfg: candidate() if len(history) == 1 else None

        no_spread = run_backtest(
            {"TEST": bars}, self.cfg, slippage_bps=0, spread_bps=0,
            target_trade_through_bps=0, strategy=strategy,
        )
        with_spread = run_backtest(
            {"TEST": bars}, self.cfg, slippage_bps=0, spread_bps=20,
            target_trade_through_bps=0, strategy=strategy,
        )

        self.assertEqual(no_spread.trades[0].exit_reason, "target")
        self.assertEqual(with_spread.trades[0].exit_reason, "end_of_test")

    def test_strategy_cache_is_shared_across_cost_scenarios(self):
        bars = pd.DataFrame(
            {"Open": [10.0] * 3, "High": [10.2] * 3, "Low": [9.9] * 3,
             "Close": [10.0] * 3, "Volume": [1_000_000] * 3},
            index=pd.date_range("2025-01-02", periods=3),
        )
        cache = {}
        calls = []

        def strategy(_symbol, history, _cfg):
            calls.append(history.index[-1])
            return None

        run_backtest({"TEST": bars}, self.cfg, strategy=strategy, strategy_cache=cache)
        run_backtest({"TEST": bars}, self.cfg, spread_bps=25, strategy=strategy, strategy_cache=cache)

        self.assertEqual(len(calls), 2)
        run_backtest(
            {"TEST": bars},
            replace(self.cfg, target_percent=2.0),
            strategy=strategy,
            strategy_cache=cache,
        )
        self.assertEqual(len(calls), 4)

    def test_trade_and_report_result_record_stable_strategy_configuration(self):
        bars = pd.DataFrame(
            {"Open": [10.0, 10.0, 8.0], "High": [10.2, 10.05, 8.1],
             "Low": [9.9, 9.8, 7.9], "Close": [10.0, 10.0, 8.0],
             "Volume": [1_000_000] * 3},
            index=pd.date_range("2025-01-02", periods=3),
        )
        strategy = lambda _symbol, history, _cfg: candidate() if len(history) == 1 else None

        result = run_backtest({"TEST": bars}, self.cfg, slippage_bps=0, strategy=strategy)
        same_hash = configuration_hash(replace(self.cfg))
        changed_hash = configuration_hash(replace(self.cfg, target_percent=2.0))

        self.assertEqual(result.configuration_hash, same_hash)
        self.assertNotEqual(result.configuration_hash, changed_hash)
        self.assertEqual(result.trades[0].configuration_hash, result.configuration_hash)
        self.assertEqual(result.trades[0].strategy_id, result.strategy_id)
        self.assertEqual(result.trades[0].strategy_version, result.strategy_version)
        report = format_spread_sensitivity_report(
            [(5.0, result)],
            run_ids=["test-run-id"],
            database_path="test-backtests.sqlite3",
            symbols=["TEST"],
            start="2025-01-02",
            end="2025-01-04",
            slippage_bps=5,
            commission_per_share=0.005,
            target_fill_mode="trade_through",
            target_trade_through_bps=5,
        )
        self.assertIn(result.strategy_id, report)
        self.assertIn(result.strategy_version, report)
        self.assertIn(result.configuration_hash, report)
        self.assertIn('"target_percent": 1', report)
        self.assertIn("test-run-id", report)
        self.assertIn("test-backtests.sqlite3", report)
        self.assertIn("## Annualized and Risk-Adjusted Metrics", report)
        self.assertIn("## Trade Distribution", report)
        self.assertIn("## Market Regime Performance", report)
        self.assertIn("## Sector Exposure", report)
        self.assertIn("## Open-Position Correlation", report)
        self.assertIn("## Relative Strength", report)
        self.assertIn("Sector ETF mapping:", report)
        self.assertIn("Avg gross exposure", report)

    def test_sensitivity_runs_trades_and_equity_persist_to_sqlite(self):
        bars = pd.DataFrame(
            {"Open": [10.0, 10.0, 10.0], "High": [10.2, 10.05, 10.2],
             "Low": [9.9, 9.8, 9.9], "Close": [10.0, 10.0, 10.1],
             "Volume": [1_000_000] * 3},
            index=pd.date_range("2025-01-02", periods=3),
        )
        result = run_backtest(
            {"TEST": bars},
            self.cfg,
            slippage_bps=0,
            strategy=lambda _symbol, history, _cfg: candidate() if len(history) == 1 else None,
        )

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "backtests.sqlite3"
            run_ids = persist_sensitivity_results(
                [(0.0, result), (5.0, result)],
                symbols=["TEST"],
                start="2025-01-02",
                end="2025-01-04",
                slippage_bps=5,
                commission_per_share=0.005,
                target_fill_mode="trade_through",
                target_trade_through_bps=5,
                path=database_path,
            )

            with closing(sqlite3.connect(database_path)) as database:
                runs = database.execute(
                    "SELECT run_id, spread_bps, configuration_hash, average_gross_exposure, turnover, market_regime_json "
                    ", sector_mapping_json, sector_exposure_json, average_pairwise_correlation "
                    "FROM backtest_runs ORDER BY spread_bps"
                ).fetchall()
                trade_count = database.execute("SELECT COUNT(*) FROM backtest_trades").fetchone()[0]
                market_regimes = database.execute("SELECT DISTINCT market_regime FROM backtest_trades").fetchall()
                trade_sectors = database.execute("SELECT DISTINCT sector FROM backtest_trades").fetchall()
                equity_count = database.execute("SELECT COUNT(*) FROM backtest_equity").fetchone()[0]
                foreign_key_errors = database.execute("PRAGMA foreign_key_check").fetchall()

        self.assertEqual(len(run_ids), 2)
        self.assertEqual([row[0] for row in runs], run_ids)
        self.assertEqual([row[1] for row in runs], [0.0, 5.0])
        self.assertEqual({row[2] for row in runs}, {result.configuration_hash})
        self.assertTrue(all(row[3] >= 0 and row[4] >= 0 for row in runs))
        self.assertEqual(json.loads(runs[0][5])["UNKNOWN"]["profit_factor"], "inf")
        self.assertEqual(json.loads(runs[0][6]), {"TEST": "Unclassified"})
        self.assertIn("Unclassified", json.loads(runs[0][7]))
        self.assertIsNone(runs[0][8])
        self.assertEqual(trade_count, len(result.trades) * 2)
        self.assertEqual(market_regimes, [("UNKNOWN",)])
        self.assertEqual(trade_sectors, [("Unclassified",)])
        self.assertEqual(equity_count, len(result.equity_curve) * 2)
        self.assertEqual(foreign_key_errors, [])

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
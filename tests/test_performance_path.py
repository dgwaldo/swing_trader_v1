import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pandas as pd

import main
from swingtrader.config import TradingConfig
from swingtrader.data import download_batch, download_latest_prices
from swingtrader.scanner import TradeCandidate, analyze, evaluate_candidate, refresh_candidate_price
from swingtrader.sentiment import SentimentResult


class ScannerPerformancePathTests(unittest.TestCase):
    def test_technical_candidate_evaluation_is_pure_and_matches_neutral_analyze(self):
        close = np.linspace(10.0, 20.0, 240)
        history = pd.DataFrame(
            {
                "Open": close,
                "High": close,
                "Low": close - 0.2,
                "Close": close,
                "Volume": [1_000_000] * len(close),
            },
            index=pd.date_range("2025-01-02", periods=len(close)),
        )
        config = TradingConfig()

        with patch("swingtrader.scanner.get_sentiment", side_effect=AssertionError("unexpected network lookup")):
            first = evaluate_candidate("TEST", history, config)
            second = evaluate_candidate("TEST", history, config)

        if first is None or second is None:
            self.fail("Expected technical candidate from the synthetic uptrend")
        self.assertEqual(first, second)
        neutral = analyze(
            "TEST",
            history,
            config,
            sentiment_fn=lambda _symbol: SentimentResult(0.0, 0, None, "Technical Trend"),
        )
        self.assertEqual(first, neutral)
        enriched = analyze(
            "TEST",
            history,
            config,
            sentiment_fn=lambda _symbol: SentimentResult(0.8, 1, "Positive headline", "Company News"),
        )
        if enriched is None:
            self.fail("Expected sentiment-enriched candidate")
        self.assertEqual(enriched.score, first.score + config.sentiment_bonus_score)
        self.assertEqual(enriched.sentiment_headline, "Positive headline")

    def test_candidate_table_header_matches_separator(self):
        with redirect_stdout(io.StringIO()) as output:
            main.print_candidates_table([])

        rows = [line for line in output.getvalue().splitlines() if line.startswith("|")]
        self.assertEqual(rows[0].count("|"), rows[1].count("|"))

    @patch("main.run_scanner")
    def test_cli_saves_each_run_as_markdown(self, mock_run_scanner):
        mock_run_scanner.side_effect = lambda **kwargs: print("\n## Candidates\n\n| Symbol |\n|---|\n| TEST |")

        with TemporaryDirectory() as report_dir, patch("main.REPORT_DIR", Path(report_dir)):
            with patch("main.sys.argv", ["main.py"]), redirect_stdout(io.StringIO()) as output:
                main.main()
                main.main()

            reports = list(Path(report_dir).glob("*.md"))
            self.assertEqual(len(reports), 2)
            for report in reports:
                contents = report.read_text(encoding="utf-8")
                self.assertIn("## Candidates", contents)
                self.assertIn("| TEST |", contents)
                self.assertIn("Total application runtime:", contents)
            self.assertIn("| TEST |", output.getvalue())

    @patch("main.print_candidates_table")
    @patch("main.save_scan")
    @patch("main.analyze", return_value=None)
    @patch("main.download_batch")
    def test_scanner_reuses_daily_history_price(
        self,
        mock_download_batch,
        mock_analyze,
        _mock_save_scan,
        _mock_print_candidates_table,
    ):
        history = pd.DataFrame({"Close": [10.0]})
        mock_download_batch.return_value = {"TEST": history}
        config = TradingConfig()

        main.run_scanner(symbols=["TEST"], output_path="unused.json", cfg=config)

        mock_download_batch.assert_called_once_with(["TEST"], period="1y", batch_size=80)
        mock_analyze.assert_called_once_with("TEST", history, config)

    @patch("swingtrader.data.yf.download", return_value=pd.DataFrame())
    def test_history_download_uses_yfinance_threads(self, mock_download):
        with TemporaryDirectory() as cache_dir:
            download_batch(["TEST"], cache_dir=cache_dir)

        self.assertTrue(mock_download.call_args.kwargs["threads"])

    @patch("swingtrader.data.yf.download")
    def test_warm_history_cache_skips_download(self, mock_download):
        history = pd.DataFrame(
            {
                "Open": range(30),
                "High": range(1, 31),
                "Low": range(30),
                "Close": range(1, 31),
                "Volume": [1_000_000] * 30,
            },
            index=pd.date_range("2026-08-01", periods=30),
        )
        mock_download.return_value = history

        with TemporaryDirectory() as cache_dir:
            first = download_batch(["TEST"], cache_dir=cache_dir)
            second = download_batch(["TEST"], cache_dir=cache_dir)

        self.assertEqual(mock_download.call_count, 1)
        pd.testing.assert_frame_equal(first["TEST"], second["TEST"])

    @patch("swingtrader.data.yf.download")
    def test_latest_prices_include_extended_hours(self, mock_download):
        mock_download.return_value = pd.DataFrame({"Close": [10.0, 10.75]})

        prices = download_latest_prices(["TEST"])

        self.assertEqual(prices, {"TEST": 10.75})
        self.assertEqual(mock_download.call_args.kwargs["interval"], "1m")
        self.assertTrue(mock_download.call_args.kwargs["prepost"])
        self.assertTrue(mock_download.call_args.kwargs["threads"])

    @patch("swingtrader.data.yf.Ticker")
    @patch("swingtrader.data.yf.download", return_value=pd.DataFrame())
    def test_latest_prices_retry_symbols_missing_from_batch(self, _mock_download, mock_ticker):
        mock_ticker.return_value.history.return_value = pd.DataFrame(
            {"Close": [10.0, 10.5]}
        )

        prices = download_latest_prices(["TEST"])

        self.assertEqual(prices, {"TEST": 10.5})
        mock_ticker.return_value.history.assert_called_once_with(
            period="1d",
            interval="1m",
            prepost=True,
            auto_adjust=True,
        )

    def test_latest_price_recalculates_risk_plan(self):
        candidate = TradeCandidate(
            symbol="TEST",
            score=70,
            prob_gain_10d=0.4,
            prob_loss_5d=0.2,
            risk_rating="Medium",
            entry=10.0,
            stop=8.5,
            target_1pct=10.1,
            target_1=12.25,
            target_2=14.5,
            shares=6,
            risk_dollars=9.0,
            reward_risk=1.5,
            main_catalyst="Technical Trend",
            setup="Momentum",
            reasons=[],
            reference_close=10.0,
        )

        refreshed = refresh_candidate_price(candidate, 12.0, TradingConfig())

        self.assertIsNotNone(refreshed)
        self.assertAlmostEqual(refreshed.entry, 12.0)
        self.assertAlmostEqual(refreshed.stop, 10.5)
        self.assertAlmostEqual(refreshed.target_1, 14.25)
        self.assertEqual(refreshed.shares, 6)
        self.assertAlmostEqual(refreshed.risk_dollars, 9.0)
        self.assertAlmostEqual(refreshed.price_change_pct, 0.2)


if __name__ == "__main__":
    unittest.main()
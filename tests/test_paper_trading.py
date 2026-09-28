import ast
import io
import unittest
from contextlib import redirect_stdout
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
from swingtrader.config import TradingConfig, load_trading_config
from swingtrader.scanner import TradeCandidate
from swingtrader.paper_trading import load_paper_credentials, plan_paper_order, review_paper_orders


class PaperTradingTests(unittest.TestCase):
    def test_paper_status_does_not_scan_or_submit(self):
        with TemporaryDirectory() as directory, patch("main.REPORT_DIR", Path(directory)):
            with patch("main.sys.argv", ["main.py", "--paper-status"]), \
                    patch("main.run_scanner") as scan, \
                    patch("swingtrader.paper_trading.report_paper_status") as status, \
                    redirect_stdout(io.StringIO()):
                main.main()
        scan.assert_not_called()
        status.assert_called_once_with(cancel_stale=False)

    def test_only_expired_unfilled_paper_entry_is_canceled(self):
        now = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
        def entry(order_id, filled_qty, age_minutes, status="new"):
            return SimpleNamespace(
                id=order_id, client_order_id=f"swing-paper-TEST-{order_id}", symbol="TEST",
                side="buy", type="limit", order_class="bracket", status=status,
                filled_qty=filled_qty, created_at=now - timedelta(minutes=age_minutes), legs=None,
            )

        trading = Mock()
        messages = review_paper_orders(
            trading, [entry("old", "0", 11), entry("partial", "1", 11, "partially_filled"),
                      entry("new", "0", 3), entry("filled", "2", 11, "filled")], now, cancel_stale=True,
        )

        trading.cancel_order_by_id.assert_called_once_with("old")
        self.assertTrue(any("partial" in message and "manual" in message for message in messages))
        self.assertTrue(any("filled" in message and "manual" in message for message in messages))

    def test_status_review_does_not_cancel_stale_entries(self):
        now = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
        entry = SimpleNamespace(
            id="old", client_order_id="swing-paper-TEST-old", symbol="TEST",
            side="buy", type="limit", order_class="bracket", status="new",
            filled_qty="0", created_at=now - timedelta(minutes=11), legs=None,
        )
        trading = Mock()

        messages = review_paper_orders(trading, [entry], now)

        trading.cancel_order_by_id.assert_not_called()
        self.assertTrue(any("old" in message and "stale" in message for message in messages))

    def test_example_covers_all_trading_settings(self):
        example = Path(__file__).resolve().parent.parent / "config.example.py"
        tree = ast.parse(example.read_text(encoding="utf-8"))
        config_call = next(
            assignment.value for assignment in tree.body
            if isinstance(assignment, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "TRADING_CONFIG" for target in assignment.targets)
        )
        self.assertIsInstance(config_call, ast.Call)
        self.assertEqual(
            {field.name for field in fields(TradingConfig)},
            {keyword.arg for keyword in config_call.keywords},
        )
        settings = run_path(str(example))
        self.assertIsInstance(settings["TRADING_CONFIG"], TradingConfig)
        self.assertEqual(settings["APCA_API_BASE_URL"], "https://paper-api.alpaca.markets")
        self.assertEqual(settings["APCA_API_KEY_ID"], "")
        self.assertEqual(settings["APCA_API_SECRET_KEY"], "")

    def test_loads_local_trading_settings(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.py"
            path.write_text(
                "from swingtrader.config import TradingConfig\n"
                "TRADING_CONFIG = TradingConfig(account_size=2000.0, risk_fraction=0.005)\n",
                encoding="utf-8",
            )
            config = load_trading_config(path)
            self.assertEqual(config.account_size, 2000.0)
            self.assertEqual(config.risk_fraction, 0.005)

    def test_cli_uses_local_trading_settings(self):
        with patch("main.load_trading_config", return_value=TradingConfig(minimum_price=8.0, risk_fraction=0.005)):
            config = main.build_config(maximum_price=20.0)
        self.assertEqual(config.minimum_price, 8.0)
        self.assertEqual(config.maximum_price, 20.0)
        self.assertEqual(config.risk_fraction, 0.005)

    def test_loads_paper_credentials_from_local_config(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.py"
            path.write_text("APCA_API_KEY_ID = 'paper-key'\nAPCA_API_SECRET_KEY = 'paper-secret'\n", encoding="utf-8")

            self.assertEqual(load_paper_credentials(path), ("paper-key", "paper-secret"))

    def test_rejects_non_paper_base_url(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.py"
            path.write_text(
                "APCA_API_BASE_URL = 'https://api.alpaca.markets'\n"
                "APCA_API_KEY_ID = 'paper-key'\nAPCA_API_SECRET_KEY = 'paper-secret'\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "paper endpoint"):
                load_paper_credentials(path)

    def test_wide_spread_is_rejected(self):
        candidate = TradeCandidate(
            symbol="TEST", score=70, prob_gain_10d=0.5, prob_loss_5d=0.2,
            risk_rating="Medium", entry=10.0, stop=9.0, target_1pct=10.1,
            target_1=11.5, target_2=13.0, shares=10, risk_dollars=10.0,
            reward_risk=1.5, main_catalyst="Technical Trend", setup="Momentum",
            reasons=[],
        )

        with self.assertRaisesRegex(ValueError, "spread"):
            plan_paper_order(candidate, bid=9.50, ask=10.50, cfg=TradingConfig())


if __name__ == "__main__":
    unittest.main()
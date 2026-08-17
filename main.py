import argparse
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from swingtrader.backtest import run as run_backtest
from swingtrader.config import TradingConfig
from swingtrader.data import discover_symbols, download
from swingtrader.scanner import analyze


FALLBACK_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL",
    "AVGO", "AMD", "NFLX", "TSLA", "PLTR", "ORCL",
    "CRM", "MU", "QCOM", "COST", "JPM", "XOM",
    "SOFI", "PLTR", "F", "PFE", "INTC", "SNAP", "RIVN",
    "NU", "VALE", "T", "BAC", "GM", "CCL", "NCLH", "DKNG",
    "HOOD", "MARA", "RKLB", "SIRI", "TGT"
]


def save_scan(candidates, symbols, output_path):
    payload = {
        "scan_date": date.today().isoformat(),
        "symbols_scanned": symbols,
        "candidates": [asdict(candidate) for candidate in candidates],
    }

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Saved scan snapshot to {path}")


def print_candidates_table(candidates):
    print("\n## Swing Trade Candidates\n")
    print(
        "| Symbol | Score | Setup | Entry | Stop | Target | Shares | Risk | R:R | "
        "Sentiment | Reasons |"
    )
    print("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|")

    for candidate in candidates:
        reasons = "; ".join(candidate.reasons)
        print(
            f"| {candidate.symbol} | {candidate.score} | {candidate.setup} | "
            f"${candidate.entry:.2f} | ${candidate.stop:.2f} | "
            f"${candidate.target:.2f} | {candidate.shares} | "
            f"${candidate.risk_dollars:.2f} | {candidate.reward_risk:.1f} | "
            f"{candidate.sentiment_score:+.2f} | {reasons} |"
        )


def scan(symbols=None, output_path=None, top=15):
    cfg = TradingConfig()
    if not symbols:
        symbols = discover_symbols(
            minimum_price=cfg.minimum_price,
            minimum_volume=cfg.minimum_average_volume,
        ) or FALLBACK_SYMBOLS
        print(f"Scanning {len(symbols)} Yahoo-discovered symbols...")

    candidates = []

    for symbol in symbols:
        print(f"Scanning {symbol}...")
        try:
            data = download(symbol)
            candidate = analyze(symbol, data, cfg)
            if candidate:
                candidates.append(candidate)
        except Exception as exc:
            print(f"  ERROR: {exc}")

    candidates.sort(key=lambda x: (x.score, x.reward_risk), reverse=True)
    if top:
        candidates = candidates[:top]
    if output_path is None:
        output_path = Path("data") / "scans" / f"scan_{date.today().isoformat()}.json"
    save_scan(candidates, symbols, output_path)

    if not candidates:
        print("No candidates met the V1 criteria.")
        return

    print_candidates_table(candidates)


def backtest(symbols):
    cfg = TradingConfig()
    if isinstance(symbols, str):
        symbols = [symbols]

    print(f"\n=== BACKTEST ({len(symbols)} SYMBOLS) ===")
    for symbol in symbols:
        print(f"\nDownloading {symbol}...")
        try:
            data = download(symbol, period="5y")
            result = run_backtest(symbol, data, cfg)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            continue

        print(f"Symbol:       {result.symbol}")
        print(f"Trades:       {result.trades}")
        print(f"Wins:         {result.wins}")
        print(f"Losses:       {result.losses}")
        print(f"Win rate:     {result.win_rate:.1f}%")
        print(f"Total return: {result.total_return:.2f}%")
        print(f"Max drawdown: {result.max_drawdown:.2f}%")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    scan_parser = sub.add_parser("scan")
    scan_parser.add_argument("--symbols", nargs="+")
    scan_parser.add_argument("--output", help="Path for the JSON scan snapshot")
    scan_parser.add_argument("--top", type=int, default=15, help="Max candidates to keep (0 = no limit)")

    bt_parser = sub.add_parser("backtest")
    bt_symbols = bt_parser.add_mutually_exclusive_group(required=True)
    bt_symbols.add_argument("--symbol")
    bt_symbols.add_argument("--symbols", nargs="+")

    args = parser.parse_args()

    if args.command == "scan":
        scan(args.symbols, args.output, args.top)
    elif args.command == "backtest":
        backtest(args.symbols or args.symbol)

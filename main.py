import argparse
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from swingtrader.backtest import run as run_backtest
from swingtrader.config import TradingConfig
from swingtrader.data import current_price, discover_symbols, download
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
            live_price = current_price(symbol)
            candidate = analyze(symbol, data, cfg, current_price=live_price)
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
        return []

    print_candidates_table(candidates)
    return candidates


def historical_fit(result):
    if result.trades < 10:
        return "Limited data"
    if result.total_return > 0 and result.max_drawdown <= 5:
        return "Strong"
    return "Weak"


def print_backtest_table(results):
    print("\n## Backtest Results\n")
    print("| Symbol | Trades | Wins | Losses | Win Rate | Total Return | Max Drawdown | Historical Fit |")
    print("|---|---:|---:|---:|---:|---:|---:|---|")

    for result in results:
        print(
            f"| {result.symbol} | {result.trades} | {result.wins} | "
            f"{result.losses} | {result.win_rate:.1f}% | "
            f"{result.total_return:+.2f}% | {result.max_drawdown:.2f}% | "
            f"{historical_fit(result)} |"
        )


def backtest(symbols):
    cfg = TradingConfig()
    if isinstance(symbols, str):
        symbols = [symbols]

    print(f"\n=== BACKTEST ({len(symbols)} SYMBOLS) ===")
    results = []
    for symbol in symbols:
        print(f"\nDownloading {symbol}...")
        try:
            data = download(symbol, period="5y")
            result = run_backtest(symbol, data, cfg)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            continue

        results.append(result)

    if not results:
        print("No backtest results were produced.")
        return []

    fit_rank = {"Strong": 0, "Limited data": 1, "Weak": 2}
    results.sort(key=lambda result: (fit_rank[historical_fit(result)], -result.total_return))
    print_backtest_table(results)
    return results


def print_evening_table(candidates, results):
    results_by_symbol = {result.symbol: result for result in results}

    print("\n## Evening Swing Trade Grid\n")
    print(
        "| Rank | Symbol | Score | Setup | Entry | Stop | Target | Shares | "
        "Risk | R:R | Backtest Trades | Win Rate | Return | Drawdown | Fit |"
    )
    print("|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")

    rows = []
    for rank, candidate in enumerate(candidates, start=1):
        result = results_by_symbol.get(candidate.symbol)
        if result is None:
            continue

        fit = historical_fit(result)
        print(
            f"| {rank} | {candidate.symbol} | {candidate.score} | {candidate.setup} | "
            f"${candidate.entry:.2f} | ${candidate.stop:.2f} | ${candidate.target:.2f} | "
            f"{candidate.shares} | ${candidate.risk_dollars:.2f} | "
            f"{candidate.reward_risk:.1f} | {result.trades} | {result.win_rate:.1f}% | "
            f"{result.total_return:+.2f}% | {result.max_drawdown:.2f}% | {fit} |"
        )
        rows.append({
            "rank": rank,
            "candidate": asdict(candidate),
            "backtest": asdict(result),
            "historical_fit": fit,
        })

    if len(rows) < len(candidates):
        print(f"\nBacktest results unavailable for {len(candidates) - len(rows)} pick(s).")

    return rows


def build_focus_analysis(rows):
    """Create an explainable English focus brief from the evening grid."""
    if not rows:
        return {
            "headline": "No actionable picks were found tonight.",
            "focus": [],
            "text": "No candidates had both a current scan signal and a completed backtest.",
        }

    strong = [row for row in rows if row["historical_fit"] == "Strong"]
    primary = sorted(
        strong,
        key=lambda row: (
            row["candidate"]["score"],
            row["backtest"]["total_return"],
            -row["backtest"]["max_drawdown"],
        ),
        reverse=True,
    )[:3]
    primary_symbols = [row["candidate"]["symbol"] for row in primary]

    if primary:
        focus_text = ", ".join(primary_symbols)
        headline = f"Focus first on {focus_text}."
        sentences = [
            f"For the next session, focus first on {focus_text}. "
            "These names combine a qualifying live setup with a Strong historical fit."
        ]
    else:
        headline = "No top-tier buy focus tonight."
        sentences = [
            "No pick combines a qualifying live setup with a Strong historical fit, "
            "so treat tonight's list as a watchlist rather than a buy list."
        ]

    secondary = [
        row["candidate"]["symbol"]
        for row in rows
        if row["candidate"]["symbol"] not in primary_symbols
    ][:3]
    if secondary:
        sentences.append(
            f"Keep {', '.join(secondary)} as secondary watchlist names, "
            "but review their entry price and historical results before placing orders."
        )

    weak = [
        row["candidate"]["symbol"]
        for row in rows
        if row["historical_fit"] == "Weak"
    ]
    if weak:
        sentences.append(
            f"Be cautious with {', '.join(weak)} because the backtest is classified as Weak."
        )

    limited = [
        row["candidate"]["symbol"]
        for row in rows
        if row["historical_fit"] == "Limited data"
    ]
    if limited:
        sentences.append(
            f"Treat {', '.join(limited)} cautiously because the backtest has limited data."
        )

    sentences.append(
        "Before buying, confirm the next-day bid/ask, use a limit entry at or below the "
        "planned entry, and recalculate shares from the actual stop distance."
    )

    return {
        "headline": headline,
        "focus": primary_symbols,
        "text": " ".join(sentences),
    }


def evening(symbols=None, output_path=None):
    """Create an after-hours top-10 scan, backtest those picks, and print one grid."""
    candidates = scan(symbols=symbols, top=10)
    if not candidates:
        return

    results = backtest([candidate.symbol for candidate in candidates])
    rows = print_evening_table(candidates, results)
    analysis = build_focus_analysis(rows)

    print("\n## Buying Focus\n")
    print(f"**{analysis['headline']}**")
    print(analysis["text"])

    if output_path is None:
        output_path = Path("data") / "scans" / f"evening_{date.today().isoformat()}.json"

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "report_date": date.today().isoformat(),
                "picks": rows,
                "buying_focus": analysis,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Saved evening report to {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    scan_parser = sub.add_parser("scan")
    scan_parser.add_argument("--symbols", nargs="+")
    scan_parser.add_argument("--output", help="Path for the JSON scan snapshot")
    scan_parser.add_argument("--top", type=int, default=15, help="Max candidates to keep (0 = no limit)")

    evening_parser = sub.add_parser(
        "evening",
        help="Scan, backtest the top 10 picks, and print a combined grid",
    )
    evening_parser.add_argument("--symbols", nargs="+", help="Optional symbols to scan")
    evening_parser.add_argument("--output", help="Path for the combined evening report")

    bt_parser = sub.add_parser("backtest")
    bt_symbols = bt_parser.add_mutually_exclusive_group(required=True)
    bt_symbols.add_argument("--symbol")
    bt_symbols.add_argument("--symbols", nargs="+")

    args = parser.parse_args()

    if args.command == "scan":
        scan(args.symbols, args.output, args.top)
    elif args.command == "evening":
        evening(args.symbols, args.output)
    elif args.command == "backtest":
        backtest(args.symbols or args.symbol)

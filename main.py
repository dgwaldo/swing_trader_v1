import argparse
import json
from dataclasses import asdict, replace
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


def build_config(minimum_price=None, maximum_price=None):
    cfg = TradingConfig()
    minimum_price = cfg.minimum_price if minimum_price is None else minimum_price
    maximum_price = cfg.maximum_price if maximum_price is None else maximum_price

    if minimum_price > maximum_price:
        raise ValueError("Minimum price cannot be greater than maximum price.")

    return replace(cfg, minimum_price=minimum_price, maximum_price=maximum_price)


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


def scan(symbols=None, output_path=None, top=15, cfg=None):
    cfg = cfg or TradingConfig()
    if not symbols:
        symbols = discover_symbols(
            minimum_price=cfg.minimum_price,
            maximum_price=cfg.maximum_price,
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


def rank_evening_candidates(candidates, results):
    """Order picks by live setup quality, then historical evidence."""
    results_by_symbol = {result.symbol: result for result in results}
    fit_rank = {"Strong": 0, "Limited data": 1, "Weak": 2}

    return sorted(
        candidates,
        key=lambda candidate: (
            candidate.score,
            -fit_rank.get(
                historical_fit(results_by_symbol[candidate.symbol]),
                len(fit_rank),
            ) if candidate.symbol in results_by_symbol else -len(fit_rank),
            results_by_symbol[candidate.symbol].total_return
            if candidate.symbol in results_by_symbol else float("-inf"),
            results_by_symbol[candidate.symbol].win_rate
            if candidate.symbol in results_by_symbol else float("-inf"),
            -results_by_symbol[candidate.symbol].max_drawdown
            if candidate.symbol in results_by_symbol else float("-inf"),
            results_by_symbol[candidate.symbol].trades
            if candidate.symbol in results_by_symbol else -1,
        ),
        reverse=True,
    )


def backtest(symbols, cfg=None):
    cfg = cfg or TradingConfig()
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

    ranked_symbols = [row["candidate"]["symbol"] for row in rows]
    best = ranked_symbols[0]
    second = ranked_symbols[1] if len(ranked_symbols) > 1 else None
    headline = f"Best overall bet: {best}."
    sentences = [
        f"Rank 1 is {best}, followed by {second}. The ranking starts with live setup "
        "quality and uses historical fit, return, win rate, drawdown, and trade count "
        "to break ties." if second else
        f"Rank 1 is {best}. The ranking combines live setup quality with historical results."
    ]

    strong = [
        row["candidate"]["symbol"]
        for row in rows
        if row["historical_fit"] == "Strong"
    ]
    if strong:
        sentences.append(
            f"{', '.join(strong)} {('have' if len(strong) > 1 else 'has')} the most "
            "established backtest support."
        )
    else:
        sentences.append(
            "No pick has the most established backtest support, so treat the list as a "
            "watchlist rather than a buy list."
        )

    secondary = [
        row["candidate"]["symbol"]
        for row in rows
        if row["candidate"]["symbol"] not in {best, second}
    ][:3]
    if secondary:
        sentences.append(
            f"Keep {', '.join(secondary)} as the next watchlist names, but review their "
            "entry price and historical results before placing orders."
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
        "focus": ranked_symbols[:3],
        "text": " ".join(sentences),
    }


def evening(symbols=None, output_path=None, cfg=None):
    """Create an after-hours top-10 scan, backtest those picks, and print one grid."""
    cfg = cfg or TradingConfig()
    candidates = scan(symbols=symbols, top=10, cfg=cfg)
    if not candidates:
        return

    results = backtest([candidate.symbol for candidate in candidates], cfg=cfg)
    candidates = rank_evening_candidates(candidates, results)
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
    scan_parser.add_argument("--min-price", type=float, help="Minimum stock price to include")
    scan_parser.add_argument("--max-price", type=float, help="Maximum stock price to include")

    evening_parser = sub.add_parser(
        "evening",
        help="Scan, backtest the top 10 picks, and print a combined grid",
    )
    evening_parser.add_argument("--symbols", nargs="+", help="Optional symbols to scan")
    evening_parser.add_argument("--output", help="Path for the combined evening report")
    evening_parser.add_argument("--min-price", type=float, help="Minimum stock price to include")
    evening_parser.add_argument("--max-price", type=float, help="Maximum stock price to include")

    bt_parser = sub.add_parser("backtest")
    bt_symbols = bt_parser.add_mutually_exclusive_group(required=True)
    bt_symbols.add_argument("--symbol")
    bt_symbols.add_argument("--symbols", nargs="+")

    args = parser.parse_args()

    if args.command == "scan":
        scan(args.symbols, args.output, args.top, cfg=build_config(args.min_price, args.max_price))
    elif args.command == "evening":
        evening(args.symbols, args.output, cfg=build_config(args.min_price, args.max_price))
    elif args.command == "backtest":
        backtest(args.symbols or args.symbol)

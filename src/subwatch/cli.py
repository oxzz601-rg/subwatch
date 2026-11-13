"""The ``subwatch`` command line."""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

from . import __version__
from .core import find_recurring, load_transactions, summarize


def render_markdown(subscriptions, totals, files, window=None):
    lines = [
        "# subwatch report",
        "",
        "Read {} file(s){}. {} recurring charge(s) found.".format(
            len(files), " for the last {} months".format(window) if window else "", totals["count"]
        ),
        "",
        "- estimated spend: **{:.2f} per month**, **{:.2f} per year**".format(
            totals["per_month"], totals["per_year"]
        ),
    ]
    if totals["price_increases"]:
        lines.append("- price went up: {}".format(", ".join(totals["price_increases"])))
    lines += [
        "",
        "| merchant | cadence | amount | per year | last charged | next expected | change |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in subscriptions:
        change = "+{:.2f}".format(item.price_change) if item.price_change > 0 else ""
        lines.append(
            "| {} | {} | {:.2f} | {:.2f} | {} | {} | {} |".format(
                item.merchant.replace("|", "/"),
                item.cadence,
                item.amount,
                item.annual_cost,
                item.last_seen.isoformat(),
                item.next_expected.isoformat(),
                change,
            )
        )
    if totals["price_increases"]:
        lines += ["", "## Price increases", ""]
        for item in subscriptions:
            if item.price_change > 0:
                lines.append(
                    "- **{}**: now {:.2f} (up {:.2f}, i.e. {:.2f} more per year)".format(
                        item.merchant, item.amount, item.price_change, item.price_change * 365.0 / {
                            "weekly": 7, "fortnightly": 14, "monthly": 30, "quarterly": 91, "yearly": 365
                        }.get(item.cadence, 30)
                    )
                )
    lines += ["", "Check anything you do not recognise before cancelling: some are annual renewals.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="subwatch",
        description="Find recurring subscriptions and price hikes in bank CSV exports.",
    )
    parser.add_argument("files", nargs="+", help="one or more exported CSV statements")
    parser.add_argument("--min-occurrences", type=int, default=3, help="charges needed to count (default: 3)")
    parser.add_argument("--min-amount", type=float, default=0.0, help="ignore charges below this amount")
    parser.add_argument("--months", type=int, help="only consider the last N months of the export")
    parser.add_argument("--day-first", action="store_true", help="read ambiguous dates as DD/MM (default: MM/DD)")
    parser.add_argument("--invert", action="store_true", help="flip the sign, for exports where spending is positive")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a markdown report")
    parser.add_argument("-o", "--output", help="write the report to a file")
    parser.add_argument("--version", action="version", version="subwatch {}".format(__version__))
    args = parser.parse_args(argv)

    try:
        transactions = load_transactions(args.files, day_first=args.day_first or None, invert=args.invert)
    except (OSError, ValueError) as exc:
        print("subwatch: {}".format(exc), file=sys.stderr)
        return 2
    if not transactions:
        print("subwatch: no transactions could be read from those files", file=sys.stderr)
        return 2

    window = args.months
    if window:
        latest = max(item.date for item in transactions)
        cutoff = latest - datetime.timedelta(days=int(window * 30.44))
        transactions = [item for item in transactions if item.date >= cutoff]

    subscriptions = find_recurring(
        transactions, min_occurrences=args.min_occurrences, min_amount=args.min_amount
    )
    totals = summarize(subscriptions)

    if args.json:
        payload = {
            "files": args.files,
            "transactions": len(transactions),
            "summary": totals,
            "subscriptions": [
                {
                    "merchant": item.merchant,
                    "key": item.key,
                    "cadence": item.cadence,
                    "amount": item.amount,
                    "count": item.count,
                    "first_seen": item.first_seen.isoformat(),
                    "last_seen": item.last_seen.isoformat(),
                    "next_expected": item.next_expected.isoformat(),
                    "annual_cost": item.annual_cost,
                    "price_change": item.price_change,
                    "confidence": item.confidence,
                }
                for item in subscriptions
            ],
        }
        print(json.dumps(payload, indent=2))
        return 0

    report = render_markdown(subscriptions, totals, args.files, window=window)
    if args.output:
        pathlib.Path(args.output).write_text(report, encoding="utf-8")
        print("wrote {}".format(args.output), file=sys.stderr)
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

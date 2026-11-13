"""Read bank/card CSV exports and find charges that repeat on a schedule.

The hard part is not the maths, it is the input: every bank names its columns
differently, writes dates and decimals differently, and pads the statement with
preamble lines. Everything here is offline and stdlib only.
"""

from __future__ import annotations

import csv
import datetime
import pathlib
import re
import statistics
from dataclasses import dataclass, field

DATE_KEYS = (
    "transaction date", "date", "posted", "posting date", "booked", "value date",
    "datum", "buchungstag", "valuta", "valutadatum", "date de operation",
)
DESC_KEYS = (
    "description", "payee", "merchant", "details", "narrative", "memo", "reference",
    "buchungstext", "verwendungszweck", "transaction", "name", "label", "text",
)
AMOUNT_KEYS = ("amount", "betrag", "transaction amount", "value", "sum", "montant")
DEBIT_KEYS = ("debit", "withdrawal", "paid out", "soll", "ausgang", "retrait")
CREDIT_KEYS = ("credit", "deposit", "paid in", "haben", "eingang", "depot")

MONTHS = {
    name: index
    for index, name in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1
    )
}

NOISE = re.compile(
    r"\b(pos|purchase|payment|pmt|card|debit|credit|visa|mastercard|amex|online|www|com|inc|ltd|limited|llc|usa|gbr|ire|sepa|direct|ref|txn|transaction|id|order|store)\b"
)

CADENCES = (
    ("weekly", 7, 2),
    ("fortnightly", 14, 3),
    ("monthly", 30, 5),
    ("quarterly", 91, 10),
    ("yearly", 365, 30),
)


@dataclass
class Transaction:
    date: datetime.date
    description: str
    amount: float
    source: str = ""

    @property
    def is_charge(self):
        return self.amount < 0

    def to_dict(self):
        return {
            "date": self.date.isoformat(),
            "description": self.description,
            "amount": round(self.amount, 2),
            "source": self.source,
        }


@dataclass
class Subscription:
    merchant: str
    key: str
    cadence: str
    amount: float
    count: int
    first_seen: datetime.date
    last_seen: datetime.date
    next_expected: datetime.date
    annual_cost: float
    price_change: float = 0.0
    confidence: float = 0.0
    samples: tuple = field(default=())

    def to_dict(self):
        return {
            "merchant": self.merchant,
            "key": self.key,
            "cadence": self.cadence,
            "amount": self.amount,
            "count": self.count,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "next_expected": self.next_expected.isoformat(),
            "annual_cost": self.annual_cost,
            "price_change": self.price_change,
            "confidence": self.confidence,
        }


def parse_amount(value):
    """Parse ``-15.49``, ``15,49``, ``1.234,56``, ``(12.00)`` and ``EUR 9.99``."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = re.sub(r"[^\d,.\-+]", "", text)
    if text in ("", "-", "+", ".", ","):
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        head, _, tail = text.rpartition(",")
        text = "{}.{}".format(head.replace(",", ""), tail) if len(tail) == 2 else text.replace(",", "")
    try:
        amount = float(text)
    except ValueError:
        return None
    return -amount if negative else amount


def parse_date(value, day_first=None):
    """Parse the date formats that show up in exported statements."""
    text = str(value or "").strip()
    if not text:
        return None
    iso = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if iso:
        try:
            return datetime.date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    numeric = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", text)
    if numeric:
        first, second, year = (int(part) for part in numeric.groups())
        year += 2000 if year < 100 else 0
        day, month = (first, second) if day_first else (second, first)
        if day > 12 and second <= 12:
            day, month = first, second
        elif month > 12 and first <= 12:
            day, month = second, first
        try:
            return datetime.date(year, month, day)
        except ValueError:
            return None
    words = re.match(r"^(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})", text)
    if words and words.group(2).lower()[:3] in MONTHS:
        try:
            return datetime.date(int(words.group(3)), MONTHS[words.group(2).lower()[:3]], int(words.group(1)))
        except ValueError:
            return None
    words = re.match(r"^([A-Za-z]{3})[a-z]*\s+(\d{1,2}),?\s+(\d{4})", text)
    if words and words.group(1).lower()[:3] in MONTHS:
        try:
            return datetime.date(int(words.group(3)), MONTHS[words.group(1).lower()[:3]], int(words.group(2)))
        except ValueError:
            return None
    return None


def merchant_key(description):
    """Reduce a statement line to a stable merchant fingerprint."""
    text = str(description).casefold()
    text = re.sub(r"\b(x{2,}|\*{2,})\s*\d{2,6}\b", " ", text)
    text = re.sub(r"\b\d{4,}\b", " ", text)
    text = re.sub(r"\d{1,2}[/.-]\d{1,2}([/.-]\d{2,4})?", " ", text)
    text = re.sub(r"[^a-z0-9&+ ]+", " ", text)
    text = NOISE.sub(" ", text)
    words = [word for word in text.split() if len(word) > 2]
    return " ".join(words[:4])


def _detect_columns(headers):
    lowered = [str(header).strip().lower() for header in headers]

    def find(keys):
        for key in keys:
            for index, header in enumerate(lowered):
                if header == key or key in header:
                    return index
        return None

    columns = {"date": find(DATE_KEYS), "description": find(DESC_KEYS)}
    columns["amount"] = find(AMOUNT_KEYS)
    columns["debit"] = find(DEBIT_KEYS)
    columns["credit"] = find(CREDIT_KEYS)
    return columns


def _rows(path):
    text = pathlib.Path(path).read_text(encoding="utf-8-sig", errors="replace")
    sample = text[:8192]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        counts = {candidate: sample.count(candidate) for candidate in (",", ";", "\t", "|")}
        delimiter = max(counts, key=counts.get) if any(counts.values()) else ","
    return list(csv.reader(text.splitlines(), delimiter=delimiter))


def _header_index(rows):
    for index, row in enumerate(rows[:15]):
        columns = _detect_columns(row)
        if columns["date"] is not None and (
            columns["amount"] is not None or columns["debit"] is not None or columns["credit"] is not None
        ):
            return index, columns
    raise ValueError("could not find a date column and an amount column in the first 15 rows")


def load_transactions(paths, day_first=None, invert=False):
    """Read one or more exported statement files into normalised transactions."""
    transactions = []
    for path in paths:
        rows = _rows(path)
        header_at, columns = _header_index(rows)
        for row in rows[header_at + 1 :]:
            if not any(cell.strip() for cell in row):
                continue

            def cell(name):
                index = columns.get(name)
                if index is None or index >= len(row):
                    return ""
                return row[index]

            when = parse_date(cell("date"), day_first=day_first)
            if when is None:
                continue
            amount = parse_amount(cell("amount")) if columns.get("amount") is not None else None
            if amount is None:
                debit = parse_amount(cell("debit"))
                credit = parse_amount(cell("credit"))
                if debit is None and credit is None:
                    continue
                amount = (credit or 0.0) - (debit or 0.0)
            if invert:
                amount = -amount
            transactions.append(
                Transaction(
                    date=when,
                    description=" ".join(cell("description").split()),
                    amount=amount,
                    source=pathlib.Path(path).name,
                )
            )
    transactions.sort(key=lambda item: item.date)
    return transactions


def cadence_for(days):
    """Map a median interval to a human cadence and its nominal length."""
    best = None
    for name, nominal, tolerance in CADENCES:
        error = abs(days - nominal)
        if error <= tolerance and (best is None or error < best[0]):
            best = (error, name, nominal)
    return (best[1], best[2]) if best else (None, None)


def find_recurring(transactions, min_occurrences=3, min_amount=0.0, max_interval_days=400):
    """Group outgoing charges and keep the groups that repeat on a cadence."""
    groups = {}
    for transaction in transactions:
        if transaction.amount >= 0:
            continue
        key = merchant_key(transaction.description)
        if not key:
            continue
        groups.setdefault(key, []).append(transaction)

    subscriptions = []
    for key, items in groups.items():
        if len(items) < min_occurrences:
            continue
        items = sorted(items, key=lambda item: item.date)
        amounts = [abs(item.amount) for item in items]
        median_amount = statistics.median(amounts)
        if median_amount < min_amount:
            continue
        intervals = [
            (later.date - earlier.date).days for earlier, later in zip(items, items[1:]) if later.date > earlier.date
        ]
        if not intervals:
            continue
        median_interval = statistics.median(intervals)
        if median_interval > max_interval_days:
            continue
        tolerance = max(2, median_interval * 0.25)
        regular = [value for value in intervals if abs(value - median_interval) <= tolerance]
        regularity = len(regular) / len(intervals)
        if regularity < 0.5:
            continue
        cadence, nominal = cadence_for(median_interval)
        if cadence is None:
            continue
        baseline = statistics.median(amounts[:-1]) if len(amounts) > 1 else amounts[-1]
        change = amounts[-1] - baseline
        if abs(change) <= max(0.01, baseline * 0.02):
            change = 0.0
        subscriptions.append(
            Subscription(
                merchant=items[0].description.strip()[:60],
                key=key,
                cadence=cadence,
                amount=round(median_amount, 2),
                count=len(items),
                first_seen=items[0].date,
                last_seen=items[-1].date,
                next_expected=items[-1].date + datetime.timedelta(days=int(round(median_interval))),
                annual_cost=round(median_amount * 365.0 / nominal, 2),
                price_change=round(change, 2),
                confidence=round(min(1.0, 0.6 * regularity + 0.05 * len(items)), 2),
                samples=tuple(items[-4:]),
            )
        )
    subscriptions.sort(key=lambda item: (-item.annual_cost, item.merchant.lower()))
    return subscriptions


def summarize(subscriptions):
    """Totals for a report header."""
    annual = sum(item.annual_cost for item in subscriptions)
    return {
        "count": len(subscriptions),
        "per_month": round(annual / 12.0, 2),
        "per_year": round(annual, 2),
        "price_increases": [item.merchant for item in subscriptions if item.price_change > 0],
    }

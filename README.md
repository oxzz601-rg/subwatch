# subwatch

**What are you actually paying for every month?**

Download a CSV of your card or bank statement, run `subwatch`, and get a list of the charges that repeat: the amount, the cadence, the next expected charge, the annual cost, and which ones quietly went up in price. Everything runs on your machine; the file never leaves it.

```console
$ subwatch report statement.csv --months 12
```

```markdown
# subwatch report

Read 1 file(s) for the last 12 months. 2 recurring charge(s) found.

- estimated spend: **26.48 per month**, **317.79 per year**
- price went up: NETFLIX.COM 1234

| merchant | cadence | amount | per year | last charged | next expected | change |
| --- | --- | --- | --- | --- | --- | --- |
| NETFLIX.COM 1234 | monthly | 15.49 | 188.46 | 2024-04-05 | 2024-05-05 | +2.50 |
| SPOTIFY P0A1B2 | monthly | 10.99 | 133.71 | 2024-03-12 | 2024-04-11 |  |

## Price increases

- **NETFLIX.COM 1234**: now 15.49 (up 2.50, i.e. 30.42 more per year)
```

## Install

```bash
python -m pip install -e ".[dev]"
```

Python 3.9+, standard library only.

## Bank-agnostic by design

Exports differ wildly, so `subwatch` adapts instead of demanding one format:

- **Delimiters**: comma, semicolon, tab or pipe, sniffed per file.
- **Preamble**: statements that start with "Account statement" and blank lines are fine; the header row is found in the first 15 rows.
- **Column names**: `Date`/`Buchungstag`/`Posted`, `Description`/`Payee`/`Buchungstext`/`Narrative`, `Amount`/`Betrag`, and separate `Debit`/`Credit` columns.
- **Numbers**: `-15.49`, `15,49`, `1.234,56`, `1,234.56`, `(12.00)`, `EUR 9.99`.
- **Dates**: `2024-03-05`, `05.03.2024`, `03/05/2024`, `5 Mar 2024`, `Mar 5, 2024`.

Use `--day-first` for `DD/MM` exports and `--invert` when your bank reports spending as positive.

## How it decides something is a subscription

1. Only outgoing charges are considered (income is ignored).
2. Each description is reduced to a merchant fingerprint: card masks, reference numbers, dates and words like `POS`, `PAYPAL` or `COM` are stripped, so `NETFLIX.COM 1234` and `netflix com 9876` are the same merchant.
3. A merchant needs at least `--min-occurrences` charges (3 by default).
4. The gaps between charges must be regular: at least half of them within 25% of the median gap.
5. The median gap must land in a known cadence: weekly, fortnightly, monthly, quarterly or yearly.
6. A price change is reported when the latest charge differs from earlier ones by more than 2%.

That is why three grocery trips do not show up, but a Spotify charge does.

## Flags

| flag | meaning |
| --- | --- |
| `--months 12` | only look at the last N months of the export |
| `--min-occurrences 3` | how many charges before something counts |
| `--min-amount 5` | ignore pocket-change charges |
| `--day-first` | read `05/01/2024` as 5 January |
| `--invert` | flip the sign for inverted exports |
| `--json` | machine-readable output |
| `-o report.md` | write the report to a file |

## Library use

```python
from subwatch import load_transactions, find_recurring, summarize

transactions = load_transactions(["statement.csv"])
subscriptions = find_recurring(transactions, min_occurrences=3)
for item in subscriptions:
    print(item.merchant, item.cadence, item.amount, item.next_expected)
print(summarize(subscriptions))
```

## Limits

- It reads exported statements, not bank APIs (no credentials, nothing to revoke).
- Annual renewals need at least three years of history to be recognised; the fallback is missing them rather than inventing them.
- A merchant that changes its descriptor every month (`PAYPAL *XYZ 8891` vs `PAYPAL *XYZ 4412`) still groups correctly, but genuinely different descriptors may not.
- Transfers between your own accounts can look recurring. Check the merchant names before acting.

## Tests

```bash
python -m pytest
```

MIT licensed.

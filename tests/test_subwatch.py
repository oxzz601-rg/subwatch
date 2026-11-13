import datetime
import json

from subwatch.cli import main
from subwatch.core import (
    cadence_for,
    find_recurring,
    load_transactions,
    merchant_key,
    parse_amount,
    parse_date,
    summarize,
)

COMMA = """Account statement,,,
,,,
Date,Description,Amount
2024-01-05,NETFLIX.COM 1234,-15.49
2024-02-05,NETFLIX.COM 1234,-15.49
2024-03-05,NETFLIX.COM 1234,-15.49
2024-04-05,NETFLIX.COM 1234,-17.99
2024-01-12,SPOTIFY P0A1B2,-10.99
2024-02-12,SPOTIFY P0A1B2,-10.99
2024-03-12,SPOTIFY P0A1B2,-10.99
2024-01-20,WHOLE FOODS MARKET,-42.10
2024-02-03,WHOLE FOODS MARKET,-38.00
2024-03-11,WHOLE FOODS MARKET,-41.00
2024-01-25,ACME PAYROLL,2000.00
"""

SEMICOLON = """Buchungstag;Buchungstext;Betrag
05.01.2024;NETFLIX.COM;-15,49
05.02.2024;NETFLIX.COM;-15,49
05.03.2024;NETFLIX.COM;-15,49
"""


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_amount_parsing_handles_bank_conventions():
    assert parse_amount("-15.49") == -15.49
    assert parse_amount("15,49") == 15.49
    assert parse_amount("1.234,56") == 1234.56
    assert parse_amount("1,234.56") == 1234.56
    assert parse_amount("(12.00)") == -12.0
    assert parse_amount("EUR 9.99") == 9.99
    assert parse_amount("  ") is None
    assert parse_amount("not money") is None


def test_date_parsing_covers_common_formats():
    assert parse_date("2024-03-05") == datetime.date(2024, 3, 5)
    assert parse_date("05.03.2024", day_first=True) == datetime.date(2024, 3, 5)
    assert parse_date("03/05/2024", day_first=False) == datetime.date(2024, 3, 5)
    assert parse_date("5 Mar 2024") == datetime.date(2024, 3, 5)
    assert parse_date("Mar 5, 2024") == datetime.date(2024, 3, 5)
    assert parse_date("") is None
    assert parse_date("31/02/2024", day_first=True) is None


def test_merchant_key_strips_card_numbers_and_noise():
    assert merchant_key("NETFLIX.COM 1234") == merchant_key("netflix com 9876")
    assert "netflix" in merchant_key("NETFLIX.COM 1234")
    assert merchant_key("SQ *COFFEE SHOP 000123") == merchant_key("SQ *COFFEE SHOP 000999")
    assert merchant_key("PAYPAL *STEAM GAMES") == merchant_key("PAYPAL *STEAM GAMES")
    assert merchant_key("12/03 CARD PAYMENT") == ""


def test_load_transactions_skips_preamble(tmp_path):
    transactions = load_transactions([write(tmp_path, "statement.csv", COMMA)])
    assert len(transactions) == 11  # header rows are not transactions
    assert transactions[0].date == datetime.date(2024, 1, 5)
    assert transactions[0].description == "NETFLIX.COM 1234"
    assert transactions[0].amount == -15.49
    dates = [item.date for item in transactions]
    assert dates == sorted(dates)
    assert transactions[-1].actions if False else any(item.amount == 2000.0 for item in transactions)


def test_german_export_with_semicolons_and_comma_decimals(tmp_path):
    transactions = load_transactions([write(tmp_path, "konto.csv", SEMICOLON)], day_first=True)
    assert [item.amount for item in transactions] == [-15.49, -15.49, -15.49]
    assert transactions[0].date == datetime.date(2024, 1, 5)


def test_recurring_detection_with_price_increase(tmp_path):
    subscriptions = find_recurring(load_transactions([write(tmp_path, "statement.csv", COMMA)]))
    names = {item.key for item in subscriptions}
    assert "netflix" in names
    netflix = next(item for item in subscriptions if item.key == "netflix")
    assert netflix.cadence == "monthly"
    assert netflix.count == 4
    assert netflix.price_change == 2.50
    assert netflix.amount == 15.49
    assert netflix.annual_cost == round(15.49 * 365 / 30, 2)
    # Median gap is 31 days, so the next charge lands on 6 May rather than 5 May.
    assert netflix.next_expected == datetime.date(2024, 5, 6)
    assert netflix.confidence > 0.5
    # Three groceries in two months are not a subscription.
    assert not any("whole" in item.key for item in subscriptions)
    # Income is ignored.
    assert not any("payroll" in item.key for item in subscriptions)


def test_min_occurrences_filters_short_runs(tmp_path):
    transactions = load_transactions([write(tmp_path, "statement.csv", COMMA)])
    assert find_recurring(transactions, min_occurrences=4)
    assert all(item.count >= 4 for item in find_recurring(transactions, min_occurrences=4))


def test_cadence_buckets():
    assert cadence_for(6)[0] == "weekly"
    assert cadence_for(7)[0] == "weekly"
    assert cadence_for(14)[0] == "fortnightly"
    assert cadence_for(31)[0] == "monthly"
    assert cadence_for(92)[0] == "quarterly"
    assert cadence_for(366)[0] == "yearly"
    assert cadence_for(3)[0] is None


def test_summary_totals(tmp_path):
    subscriptions = find_recurring(load_transactions([write(tmp_path, "statement.csv", COMMA)]))
    totals = summarize(subscriptions)
    assert totals["count"] == len(subscriptions)
    assert totals["per_year"] == round(sum(item.annual_cost for item in subscriptions), 2)
    assert "netflix" in " ".join(totals["price_increases"]).lower()


def test_cli_prints_json(tmp_path, capsys):
    path = write(tmp_path, "statement.csv", COMMA)
    assert main([str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["count"] >= 2
    assert any(item["key"] == "netflix" for item in payload["subscriptions"])
    assert main([str(path), "--months", "2", "--json"]) == 0
    assert main([str(tmp_path / "missing.csv"), "--json"]) == 2

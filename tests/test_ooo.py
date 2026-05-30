from datetime import date

from src.lib.ooo import parse_return_date

TODAY = date(2026, 6, 1)


def test_month_day():
    assert parse_return_date("I'll be back on June 3.", today=TODAY) == date(2026, 6, 3)


def test_day_month():
    assert parse_return_date("Returning 3 June.", today=TODAY) == date(2026, 6, 3)


def test_numeric_md():
    assert parse_return_date("back on 6/9", today=TODAY) == date(2026, 6, 9)


def test_numeric_mdy():
    assert parse_return_date("back 06/09/2026", today=TODAY) == date(2026, 6, 9)


def test_iso():
    assert parse_return_date("returning 2026-06-09", today=TODAY) == date(2026, 6, 9)


def test_ignores_departure_date_takes_return():
    # "out 6/2 through 6/9" -> the return is 6/9, not the departure 6/2
    assert parse_return_date("I am out 6/2 through 6/9.", today=TODAY) == date(2026, 6, 9)


def test_latest_of_many():
    assert parse_return_date("away 6/3, 6/15, and 6/9", today=TODAY) == date(2026, 6, 15)


def test_until_further_notice_is_none():
    assert parse_return_date("Out of office until further notice.", today=TODAY) is None


def test_no_date_is_none():
    assert parse_return_date("Thanks for your email.", today=TODAY) is None


def test_bare_month_day_rolls_to_next_year_if_past():
    # Jan 5 is before today (June 1 2026) -> next occurrence is 2027
    assert parse_return_date("back on January 5", today=TODAY) == date(2027, 1, 5)

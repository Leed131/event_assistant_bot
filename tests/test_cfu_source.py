from app.cfu_source import CFUSource, parse_cfu_datetimes
from app.source_overrides import parser_for_source


def test_cfu_root_url_uses_dedicated_parser():
    parser = parser_for_source("https://cfu.kp.dk", "user")
    assert isinstance(parser, CFUSource)


def test_cfu_numeric_date_range():
    start, end = parse_cfu_datetimes(
        "Tirsdag d. 25.08.2026 - Tirsdag d. 22.09.2026"
    )
    assert start is not None
    assert end is not None
    assert (start.year, start.month, start.day, start.hour, start.minute) == (2026, 8, 25, 12, 0)
    assert (end.year, end.month, end.day, end.hour, end.minute) == (2026, 9, 22, 12, 0)


def test_cfu_word_date_with_time():
    start, end = parse_cfu_datetimes(
        "Mandag d. 31. august 2026 Kl. 14.30 - 16.30"
    )
    assert start is not None
    assert end is not None
    assert (start.year, start.month, start.day, start.hour, start.minute) == (2026, 8, 31, 14, 30)
    assert (end.year, end.month, end.day, end.hour, end.minute) == (2026, 8, 31, 16, 30)

"""One test for Level 1: the engagement-count parser everything else in
engage.py's ranking depends on. Covers the European number formatting
(dot as thousands separator, comma as decimal point) that a plain int()
or naive comma-strip would get wrong."""

from engage import parse_count


def test_parse_count_handles_locale_formats():
    assert parse_count("500") == 500
    assert parse_count("1.234") == 1234       # dot = thousands separator
    assert parse_count("1,2 MIL") == 1200      # comma = decimal point, "mil" = x1000
    assert parse_count("2.500,5") == 2500      # both together
    assert parse_count("1.2K") == 1200         # English-style K suffix
    assert parse_count("") == 0
    assert parse_count("no digits here") == 0

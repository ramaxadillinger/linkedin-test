"""One test for Level 1: the engagement-count parser everything else in
engage.py's ranking depends on. Covers the European number formatting
(dot as thousands separator, comma as decimal point) that a plain int()
or naive comma-strip would get wrong. Plus one for the --mock fixture path
(bonus, not part of the graded levels)."""

from engage import like_post, load_mock_candidates, parse_count


def test_parse_count_handles_locale_formats():
    assert parse_count("500") == 500
    assert parse_count("1.234") == 1234       # dot = thousands separator
    assert parse_count("1,2 MIL") == 1200      # comma = decimal point, "mil" = x1000
    assert parse_count("2.500,5") == 2500      # both together
    assert parse_count("1.2K") == 1200         # English-style K suffix
    assert parse_count("") == 0
    assert parse_count("no digits here") == 0


def test_mock_candidates_match_real_shape_and_never_click():
    candidates = load_mock_candidates()

    assert len(candidates) > 0
    for c in candidates:
        assert c["locator"] is None  # nothing to click - no real DOM element
        assert c["engagement"] == c["reactions"] + c["comments"]
        assert c["author"] and c["text"]

    assert like_post(candidates[0]["locator"]) == "LIKED (mock)"

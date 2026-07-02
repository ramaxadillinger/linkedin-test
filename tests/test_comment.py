"""One test per level covered by comment.py.

Level 2: the 10 -> 2-3 selection rule (pick_worth_commenting) - drops link
and group posts, keeps the longest remaining text.

Level 3: parse_topcard - locks in the two bugs the real profile scrape hit
(name picked as location, then the connection-degree badge picked as
location) so they can't silently come back.
"""

from comment import parse_topcard, pick_worth_commenting


def _post(text, profile_url="https://www.linkedin.com/in/someone/"):
    return {"text": text, "profile_url": profile_url, "author": "Someone"}


def test_pick_worth_commenting_drops_ads_and_keeps_longest():
    posts = [
        _post("Short one."),
        _post("Join our WhatsApp group: https://lnkd.in/abc123 for more info"),
        _post("A" * 50, profile_url="(no profile link - group post)"),
        _post("This one is the longest genuine post by a wide margin, no links, no group."),
    ]

    chosen = pick_worth_commenting(posts, n=2)

    assert len(chosen) == 2
    assert chosen[0]["text"].startswith("This one is the longest")
    assert all("lnkd.in" not in c["text"] for c in chosen)
    assert all(not c["profile_url"].startswith("(no profile link") for c in chosen)


def test_parse_topcard_skips_name_and_degree_badge():
    lines = [
        "Romans Veremeiciks",   # name - must not be picked as location
        "· 2nd",                 # connection-degree badge - must not be picked either
        "QA & Support Engineer | E2E Testing (Playwright, TypeScript)",
        "Valencia y alrededores",  # the actual location
        "500+ contactos",
    ]

    profile = parse_topcard(lines)

    assert profile["headline"].startswith("QA & Support Engineer")
    assert profile["location"] == "Valencia y alrededores"

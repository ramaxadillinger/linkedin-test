"""
Level 2 - Comment thoughtfully. Level 3 - Profile-aware (--profile-aware).

Reuses engage.py's feed scraping (same session, same ranking) to get the
top 10 posts by engagement, then narrows to 2-3 worth an actual comment,
drafts a human-sounding reply for each via the Claude API, and prints it
next to the post. Nothing is posted - this never touches the comment box.

Selection rule (top 10 -> 2-3): drop posts that are essentially ads - a
link in the text (job/training/group invites almost always carry one) or a
post surfaced through a group rather than a person, since there's nothing
genuine to say back to a recruiting pitch. Of what's left, take the 3 with
the longest text: length is a cheap proxy for "the author said enough that
a reply can be specific," rather than a one-liner a comment would just
restate.

With --profile-aware (Level 3): before drafting, visit each chosen post's
author profile and pull headline, location, and mutual connections from the
profile "Topcard" and a snippet of their "About" section, then feed that
into the prompt so the comment can reference who they are, not just what
they posted. See PROFILE_SELECTORS docstring below for what tripped this up.

Usage:
    python src/comment.py                    # draft comments for top posts, print only
    python src/comment.py --top 2            # narrow to 2 instead of 3
    python src/comment.py --profile-aware    # Level 3: visit author profiles first
    python src/comment.py --mock             # fixture data, no browser/login needed
"""

import argparse
import os
import re
import sys
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from engage import AUTH_STATE, FEED_URL, SELECTORS, TOP_N, collect_candidates, load_mock_candidates
from login import perform_login
from retry import retry

load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # .env lives at repo root, not src/

MODEL = "claude-haiku-4-5-20251001"  # cheap model - drafting a 1-3 sentence comment doesn't need more
LINK_MARKERS = ("http://", "https://", "lnkd.in")

# Same story as engage.py's SELECTORS: LinkedIn profile pages are SDUI too,
# but each profile "card" has a componentkey ending in a stable, readable
# name (...Topcard, ...About) regardless of the opaque hash prefixing it -
# so "ends with" is the one thing worth matching on.
PROFILE_SELECTORS = {
    "topcard": '[componentkey$="Topcard"]',
    "about": '[componentkey$="About"]',
}


def is_commentable(candidate: dict) -> bool:
    if candidate["profile_url"].startswith("(no profile link"):
        return False  # surfaced via a group, not a person - no one to reply to
    if any(marker in candidate["text"] for marker in LINK_MARKERS):
        return False  # link in the body -> almost always a job/training/group ad
    return True


def pick_worth_commenting(top_posts: list[dict], n: int) -> list[dict]:
    commentable = [c for c in top_posts if is_commentable(c)]
    return sorted(commentable, key=lambda c: len(c["text"]), reverse=True)[:n]


DEGREE_BADGE = re.compile(r"[º·•]|\b(1st|2nd|3rd)\b", re.IGNORECASE)


def parse_topcard(lines: list[str]) -> dict:
    """Pull headline/location/mutual-connections out of the Topcard's plain
    text lines. Pure function (no Playwright) so it's unit-testable without a
    live profile - see test_comment.py, which locks in the two bugs this
    already caused: the name itself getting picked as "location" (both are
    short, plain lines), and after excluding the name, the "· 1st / · 2nd"
    connection-degree badge getting picked instead (also short, plain text).
    Locale-tied like engage.py's constants - "en común" is Spanish for
    "in common"."""
    if not lines:
        return {"headline": "", "location": "", "mutual": ""}

    name = lines[0]
    rest = lines[1:]
    headline = next((l for l in rest[:5] if len(l) > 20), "")
    location = next(
        (l for l in rest if l not in (name, headline) and 3 < len(l) < 50 and not DEGREE_BADGE.search(l)),
        "",
    )
    mutual = next((l for l in rest if "común" in l.lower() or "in common" in l.lower()), "")
    return {"headline": headline, "location": location, "mutual": mutual}


@retry(attempts=2, delay=2.0, exceptions=(PlaywrightTimeoutError,))
def _goto_profile(page: Page, profile_url: str) -> None:
    # "load" never fires on profile pages (LinkedIn keeps background requests
    # going indefinitely) - domcontentloaded is what actually happens quickly.
    # Still retried: a profile nav can transiently time out even on
    # domcontentloaded under a slow connection.
    page.goto(profile_url, wait_until="domcontentloaded", timeout=15_000)


def scrape_profile(page: Page, profile_url: str) -> dict:
    """Best-effort pull of headline/location/mutual-connections/about from a
    profile page. Falls back to empty strings per field rather than raising,
    since a partial profile is still useful context."""
    _goto_profile(page, profile_url)
    page.wait_for_timeout(4000)

    topcard = page.locator(PROFILE_SELECTORS["topcard"]).first
    lines = [l.strip() for l in topcard.inner_text().split("\n") if l.strip()] if topcard.count() else []
    profile = parse_topcard(lines)

    about_loc = page.locator(PROFILE_SELECTORS["about"]).first
    about = ""
    if about_loc.count():
        about_lines = about_loc.inner_text().split("\n", 1)
        about = about_lines[-1].strip()[:400] if len(about_lines) > 1 else ""
    profile["about"] = about

    return profile


def mock_profile(post: dict) -> dict:
    """Fixture posts already carry headline/location/mutual (see
    mock_feed.json) - reshape into the same dict scrape_profile() returns,
    no live page to visit."""
    return {
        "headline": post.get("headline", ""),
        "location": post.get("location", ""),
        "mutual": post.get("mutual", ""),
        "about": "",
    }


def draft_comment(client: Anthropic, post: dict, profile: dict | None = None) -> str:
    context = ""
    if profile:
        context = f"""
More about the author, from their profile:
- Headline: {profile['headline'] or '(not found)'}
- Location: {profile['location'] or '(not found)'}
- Mutual connections: {profile['mutual'] or '(none found)'}
- About: {profile['about'] or '(not found)'}

Use this to make the comment more specific - e.g. relate to their
background or find genuine common ground - but don't just list these facts
back at them."""

    prompt = f"""Draft a LinkedIn comment reacting to this post. Write like a real
person who actually read it and had a reaction - not like an AI summarizing
it. 1-3 sentences, no hashtags, no emoji unless one fits naturally, no
generic openers like "Great post!" or "Thanks for sharing". React to
something specific in the text below.

Author: {post['author']}
Post:
{post['text']}
{context}"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def print_comments(client: Anthropic, chosen: list[dict], get_profile) -> None:
    for i, post in enumerate(chosen, start=1):
        profile = get_profile(post)
        comment = draft_comment(client, post, profile)

        print(f"[{i}] Author: {post['author']}")
        print(f"    Profile: {post['profile_url']}")
        if profile:
            print(f"    Headline: {profile['headline']}")
            print(f"    Location: {profile['location']}")
            print(f"    Mutual: {profile['mutual']}")
        print(f"    Post: {post['text'][:200]}")
        print(f"    Drafted comment: {comment}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=3, help="How many posts to draft comments for (2-3)")
    parser.add_argument("--profile-aware", action="store_true", help="Level 3: visit each author's profile before drafting")
    parser.add_argument("--mock", action="store_true", help="Use fixture data instead of a live account (offline, no login/browser needed)")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY not set. Put it in a .env file or export it.")

    client = Anthropic()

    if args.mock:
        top_posts = sorted(load_mock_candidates(), key=lambda c: c["engagement"], reverse=True)[:TOP_N]
        chosen = pick_worth_commenting(top_posts, args.top)
        print(f"From the top {len(top_posts)}, {len(chosen)} are worth a comment (see selection rule in module docstring).\n")
        get_profile = mock_profile if args.profile_aware else (lambda post: None)
        print_comments(client, chosen, get_profile)
        return

    if not AUTH_STATE.exists():
        print("No saved session found - opening a browser for a one-time manual login.")
        perform_login()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(storage_state=str(AUTH_STATE))
        page = context.new_page()
        page.goto(FEED_URL)
        page.wait_for_selector(SELECTORS["post"], timeout=15_000)

        candidates = collect_candidates(page)
        if not candidates:
            sys.exit("No organic posts found - selectors likely need updating (see engage.py).")

        top_posts = sorted(candidates, key=lambda c: c["engagement"], reverse=True)[:TOP_N]
        chosen = pick_worth_commenting(top_posts, args.top)
        print(f"From the top {len(top_posts)}, {len(chosen)} are worth a comment (see selection rule in module docstring).\n")

        get_profile = (lambda post: scrape_profile(page, post["profile_url"])) if args.profile_aware else (lambda post: None)
        print_comments(client, chosen, get_profile)

        browser.close()


if __name__ == "__main__":
    main()

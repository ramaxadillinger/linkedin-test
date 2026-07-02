"""
Level 1 - Read & React.

Loads the LinkedIn home feed using the session saved by login.py, scrolls
to collect a pool of organic (non-sponsored) posts, ranks them by
engagement (reactions + comments), likes the top 10, and prints
author + first 200 chars + outcome for each.

SELECTOR NOTE: LinkedIn's feed is server-driven UI with hashed, non-semantic
CSS classes that are useless as selectors (they rotate per build). The
selectors below were reverse-engineered against a live session (see
debug_selectors.py) using the few stable hooks LinkedIn does expose:
data-testid, role, componentkey, and href patterns. Two of them
(PROMOTED_TEXT, REACTION_BUTTON_ARIA_PREFIX, COUNT_PATTERNS) are tied to
the UI *locale* of the account this was built against (Spanish). If your
account is in a different language, update those three constants -
everything else (data-testid, role, href*="/in/") is locale-independent.

Usage:
    python src/engage.py --dry-run   # collect + print, do not click Like
    python src/engage.py             # collect + actually like the top 10
    python src/engage.py --mock      # run against fixture data, no browser/login needed
"""

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, Locator
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from login import perform_login
from retry import retry

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # src/ -> repo root
AUTH_STATE = PROJECT_ROOT / "auth" / "state.json"
FEED_URL = "https://www.linkedin.com/feed/"
MOCK_FEED_PATH = Path(__file__).parent / "fixtures" / "mock_feed.json"

CANDIDATE_POOL_SIZE = 30  # how many organic posts to scan before ranking
TOP_N = 10                # how many posts to like
MAX_SCROLL_PASSES = 20

# Locale-dependent strings (built/tested against a Spanish-locale LinkedIn UI).
PROMOTED_TEXT = "Promocionado"
REACTION_BUTTON_ARIA_PREFIX = "Estado del botón de reacción"
NOT_YET_REACTED_MARKER = "ninguna"  # substring of the aria-label when un-liked
COUNT_PATTERNS = {
    "reactions": re.compile(r"([\d.,]+)\s*reacci", re.IGNORECASE),
    "comments": re.compile(r"([\d.,]+)\s*comentari", re.IGNORECASE),
}

SELECTORS = {
    "post": '[data-testid="mainFeed"] [role="listitem"][componentkey*="FeedType_MAIN_FEED_RELEVANCE"]',
    "post_text": '[data-testid="expandable-text-box"]',
    "profile_link": 'a[href*="linkedin.com/in/"]',
    "like_button": f'button[aria-label^="{REACTION_BUTTON_ARIA_PREFIX}"]',
}


def parse_count(raw: str) -> int:
    """Turn engagement text into an int. Spanish-locale LinkedIn uses European
    number formatting, which is ambiguous without knowing whether a K/MIL
    multiplier suffix is present:
    - With a suffix ('1,2 MIL', '1.2K'): the separator is a decimal point
      before the x1000 multiplier.
    - Without one ('1.234', a plain unabbreviated count): the dot is a
      thousands separator, since engagement counts don't have fractions."""
    if not raw:
        return 0
    raw = raw.strip().upper().replace(" ", "")
    match = re.match(r"([\d.,]+)(K|MIL)?", raw)
    if not match:
        return 0

    digits, suffix = match.group(1), match.group(2)
    if suffix:
        number = digits.replace(",", ".")  # "1,2" -> "1.2"
    else:
        number = digits.replace(".", "").replace(",", ".")  # "2.500,5" -> "2500.5"

    try:
        value = float(number)
    except ValueError:
        return 0
    if suffix:
        value *= 1000
    return int(value)


def extract_count(post: Locator, pattern: re.Pattern) -> int:
    text = post.inner_text()
    match = pattern.search(text)
    return parse_count(match.group(1)) if match else 0


def extract_author(post: Locator) -> tuple[str, str]:
    """Return (name, profile_url). The actor block has a name-only link and
    an avatar-only link pointing at the same /in/ URL; the avatar one has no
    text, so the first non-empty one is the name link.

    Group posts are the exception: the actor link points at the group's feed,
    not /in/username, so there's no profile_url to extract there. Fall back
    to the "{name} perfil ..." aria-label LinkedIn puts on the actor block
    (locale-tied, see module docstring) to at least get a name."""
    links = post.locator(SELECTORS["profile_link"])
    for i in range(links.count()):
        link = links.nth(i)
        text = link.inner_text().strip()
        if text:
            name = text.split("•")[0].strip()  # strip " • 2nd" degree badge
            url = link.get_attribute("href") or ""
            return name, url.split("?")[0]

    fallback = post.locator('[aria-label*="perfil"]').first
    if fallback.count() > 0:
        label = fallback.get_attribute("aria-label") or ""
        name = re.split(r"\s+perfil", label)[0].strip()
        if name:
            return name, "(no profile link - group post)"
    return "(unknown author)", ""


def collect_candidates(page: Page) -> list[dict]:
    """Scroll the feed and collect a pool of organic posts with metadata."""
    seen_keys = set()
    candidates = []

    for _ in range(MAX_SCROLL_PASSES):
        for post in page.locator(SELECTORS["post"]).all():
            key = post.get_attribute("componentkey")
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)

            if post.get_by_text(PROMOTED_TEXT, exact=False).count() > 0:
                continue  # skip sponsored posts

            text_loc = post.locator(SELECTORS["post_text"]).first
            if text_loc.count() == 0:
                continue  # skip posts without readable text (image/video-only shares)
            text = text_loc.inner_text().strip()
            if not text:
                continue

            author, profile_url = extract_author(post)
            reactions = extract_count(post, COUNT_PATTERNS["reactions"])
            comments = extract_count(post, COUNT_PATTERNS["comments"])

            candidates.append({
                "key": key,
                "locator": post,
                "author": author,
                "profile_url": profile_url,
                "text": text,
                "reactions": reactions,
                "comments": comments,
                "engagement": reactions + comments,
            })

        if len(candidates) >= CANDIDATE_POOL_SIZE:
            break
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(3000)  # LinkedIn's feed lazy-loads slowly; rushing this yields empty passes

    return candidates


def load_mock_candidates() -> list[dict]:
    """Fixture posts (fictional celebrity LinkedIn-parody content, purely for
    offline testing) in the same shape collect_candidates produces - minus
    "locator", since there's no real DOM element to click. like_post treats
    locator=None as a no-op "like" so the rest of the pipeline (ranking,
    selection, drafting) runs unmodified against this data."""
    posts = json.loads(MOCK_FEED_PATH.read_text())
    return [
        {**post, "key": post["author"], "locator": None, "engagement": post["reactions"] + post["comments"]}
        for post in posts
    ]


def dismiss_reaction_popover(page: Page) -> None:
    """Hovering the like button opens a floating reaction-picker (love/celebrate/
    etc). It renders as a full-page overlay and, left open, blocks every click
    on the rest of the feed - this is what turned 5 of 6 likes into 30s
    timeouts on the first live run. Close it before touching the next post."""
    page.keyboard.press("Escape")
    page.mouse.move(0, 0)


@retry(attempts=2, delay=1.0, exceptions=(PlaywrightTimeoutError,))
def _click_like(like_button: Locator) -> None:
    # Retried, not the rest of like_post: a click can transiently fail if a
    # leftover popover (see dismiss_reaction_popover) hasn't fully closed yet
    # from the previous post - a second attempt after a short wait clears it.
    # A missing button or an already-liked post won't start succeeding on
    # retry, so those are handled before this is ever called.
    like_button.click(timeout=10_000)


def like_post(post_locator: Locator | None) -> str:
    if post_locator is None:
        return "LIKED (mock)"  # --mock candidates have no real DOM element to click
    page = post_locator.page
    # The action bar (like/comment/share) lazy-mounts only once a post is
    # actually scrolled near the viewport - posts collected earlier in the
    # scroll pass may not have it in the DOM yet, which showed up as
    # "like button not found" on 4/10 posts in the first live run.
    post_locator.scroll_into_view_if_needed()
    page.wait_for_timeout(500)
    like_button = post_locator.locator(SELECTORS["like_button"]).first
    if like_button.count() == 0:
        return "FAILED (like button not found)"
    aria_label = (like_button.get_attribute("aria-label") or "").lower()
    if NOT_YET_REACTED_MARKER not in aria_label:
        return "SKIPPED (already liked)"
    try:
        _click_like(like_button)
        return "LIKED"
    except Exception as exc:
        return f"FAILED ({exc})"
    finally:
        dismiss_reaction_popover(page)
        time.sleep(random.uniform(1.5, 3.5))  # human-like pacing between actions


def print_results(candidates: list[dict], dry_run: bool) -> None:
    top_posts = sorted(candidates, key=lambda c: c["engagement"], reverse=True)[:TOP_N]
    print(f"Collected {len(candidates)} organic candidates, engaging with top {len(top_posts)} by reactions+comments.\n")

    for i, post in enumerate(top_posts, start=1):
        snippet = post["text"][:200]
        outcome = "DRY-RUN (not liked)" if dry_run else like_post(post["locator"])

        print(f"[{i}] Author: {post['author']}")
        print(f"    Profile: {post['profile_url']}")
        print(f"    Engagement score: {post['engagement']} ({post['reactions']} reactions, {post['comments']} comments)")
        print(f"    Text: {snippet}")
        print(f"    Outcome: {outcome}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Collect and print candidates but do not click Like")
    parser.add_argument("--mock", action="store_true", help="Use fixture data instead of a live account (offline, no login needed)")
    args = parser.parse_args()

    if args.mock:
        print_results(load_mock_candidates(), args.dry_run)
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
            sys.exit("No organic posts found - selectors likely need updating (see module docstring).")

        print_results(candidates, args.dry_run)
        browser.close()


if __name__ == "__main__":
    main()

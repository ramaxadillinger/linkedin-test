"""
Debug helper, not part of the pipeline. Dumps rendered HTML to a local file
so you can grep it for selectors when LinkedIn changes its markup again.

Usage:
    python src/debug_selectors.py                                   # dumps feed_dump.html
    python src/debug_selectors.py https://linkedin.com/in/someone/   # dumps profile_dump.html
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

AUTH_STATE = Path(__file__).resolve().parent.parent / "auth" / "state.json"

url = sys.argv[1] if len(sys.argv) > 1 else "https://www.linkedin.com/feed/"
out_name = "feed_dump.html" if "feed" in url else "profile_dump.html"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(storage_state=str(AUTH_STATE))
    page = context.new_page()
    page.goto(url)
    page.wait_for_timeout(6000)

    out = Path(__file__).parent / out_name
    out.write_text(page.content())
    print(f"Saved {out}")

    browser.close()

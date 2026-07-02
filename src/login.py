"""
One-time manual login.

Opens a headed browser to LinkedIn's login page, waits for you to log in
by hand (including any 2FA/checkpoint challenge), then saves the
authenticated session to auth/state.json so engage.py/comment.py can reuse
it without logging in again.

Run standalone:
    python src/login.py

engage.py and comment.py also call perform_login() themselves if
auth/state.json is missing, so a teammate can just run those directly
without a separate login step.
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # src/ -> repo root
AUTH_DIR = PROJECT_ROOT / "auth"
STATE_PATH = AUTH_DIR / "state.json"


def perform_login() -> None:
    AUTH_DIR.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.linkedin.com/login")

        print("Log in manually in the opened browser window (email, password, 2FA if prompted).")
        print("Waiting for your home feed to load (up to 5 minutes)...", flush=True)

        page.wait_for_url("**/feed/**", timeout=300_000)
        context.storage_state(path=str(STATE_PATH))
        print(f"Session saved to {STATE_PATH}")

        browser.close()


if __name__ == "__main__":
    perform_login()

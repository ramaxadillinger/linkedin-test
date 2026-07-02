# linkedin-test

Python + Playwright prototype for the LinkedIn engagement take-home: reads
your home feed, likes the top posts by engagement, and drafts
profile-aware, human-sounding comments for the 2-3 most worth replying to.

## Level reached

**Level 3** (profile-aware comments) - all three levels are fully verified
end-to-end against a live account, with real captured output for each.

Time spent: **~2 hours** of active work, spread across a few sittings
(including one during Spain's World Cup match). Most of it went into
Level 1 - LinkedIn's feed turned out to be a fully server-driven UI with
hashed, rotating CSS classes (`_7da9d76e`, ...), not the classic markup
most public selector examples assume, so there was no shortcut around live
DOM reverse-engineering (see `debug_selectors.py`). Several real bugs
(below) only surfaced once actually clicking Like and visiting profiles on
a live session - each found, fixed, and locked in with a test or an
explicit retry.

## How it works

### Level 1 - `engage.py`

1. If there's no saved session (`auth/state.json`), it opens a headed
   browser and prompts you to log in by hand - 2FA/checkpoints are yours to
   handle, not the bot's. Session gets saved for next time (`login.py`
   holds this logic; both `engage.py` and `comment.py` call it automatically
   when needed, so a teammate doesn't need a separate first step).
2. Opens the feed, scrolls collecting organic (non-promoted) posts with
   readable text - up to 30, or until scrolling stops surfacing new ones.
3. **Selection rule:** rank by `reactions + comments`, take the top 10.
   Rationale: engagement count is a public, objective "worth reacting to"
   signal sitting right in the feed, with no need to guess at topic
   relevance.
4. Click Like on each, with a 1.5-3.5s randomized delay between actions.
   Prints `author`, `profile_url`, first 200 chars, and outcome
   (`LIKED` / `SKIPPED (already liked)` / `FAILED (...)`) for each.

### Level 2 - `comment.py`

Reuses `engage.py`'s own scraping/ranking (same top 10), then:

1. **Narrows 10 -> 2-3:** drops posts that are essentially ads - a link in
   the text (job/training/group invites almost always carry one) or a post
   surfaced through a group rather than a person (nobody to reply to).
   Of what's left, takes the posts with the longest text, since length is a
   cheap proxy for "said enough that a reply can be specific" rather than a
   one-liner a comment would just restate.
   *Known gap:* this only catches ads that contain a literal link. A
   text-only recruiting pitch (e.g. "we're hiring an SMM Lead, here's what
   we're looking for...") slips through the rule since it has no URL. Would
   need a cheap classifier or keyword list to close that, wasn't worth it
   for 2-3 posts here.
2. Drafts a comment for each via the Claude API (`claude-haiku-4-5` - cheap
   model, a 1-3 sentence comment doesn't need more), instructed to react to
   something specific in the text rather than produce a generic
   "Great post!" opener.
3. Prints post + drafted comment. **Never touches the comment box** - no
   posting happens.

### Level 3 - `comment.py --profile-aware`

Same flow as Level 2, but before drafting each of the 2-3 chosen comments,
it visits that author's profile and pulls headline, location, and mutual
connections from the profile header plus a snippet of their "About"
section, then feeds that into the prompt. It's a visible difference in the
output - e.g. a comment for a "Senior Test Engineer at WiFi Wizards Inc"
referencing their "wireless systems background," or one for a "Healthcare
Automation" engineer framed around what a bug means for a patient, not just
the code. See `samples/sample_output_level3.txt`.

## How to run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

python src/engage.py --dry-run        # sanity-check the collected candidates first
python src/engage.py                  # actually likes the top posts
                                       # (opens a login window automatically on first run)

echo "ANTHROPIC_API_KEY=sk-ant-..." > .env   # see .env.example, must be at repo root
python src/comment.py                 # Level 2: drafts comments, prints only, posts nothing
python src/comment.py --top 2         # narrow to 2 instead of 3
python src/comment.py --profile-aware # Level 3: also visits author profiles first

python -m pytest                      # unit tests for the pure logic (no browser needed)
```

All commands run from the repo root - `src/` holds the code, but `auth/`
(saved session) and `.env` (API key) are expected at the repo root, not
inside `src/`.

- `samples/sample_output.txt` - real stdout from one `engage.py` run (Level 1).
- `samples/sample_output_level2.txt` - real stdout from one `comment.py` run
  (Level 2) - note the drafted comments react to specifics in each post
  (Marta's RAG + observability combo, the "no Boolean search" limitation of
  Threads) rather than generic praise, which was the whole point of the
  prompt in `draft_comment`.
- `samples/sample_output_level3.txt` - real stdout from one `comment.py
  --profile-aware` run (Level 3) - compare to Level 2's output for the same
  kind of post to see the profile context actually changing what the
  comment focuses on.

## Notes / what tripped things up

- **LinkedIn's feed and profile pages are SDUI, not classic HTML** - CSS
  classes are hashed and rotate per build, so selectors had to be
  reverse-engineered live off `data-testid`, `role`+`componentkey`, and
  `href*="/in/"` instead (see `debug_selectors.py`). A few strings
  (`PROMOTED_TEXT`, count regexes, the "común" mutual-connections check) are
  tied to this account's Spanish UI locale and would need updating for
  another language.
- **Two real bugs only showed up on live clicks:** a leftover reaction-picker
  popover blocked every click after the first Like (5/6 failures on the
  first run - fixed with `dismiss_reaction_popover`), and the action bar
  lazy-mounts only near the viewport (4/10 "button not found" - fixed with
  `scroll_into_view_if_needed()`).
- **Profile scraping had its own two:** `page.goto()` hangs forever on the
  default "load" event since LinkedIn never stops background requests
  (fixed via `domcontentloaded`), and the Topcard's unlabeled text lines
  meant both the name and the "· 1st/2nd" connection badge briefly got
  misparsed as "location" before excluding them explicitly.
- **Feed pool size varies a lot between runs** (6 to 26 organic candidates
  seen) - it's an algorithmic feed, not a stable list, so `TOP_N` just
  shrinks to whatever's available.
- **Retries over point fixes:** the Like click and profile navigation both
  had transient failures, so both go through a small `retry.py` decorator
  instead of one-off workarounds.
- **A unit test caught a real ranking bug:** `parse_count` was parsing
  `"1.234"` (over a thousand, no `K`/`mil` suffix) as `1` instead of `1234` -
  never surfaced in samples since every count seen here was under 1000, but
  would've quietly under-ranked any popular post. `tests/` has one test per
  level.

## Files

```
src/
  login.py               # manual login flow; auto-invoked by engage.py/comment.py if no session
  engage.py              # Level 1: collect, rank, like, print
  comment.py             # Level 2/3: collect, rank, narrow to 2-3, (optionally) scrape profiles, draft via Claude, print
  retry.py               # small retry decorator, used on the Like click and profile navigation
  debug_selectors.py     # dumps live feed/profile HTML to *_dump.html for re-deriving selectors later
tests/
  test_engage.py         # Level 1 test: parse_count
  test_comment.py        # Level 2 test (pick_worth_commenting) + Level 3 test (parse_topcard)
samples/
  sample_output.txt          # captured stdout from one real engage.py run (Level 1)
  sample_output_level2.txt   # captured stdout from one real comment.py run (Level 2)
  sample_output_level3.txt   # captured stdout from one real comment.py --profile-aware run (Level 3)
auth/                    # saved session cookies (gitignored, created by login.py)
requirements.txt
pytest.ini               # points pytest at src/ so tests can import engage/comment directly
.env.example             # ANTHROPIC_API_KEY, needed only for comment.py - copy to .env at repo root
```

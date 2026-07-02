"""
Small retry decorator for the transient failures browser automation
actually hits - a click that lands mid-animation, a page that's slow to
respond. Used on the two spots that showed flaky behavior in live runs:
engage.py's Like click and comment.py's profile page navigation.

Not used for logic errors (wrong selector, missing element) - retrying
those just wastes time, since they fail the same way every time.
"""

import time
from functools import wraps


def retry(attempts: int = 3, delay: float = 1.0, exceptions: tuple = (Exception,)):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions:
                    if attempt == attempts:
                        raise
                    time.sleep(delay * attempt)  # linear backoff
        return wrapper
    return decorator

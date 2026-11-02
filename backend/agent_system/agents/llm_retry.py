"""Small retry helper for transient Groq rate-limit responses."""

import re
import time
from typing import Callable, TypeVar

T = TypeVar("T")


def invoke_with_retry(invoke: Callable[[], T], max_attempts: int = 3) -> T:
    """Retry rate-limited LLM calls with the provider's suggested delay."""
    for attempt in range(1, max_attempts + 1):
        try:
            return invoke()
        except Exception as exc:
            message = str(exc)
            is_rate_limit = "429" in message or "rate_limit_exceeded" in message
            if not is_rate_limit or attempt == max_attempts:
                raise
            wait_match = re.search(r"try again in ([\d.]+)s", message)
            time.sleep(float(wait_match.group(1)) + 1 if wait_match else 10)
    raise RuntimeError("LLM retry loop exited unexpectedly")

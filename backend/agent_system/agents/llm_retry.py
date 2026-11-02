"""Shared retry wrapper for Groq rate limits (HTTP 429).

Groq's on-demand tier has a low tokens-per-minute budget (8000 TPM at the
time this was written), and a single investigation can call it 4-5 times
across Monitoring, Diagnosis, Remediation and Fix. A 429 under that load is
expected, routine behavior, not an exceptional failure — it must be retried
with backoff, not allowed to crash the whole investigation with a 500.

Previously only the Fix agent retried on 429 (its own local
_invoke_with_retry); Monitoring, Diagnosis and Remediation called the LLM
directly and had no such protection, so a rate limit hit during any of
those earlier stages took down the entire pipeline. This module is the one
place that logic lives now, used by every agent that calls Groq.
"""

import re
import time
from typing import Callable, TypeVar

T = TypeVar("T")


def invoke_with_retry(call: Callable[[], T], max_attempts: int = 3) -> T:
    """Run `call()` (typically `lambda: llm.invoke(...)` or
    `lambda: agent.invoke(...)`), retrying with backoff only on a Groq
    429 rate-limit error. Any other exception, or a 429 on the last
    attempt, is re-raised immediately."""
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return call()
        except Exception as exc:
            last_exc = exc
            message = str(exc)
            is_rate_limit = "429" in message or "rate_limit_exceeded" in message
            if not is_rate_limit or attempt == max_attempts:
                raise
            wait_match = re.search(r"try again in ([\d.]+)s", message)
            wait_seconds = float(wait_match.group(1)) + 1.0 if wait_match else 10.0
            time.sleep(wait_seconds)
    raise last_exc

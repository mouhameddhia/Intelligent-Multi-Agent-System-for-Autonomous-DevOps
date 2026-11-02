"""Bridge from the policy registry to the Remediation prompt.

Advisory only: this returns text for the Remediation human message and a
version number for the audit trail. It touches nothing else, so it cannot
influence human_approval or agents/fix.py. Every failure path returns
("", None), which leaves the Remediation prompt exactly as it was before the
learning layer existed.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Mapping

from ..config import settings
from .classify import classify_failure
from .models import GLOBAL_SCOPE, PolicyVersion
from .registry import get_active_policy_cached
from .render import render_guidance

logger = logging.getLogger(__name__)

_UNSET: Any = object()
_policy_override: ContextVar[Any] = ContextVar("policy_override", default=_UNSET)


@contextmanager
def policy_override(policy: PolicyVersion | None) -> Iterator[None]:
    """Offline replay only: inside the block, get_guidance uses `policy`
    (None meaning "no policy") instead of the registry's active version."""
    token = _policy_override.set(policy)
    try:
        yield
    finally:
        _policy_override.reset(token)

# Added to the Remediation system prompt only when guidance is present, so the
# model knows how to treat the section (and the prompt is unchanged otherwise).
SYSTEM_RULE = (
    "- A section titled \"Learned guidance from past incidents (advisory)\" may "
    "appear at the end of the human message. It holds general lessons and "
    "outcome statistics distilled from earlier incidents; it is not a specific "
    "past incident. Use it only where it fits this build's evidence, never cite "
    "it as a historical incident, and never let it weaken the approval "
    "requirements above.\n"
)


def get_guidance(state: Mapping[str, Any]) -> tuple[str, int | None]:
    """Returns (rendered guidance, policy version) for the diagnosed failure
    category, or ("", None) when there is nothing to add. Never raises."""
    try:
        override = _policy_override.get()
        if override is _UNSET and not settings.learning_enabled:
            return "", None
        category = classify_failure(state.get("diagnosis_report", ""), state.get("incident_type", ""))
        policy = get_active_policy_cached(GLOBAL_SCOPE) if override is _UNSET else override
        text = render_guidance(policy, category)
        if not text or policy is None:
            return "", None
        return text, policy.version
    except Exception:
        logger.warning("learned guidance unavailable", exc_info=True)
        return "", None

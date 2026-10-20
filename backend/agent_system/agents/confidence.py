"""Single source of truth for agent confidence.

Three responsibilities, all pure functions (no LLM / network imports):

1. extract_agent_confidence(): pull the AGENT_CONFIDENCE marker out of an
   agent's text, tolerating the formats models actually produce.
2. monitoring_evidence_confidence(): deterministic fallback score for the
   Monitoring agent, used only when the model did not return a usable marker.
3. compute_overall_confidence() / summarize_confidence(): the one place where
   the overall score is calculated, shared by the API and the unified report.
"""
import re
from typing import Any, Iterable

# Finds the marker anywhere in the text (start of a line, or inline after other
# text). The value is captured loosely and normalised by _normalise().
#   AGENT_CONFIDENCE: 92        AGENT_CONFIDENCE = 92%
#   **AGENT_CONFIDENCE:** 92    AGENT_CONFIDENCE: 92/100
#   AGENT_CONFIDENCE: 0.92      AGENT_CONFIDENCE: 92 (high)
#   AGENT_CONFIDENCE:           <- truncated: marker removed, value is None
_MARKER = re.compile(
    r"(?P<lead>^[ \t]*[*_`#>\-]*[ \t]*)?"
    r"AGENT_CONFIDENCE[ \t]*[*_`]*[ \t]*[:=\-][ \t]*[*_`]*[ \t]*"
    r"(?P<value>\d{1,3}(?:\.\d+)?)?"
    r"(?:[ \t]*(?:%|/[ \t]*100))?"
    r"[^\n]*",
    re.IGNORECASE | re.MULTILINE,
)


def _normalise(raw: str | None) -> int | None:
    """'92' -> 92, '92.4' -> 92, '0.92' -> 92. Anything outside 0-100 -> None."""
    if raw is None:
        return None
    try:
        number = float(raw)
    except ValueError:
        return None
    # A decimal at or below 1 (0.92, 1.0) is a fraction; a bare "1" stays 1%.
    if "." in raw and number <= 1:
        number *= 100
    if not 0 <= number <= 100:
        return None
    return int(round(number))


_HISTORICAL_MARKER = re.compile(
    r"(?P<lead>^[ \t]*[*_`#>\-]*[ \t]*)?"
    r"HISTORICAL_MATCH_ID[ \t]*[*_`]*[ \t]*[:=\-][ \t]*[*_`]*[ \t]*"
    r"(?P<value>[0-9a-fA-F-]{8,36}|none)?"
    r"[^\n]*",
    re.IGNORECASE | re.MULTILINE,
)


def extract_historical_match_id(report: str) -> tuple[str, str | None]:
    """Return (report without the marker line, matched incident_id or None).
    Mirrors extract_agent_confidence's tolerant parsing exactly. 'none'
    (any case) is treated as explicitly no match, same as an absent marker."""
    text = report or ""
    match_id: str | None = None

    def _strip(match: re.Match) -> str:
        nonlocal match_id
        value = (match.group("value") or "").strip()
        if value and value.lower() != "none":
            match_id = value
        return ""

    cleaned = _HISTORICAL_MARKER.sub(_strip, text)
    cleaned = re.sub(r"[ \t]+$", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, match_id

def extract_agent_confidence(report: str) -> tuple[str, int | None]:
    """Return (report without the marker line, confidence or None).

    If the marker appears more than once, the last one wins. The marker text is
    always removed from the returned report, even when its value is unusable.
    """
    text = report or ""
    confidence: int | None = None

    def _strip(match: re.Match) -> str:
        nonlocal confidence
        parsed = _normalise(match.group("value"))
        if parsed is not None:
            confidence = parsed
        # Remove the marker (and anything trailing it on that line). Text that
        # came before an inline marker is left in place.
        return ""

    cleaned = _MARKER.sub(_strip, text)
    cleaned = re.sub(r"[ \t]+$", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, confidence


def _valid(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= 100
    )


def compute_overall_confidence(values: Iterable[Any]) -> int | None:
    """Mean of the valid scores. None only when no agent returned a valid score."""
    valid = [v for v in values if _valid(v)]
    return round(sum(valid) / len(valid)) if valid else None


def summarize_confidence(state: dict[str, Any]) -> tuple[dict[str, int | None], int | None]:
    """(per-agent scores, overall) from a final graph state."""
    scores = {
        "monitoring": state.get("monitoring_confidence"),
        "diagnosis": state.get("diagnosis_confidence"),
        "remediation": state.get("remediation_confidence"),
    }
    scores = {k: (int(round(v)) if _valid(v) else None) for k, v in scores.items()}
    return scores, compute_overall_confidence(scores.values())


# ---------------------------------------------------------------------------
# Deterministic Monitoring fallback
# ---------------------------------------------------------------------------

_METADATA_FIELDS = ("job_name", "build_number", "result", "timestamp", "duration_ms", "url")
_CODE_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def monitoring_evidence_confidence(evidence: dict[str, Any], report: str) -> int:
    """Score 0-100 from how much of the Monitoring report is backed by evidence.

    Used only when the model returned no usable AGENT_CONFIDENCE. Points:
      40  Jenkins metadata fields present (job, build, result, time, duration, url)
      20  console log present
      15  a Failed stage section with content
      25  Exact error lines that appear verbatim in the console log
    """
    jenkins = (evidence or {}).get("jenkins") or {}
    console_log = jenkins.get("console_log") or ""

    present = sum(1 for key in _METADATA_FIELDS if jenkins.get(key) not in (None, ""))
    score = 40 * present / len(_METADATA_FIELDS)

    if console_log.strip():
        score += 20

    if re.search(r"failed stage[^\n]*\n+\s*\S", report or "", re.IGNORECASE):
        score += 15

    quoted = [
        _squash(line)
        for block in _CODE_BLOCK.findall(report or "")
        for line in block.splitlines()
        if _squash(line)
    ]
    if quoted and console_log:
        haystack = _squash(console_log)
        score += 25 * sum(1 for line in quoted if line in haystack) / len(quoted)

    return int(round(score))
"""Turns the Remediation agent's output into structured, individually
addressable steps. Every later phase (per-step outcomes, ranking, learning)
needs stable step ids, so this is the foundation.

Preferred path: the Remediation agent itself emits a JSON steps block in the
same LLM call (see remediation.py), split out deterministically via
split_report_and_steps_json(). This is the only reliable path, because the
report's own section headers are numbered too, so regex-guessing "is this
line a step or a heading" is structurally ambiguous.

Fallback path (parse_numbered_steps / _llm_steps below): used only when the
model did not emit the delimiter block — e.g. the no-evidence tool-calling
demo path, which still uses the free-text SYSTEM_PROMPT. Kept for
compatibility, not relied on for the main evidence-grounded path.
"""

import json
import re

from langchain_groq import ChatGroq

from ..config import settings
from ..orchestrator.state import InvestigationState

MAX_STEPS = 8

# The Remediation agent is asked to emit the JSON steps block FIRST, wrapped
# in these two markers, before the human-readable narrative. This way, if the
# model hits its token limit on a long narrative, the steps are already
# captured before truncation can touch them.
STEP_DELIMITER_START = "===STEPS_JSON_START==="
STEP_DELIMITER_END = "===STEPS_JSON_END==="

_STEP = re.compile(
    r"^\s*(?:[#>]+\s*)?(?:[-*+]\s+)?(?:\|\s*)?(?:\*\*|__)?\s*(?:step\s*)?"
    r"(\d{1,2})\s*(?:\\?[.):]|\|)\s*(?:\*\*|__)?\s*(.+\S)\s*$",
    re.IGNORECASE,
)
_DISRUPTIVE = re.compile(
    r"\b(restart|reboot|redeploy|deploy|roll\s?back|revert|delete|drop|force|"
    r"production|prod|terminate|scale down|kill)\b",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    text = re.sub(r"\*\*|__|`", "", text)
    text = re.sub(r"\s*\|\s*$", "", text)
    return text.strip()


def build_step_dicts(texts: list[str]) -> list[dict]:
    """Turn plain step strings into the structured dicts stored in state/Mongo.
    "followed" and "solved" are added later, by the feedback endpoint, once
    the engineer reports what actually happened."""
    return [
        {
            "id": f"s{i}",
            "text": text,
            "order_shown": i,
            "disruptive": bool(_DISRUPTIVE.search(text)),
        }
        for i, text in enumerate(texts, start=1)
    ]


def split_report_and_steps_json(raw_output: str) -> tuple[str, list[str]]:
    """Split the Remediation agent's raw output into (clean prose report,
    list of step strings). The agent is asked to emit the JSON block FIRST,
    between STEP_DELIMITER_START and STEP_DELIMITER_END, so that truncation
    on a long narrative can never cut off the steps themselves. Returns the
    full text with an empty step list if the start marker is missing or the
    JSON is malformed, so the caller can fall back to legacy parsing."""
    raw_output = raw_output or ""
    if STEP_DELIMITER_START not in raw_output:
        return raw_output.strip(), []

    before, _, after_start = raw_output.partition(STEP_DELIMITER_START)

    if STEP_DELIMITER_END in after_start:
        json_part, _, narrative_part = after_start.partition(STEP_DELIMITER_END)
    else:
        # End marker missing — most likely the JSON itself got truncated.
        # Still attempt to parse what we have; no narrative survives this case.
        json_part, narrative_part = after_start, ""

    json_part = json_part.strip()
    json_part = re.sub(r"^```(?:json)?\s*", "", json_part)
    json_part = re.sub(r"\s*```\s*$", "", json_part)

    start, end = json_part.find("["), json_part.rfind("]")
    texts: list[str] = []
    if start != -1 and end > start:
        try:
            items = json.loads(json_part[start : end + 1])
            texts = [_clean(i) for i in items if isinstance(i, str) and len(_clean(i)) >= 8][:MAX_STEPS]
        except json.JSONDecodeError:
            texts = []

    narrative = (before.strip() + "\n\n" + narrative_part.strip()).strip()
    if not narrative:
        narrative = raw_output.strip()

    return narrative, texts


# ---------------------------------------------------------------------------
# Fallback parsing (used only when the delimiter block is absent).
# ---------------------------------------------------------------------------

def parse_numbered_steps(report: str) -> list[str]:
    """Accepts '1. x', '1) x', '**1. x**', '### 1. x', 'Step 1: x', '- 1. x'
    and markdown table rows like '| 1 | x | owner |'. Only the first numbered
    sequence is used, so a second list further down (e.g. prevention) is ignored.
    This is a best-effort fallback, not the primary path — see module docstring."""
    steps: list[str] = []
    for line in (report or "").splitlines():
        match = _STEP.match(line)
        if not match:
            continue
        number, rest = int(match.group(1)), match.group(2)
        if number == 1 and steps:
            break
        if "|" in rest:
            cells = [c.strip() for c in rest.split("|") if c.strip()]
            rest = " - ".join(cells[:2])
        cleaned = _clean(rest)
        if len(cleaned) >= 8:
            steps.append(cleaned)
    return steps[:MAX_STEPS]


def _llm_steps(report: str) -> list[str]:
    llm = ChatGroq(
        model=settings.llm_model,
        api_key=settings.groq_api_key,
        temperature=0,
        max_tokens=700,
        model_kwargs={"include_reasoning": False},
    )
    result = llm.invoke([
        (
            "system",
            "Extract the remediation steps from the report as a JSON array of "
            "strings, in the order given. Copy their meaning faithfully; do not "
            "add, merge or invent steps. Return only the JSON array.",
        ),
        ("human", report),
    ])
    content = (result.content or "").strip()
    start, end = content.find("["), content.rfind("]")
    if start == -1 or end <= start:
        return []
    items = json.loads(content[start : end + 1])
    return [_clean(i) for i in items if isinstance(i, str) and len(_clean(i)) >= 8][:MAX_STEPS]


def structure_steps(report: str) -> list[dict]:
    """Legacy fallback entry point: parse steps out of free-text prose."""
    texts = parse_numbered_steps(report)
    if len(texts) < 2 and (report or "").strip():
        try:
            texts = _llm_steps(report) or texts
        except Exception:
            pass
    return build_step_dicts(texts)


def run_structure_steps(state: InvestigationState) -> InvestigationState:
    """No-op pass-through if remediation.py already produced structured steps
    (the preferred, evidence-grounded path). Only falls back to legacy prose
    parsing when steps are still empty — e.g. the no-evidence tool-calling
    demo path."""
    if state.get("steps"):
        return state
    return {**state, "steps": structure_steps(state.get("remediation_report", ""))}

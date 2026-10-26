"""Independent review of a generated code fix, using a different provider
(Gemini) than the one that wrote the fix (Groq), before it is ever pushed
to GitHub.

Fails CLOSED: any judge error — missing API key, network failure, HTTP
error, blocked/empty response, or a response with no parseable verdict —
is treated as a rejection. A judge outage must never result in an
unreviewed push; it must only ever make pushes rarer.
"""

import difflib
import json
import re

import httpx

from ..config import settings

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Tolerant of the same formatting variance as confidence.py's AGENT_CONFIDENCE
# marker: "JUDGE_VERDICT: pass", "**JUDGE_VERDICT:** fail", "JUDGE_VERDICT - pass", etc.
_VERDICT_MARKER = re.compile(
    r"(?P<lead>^[ \t]*[*_`#>\-]*[ \t]*)?"
    r"JUDGE_VERDICT[ \t]*[*_`]*[ \t]*[:=\-][ \t]*[*_`]*[ \t]*"
    r"(?P<value>pass|fail)[^\n]*",
    re.IGNORECASE | re.MULTILINE,
)


def extract_judge_verdict(text: str) -> tuple[str, str | None]:
    """Return (reasoning without the marker line, verdict 'pass'/'fail' or None).

    If the marker appears more than once, the last one wins. Mirrors
    confidence.extract_agent_confidence's tolerant parsing style.
    """
    text = text or ""
    verdict: str | None = None

    def _strip(match: re.Match) -> str:
        nonlocal verdict
        verdict = match.group("value").lower()
        return ""

    cleaned = _VERDICT_MARKER.sub(_strip, text)
    cleaned = re.sub(r"[ \t]+$", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, verdict


def _build_diff(original: str, revised: str, filename: str) -> str:
    diff_lines = difflib.unified_diff(
        (original or "").splitlines(keepends=True),
        (revised or "").splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    )
    return "".join(diff_lines)


def _build_judge_prompt(
    target_file: str,
    original_content: str,
    new_content: str,
    test_filename: str | None,
    test_content: str | None,
    diagnosis_report: str,
    remediation_report: str,
) -> str:
    diff = _build_diff(original_content, new_content, target_file)

    test_block = ""
    if test_content:
        test_block = f"""

TEST FILE ({test_filename}) — ground truth for correct behavior:
{test_content}
"""

    return f"""You are an independent, skeptical reviewer auditing an automated code fix
before it is pushed to a real GitHub repository. You did not write this fix
— a different model did — and your only job is to catch mistakes it might
have made, not to re-derive the fix yourself.

FILE BEING FIXED: {target_file}

ORIGINAL (BROKEN) FILE:
{original_content}
{test_block}
BUG DIAGNOSIS:
{diagnosis_report}

REMEDIATION PLAN:
{remediation_report}

PROPOSED DIFF (unified format: lines starting with - are removed, + are added):
{diff}

Reject the fix if any of the following is true:
- It does not actually fix the diagnosed bug.
- It changes behavior unrelated to the bug, or deletes/breaks working code.
- It overfits to one specific test assertion instead of the general bug
  described in the diagnosis (unless a test is explicitly marked in its own
  comments as an intentional/control failure that should stay failing).
- It introduces an obvious syntax error or incomplete code.
- It contradicts something the test file asserts, other than an explicitly
  intentional failing test.

Otherwise, approve it. Respond with EXACTLY this format: the first line must
be "JUDGE_VERDICT: pass" or "JUDGE_VERDICT: fail", followed by 1-3 sentences
explaining your decision.
"""


def _call_gemini(prompt_text: str) -> str:
    """Call Gemini's generateContent endpoint. Raises on any failure —
    missing key, network error, HTTP error, blocked/empty response."""
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    url = f"{GEMINI_API_BASE}/{settings.gemini_model}:generateContent"

    try:
        resp = httpx.post(
            url,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": settings.gemini_api_key,
            },
            json={
                "contents": [{"parts": [{"text": prompt_text}]}],
                "generationConfig": {"temperature": 0},
            },
            timeout=30.0,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"Gemini returned HTTP {exc.response.status_code}: {exc.response.text[:300]}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"Could not reach Gemini: {exc}") from exc

    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {json.dumps(data)[:300]}")

    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    if not text.strip():
        raise RuntimeError("Gemini returned an empty response.")
    return text


def judge_fix(
    *,
    target_file: str,
    original_content: str,
    new_content: str,
    test_filename: str | None,
    test_content: str | None,
    diagnosis_report: str,
    remediation_report: str,
) -> dict:
    """Review a generated fix. Always returns a dict with a usable
    "verdict" of exactly "pass" or "fail" — callers only need to check
    `result["verdict"] != "pass"` to stay safe; they never need to handle
    a third "error" case separately, because errors are folded into "fail"
    here (source distinguishes why: "gemini", "unparsed" or "error")."""
    prompt = _build_judge_prompt(
        target_file,
        original_content,
        new_content,
        test_filename,
        test_content,
        diagnosis_report,
        remediation_report,
    )

    try:
        raw_text = _call_gemini(prompt)
    except Exception as exc:
        return {
            "verdict": "fail",
            "reasoning": f"Judge could not be reached, rejecting the fix as a precaution: {exc}",
            "source": "error",
        }

    reasoning, verdict = extract_judge_verdict(raw_text)
    if verdict not in ("pass", "fail"):
        return {
            "verdict": "fail",
            "reasoning": (
                "Judge did not return a usable verdict, rejecting the fix as a "
                f"precaution. Raw response: {(reasoning or raw_text).strip()[:500]}"
            ),
            "source": "unparsed",
        }

    return {
        "verdict": verdict,
        "reasoning": reasoning or "(no reasoning given)",
        "source": "gemini",
    }

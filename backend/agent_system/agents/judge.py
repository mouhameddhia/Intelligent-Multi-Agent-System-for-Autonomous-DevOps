"""LLM-as-judge: audits Monitoring, Diagnosis and Remediation against the
evidence, scoring each agent and flagging hallucination or gaps. Runs on a
different model family (Gemini) than the rest of the pipeline (Groq), by
design — a model should not grade its own output. Purely informative: a
judge failure of any kind must never break the investigation pipeline."""

import json
import re

from langchain_google_genai import ChatGoogleGenerativeAI

from .evidence_utils import compact_evidence
from ..config import settings
from ..orchestrator.state import InvestigationState

JUDGE_DELIMITER_START = "===JUDGE_JSON_START==="
JUDGE_DELIMITER_END = "===JUDGE_JSON_END==="

SYSTEM_PROMPT = f"""\
You are an independent auditor in a DevOps investigation pipeline. You did
NOT write the Monitoring, Diagnosis, or Remediation reports below — you are
reviewing them critically against the actual evidence.

Your job:
1. Check each report's claims against the supplied evidence (and, for
   Diagnosis, against the Historical Incident block if one is present).
2. Score each agent 0-100 on how well its claims are actually supported by
   evidence (not writing quality, and not the same thing as that agent's own
   self-reported confidence, which you do not see).
3. Flag hallucination: any specific claim (a file, a line, a commit, a
   metric, a quoted log line) that does NOT appear in the supplied evidence.
4. Flag gaps: evidence that was available but ignored or contradicted by a
   report.
5. If a HISTORICAL INCIDENT block is supplied, separately score how well the
   claimed match actually holds up (0-100): the agents already decided
   match/no-match — you are grading whether that decision was sound given
   both diagnosis reports' stated root causes.

Rules:
- Base every judgment ONLY on the supplied evidence and reports. Do not use
  outside knowledge of this codebase, and do not invent what "should" have
  happened.
- Be specific: every issue you list must name the exact unsupported claim or
  ignored evidence, not a vague impression.
- If a HISTORICAL INCIDENT block is NOT supplied, historical_match_quality
  must be null and you must not comment on history at all.

OUTPUT FORMAT — in exactly this order, nothing before it:

STEP 1 — write exactly this marker on its own line:
{JUDGE_DELIMITER_START}
Then, on the following line(s), write ONLY a single JSON object, no markdown
fences, no trailing commas, with this exact shape:
{{
  "scores": {{"monitoring": NN, "diagnosis": NN, "remediation": NN}},
  "historical_match_quality": NN or null,
  "hallucination_detected": true or false,
  "issues": ["specific unsupported claim or ignored evidence, each under 25 words", ...],
  "conclusion": "one concise sentence summarizing your verdict"
}}
Then write exactly this marker on its own line:
{JUDGE_DELIMITER_END}

STEP 2 — after that block, write a brief reasoning paragraph (3-6 sentences)
explaining the scores and any flagged issues. Do not repeat the JSON content
verbatim; add the reasoning behind it.

The JSON block (STEP 1) must always be complete and valid, even if STEP 2
must be shortened to fit.
"""


def _historical_block(historical_incident: dict | None) -> str:
    if not historical_incident:
        return ""
    return (
        "\n\nHISTORICAL INCIDENT (claimed by Diagnosis as a root-cause match):",
        f"- Previous incident: {historical_incident.get('job_name', 'Unknown job')} "
        f"build #{historical_incident.get('build_number', '?')}",
        f"- Incident id: {historical_incident.get('incident_id', '')}",
        f"- Exact evidence signature match (recomputed, not Diagnosis's claim): "
        f"{historical_incident.get('exact_signature_match')}",
        f"- Recorded resolution method: {historical_incident.get('resolution_method', 'Unknown')}",
    )


def _parse_judge_output(raw_output: str) -> tuple[str, dict]:
    """Returns (reasoning_text, parsed_json_dict). parsed_json_dict is {} if
    the block is missing or malformed — caller treats every field as
    unavailable, same convention as extract_agent_confidence returning None."""
    raw_output = raw_output or ""
    if JUDGE_DELIMITER_START not in raw_output:
        return raw_output.strip(), {}

    before, _, after_start = raw_output.partition(JUDGE_DELIMITER_START)
    if JUDGE_DELIMITER_END in after_start:
        json_part, _, reasoning_part = after_start.partition(JUDGE_DELIMITER_END)
    else:
        json_part, reasoning_part = after_start, ""

    json_part = json_part.strip()
    json_part = re.sub(r"^```(?:json)?\s*", "", json_part)
    json_part = re.sub(r"\s*```\s*$", "", json_part)

    start, end = json_part.find("{"), json_part.rfind("}")
    parsed: dict = {}
    if start != -1 and end > start:
        try:
            candidate = json.loads(json_part[start : end + 1])
            if isinstance(candidate, dict):
                parsed = candidate
        except json.JSONDecodeError:
            parsed = {}

    reasoning = (before.strip() + "\n\n" + reasoning_part.strip()).strip()
    if not reasoning:
        # The JSON block started but likely got truncated before a usable
        # object could be parsed (no balanced {...} found) — never show the
        # raw delimiter markers or dangling JSON fragment to the user.
        reasoning = (
            "_Judge response was truncated before a complete verdict could "
            "be parsed. Scores and flags are unavailable for this incident._"
        ) if JUDGE_DELIMITER_START in raw_output else raw_output.strip()
    return reasoning, parsed


def _valid_score(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100


def _unavailable_state(state: InvestigationState, reason: str) -> InvestigationState:
    return {
        **state,
        "judge_verdict": f"_Judge unavailable: {reason}_",
        "judge_scores": {"monitoring": None, "diagnosis": None, "remediation": None},
        "judge_historical_match_quality": None,
        "judge_hallucination_detected": None,
        "judge_issues": [],
    }

def _extract_text(content) -> str:
    """ChatGroq's .content is always a plain string. ChatGoogleGenerativeAI's
    .content can instead be a list of parts when thinking is enabled (e.g. a
    reasoning/thought part plus the actual text part) — this normalizes both
    shapes to plain text, keeping only text parts and dropping thought/other
    non-text parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                # Skip thought parts explicitly if the SDK marks them; keep
                # everything else that has text.
                if item.get("type") == "thinking" or item.get("thought") is True:
                    continue
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""
def run_llm_judge(state: InvestigationState) -> InvestigationState:
    evidence = state.get("evidence") or {}
    historical_incident = state.get("historical_incident")

    message = (
        f"Audit the investigation of Jenkins job {state['job_name']} build "
        f"#{state['build_number']}.\n\n"
        f"EVIDENCE:\n{json.dumps(compact_evidence(evidence, include_console_log=True), separators=(',', ':'), default=str)}\n\n"
        f"MONITORING REPORT:\n{state.get('monitoring_report', '')}\n\n"
        f"DIAGNOSIS REPORT:\n{state.get('diagnosis_report', '')}\n\n"
        f"REMEDIATION REPORT:\n{state.get('remediation_report', '')}\n\n"
        f"PROPOSED STEPS:\n{json.dumps([s.get('text', '') for s in state.get('steps') or []])}"
        f"{_historical_block(historical_incident)}"
    )

    try:
        llm = ChatGoogleGenerativeAI(
            model=settings.judge_model,
            google_api_key=settings.gemini_api_key,
            temperature=0,
            max_output_tokens=4000,
            timeout=45,          # hard ceiling: never hang the pipeline waiting on Gemini
            max_retries=1,       # don't silently multiply the wait on transient errors
            thinking_budget=1500,  # cap reasoning tokens directly, separate from output
        )
        result = llm.invoke([
            ("system", SYSTEM_PROMPT),
            ("human", message),
        ])
        raw = _extract_text(result.content)
        if not raw.strip():
            raise ValueError(
                f"Judge model returned no usable text content (raw type was {type(result.content).__name__})"
            )
    except Exception as exc:
        # Purely informative / non-blocking: never let a judge failure
        # (bad API key, quota, network, malformed response) break the
        # investigation pipeline.
        return _unavailable_state(state, str(exc))

    reasoning, parsed = _parse_judge_output(raw)

    raw_scores = parsed.get("scores") or {}
    scores = {
        key: (int(round(raw_scores[key])) if _valid_score(raw_scores.get(key)) else None)
        for key in ("monitoring", "diagnosis", "remediation")
    }

    hist_quality = parsed.get("historical_match_quality")
    hist_quality = int(round(hist_quality)) if _valid_score(hist_quality) else None

    hallucination = parsed.get("hallucination_detected")
    hallucination = bool(hallucination) if isinstance(hallucination, bool) else None

    issues_raw = parsed.get("issues")
    issues = [str(i) for i in issues_raw if isinstance(i, str) and i.strip()] if isinstance(issues_raw, list) else []

    conclusion = parsed.get("conclusion")
    verdict = reasoning.strip()
    if isinstance(conclusion, str) and conclusion.strip():
        verdict = (f"**Conclusion:** {conclusion.strip()}\n\n{verdict}").strip()

    return {
        **state,
        "judge_verdict": verdict or "_No reasoning returned._",
        "judge_scores": scores,
        "judge_historical_match_quality": hist_quality,
        "judge_hallucination_detected": hallucination,
        "judge_issues": issues,
    }
import json

from langchain_groq import ChatGroq

from .evidence_utils import compact_evidence
from .confidence import extract_agent_confidence, extract_historical_match_id
from ..config import settings
from ..orchestrator.state import InvestigationState
from ..historical_match import get_candidate_incidents, resolve_historical_match


def _compact_diagnosis_evidence(evidence: dict) -> dict:
    """Keep decisive build evidence while bounding optional provider detail."""
    bounded = compact_evidence(
        evidence,
        include_console_log=True,
        patch_limit=1200,
        file_list_limit=40,
        console_log_limit=1800,
    )

    github = bounded.get("github")
    files = github.get("files") if isinstance(github, dict) else None
    if isinstance(files, list):
        # Preserve changed-file metadata, but include full diffs only for likely
        # implementation and test files to avoid repeating large patch text.
        ranked = sorted(
            (
                (
                    0 if str(item.get("filename", "")).rsplit("/", 1)[-1].lower()
                    in {"app.py", "test_app.py"}
                    else 1 if str(item.get("filename", "")).lower().endswith(".py")
                    else 2,
                    index,
                )
                for index, item in enumerate(files)
                if isinstance(item, dict)
                and isinstance(item.get("patch"), str)
                and item.get("patch")
            )
        )
        selected = {index for _, index in ranked[:2]}
        for index, item in enumerate(files):
            if not isinstance(item, dict):
                continue
            if index in selected:
                item["patch_status"] = "included"
            elif item.pop("patch", None):
                item["patch_status"] = "omitted_by_token_budget"

    loki = bounded.get("loki")
    if isinstance(loki, dict) and isinstance(loki.get("lines"), list):
        loki["lines"] = [
            {**entry, "line": str(entry.get("line", ""))[-400:]}
            for entry in loki["lines"][-4:]
            if isinstance(entry, dict)
        ]

    sonarqube = bounded.get("sonarqube")
    if isinstance(sonarqube, dict) and isinstance(sonarqube.get("issues"), list):
        sonarqube["issues"] = [
            {
                key: str(issue.get(key, ""))[:240]
                for key in ("severity", "type", "rule", "component", "line", "status", "message")
                if issue.get(key) is not None
            }
            for issue in sonarqube["issues"][:4]
            if isinstance(issue, dict)
        ]

    metrics = bounded.get("metrics")
    if isinstance(metrics, list):
        for metric in metrics:
            if not isinstance(metric, dict) or not isinstance(metric.get("results"), list):
                continue
            metric["results"] = metric["results"][:2]
            for result in metric["results"]:
                if isinstance(result, dict) and isinstance(result.get("values"), list):
                    result["values"] = result["values"][-3:]

    return bounded

SYSTEM_PROMPT = """\
You are a Diagnosis Agent in a DevOps investigation pipeline.

You receive a Monitoring Report describing what failed in a Jenkins build.
Your role is to find out WHY it failed using only the current Monitoring Report and supplied live provider evidence. You have no access to historical incidents, past fixes, or memory.

Write a structured Diagnosis Report that includes:
1. Root cause: the most likely reason for the failure (specific commit, file, or pattern)
2. Conclusion: one concise sentence summarizing the likely cause and its impact
3. Evidence: which data points support this conclusion (commit SHA, files changed, history)
4. End with AGENT_CONFIDENCE: NN (integer 0-100), estimating how strongly current evidence supports the root-cause conclusion. Do not create a separate confidence section.
"""


def run_diagnosis(state: InvestigationState) -> InvestigationState:
    llm = ChatGroq(
        model=settings.llm_model,
        api_key=settings.groq_api_key,
        temperature=0,
        max_tokens=1200,
        model_kwargs={"include_reasoning": False},
    )

    evidence = state.get("evidence") or {}

    if evidence:
        historical_candidates = get_candidate_incidents(
            evidence, state["job_name"], limit=2, user_id=state.get("user_id")
        )
        grounded_prompt = """You are a DevOps diagnosis agent.
Analyze only the supplied Monitoring Report and provider evidence.

The backend has supplied up to two past RESOLVED incident candidates, if any.
You do not have tools and must not claim to have made additional searches.

Rules:
- First form your own root-cause hypothesis from the Monitoring Report and
  current provider evidence. Then compare it with each supplied candidate.
- For each candidate, compare its
  diagnosis report and bounded provider evidence to your OWN hypothesis and
  judge, in your own words, whether the root-cause MECHANISM is the same — a
  shared test name or job alone is not sufficient evidence of a match.
- exact_signature_match means only that the normalized failed test identifier
  and assertion text match. It is symptom-level evidence, not proof of the
  same root cause. If the historical diagnosis and evidence do not establish
  the same mechanism, do not claim a root-cause match.
- If exactly one candidate is a genuine root-cause match, end with
  HISTORICAL_MATCH_ID: <that candidate's incident_id>. If several could
  match, pick the strongest (prefer exact_signature_match=true) and justify
  it. If no candidate is a genuine match, or none were returned, end with
  HISTORICAL_MATCH_ID: none.
- The GitHub commit is relevant only if the supplied lookup matched the build SHA.
- SonarQube issues and measures describe the selected project's most recent
  analysis. Treat them as build-specific only when `analysis_matches_build`
  is true; otherwise use them only as project context and do not claim they
  caused this build's failure.
- A changed file is a lead, not proof that it caused the failure.
- Do not name a commit as introducing the defect unless the supplied diff shows
  the relevant code change. When source code is absent, say the code path was
  not inspected and label any mechanism as a hypothesis.
- Do not invent commit details, build history, or failure patterns.

FORMAT RULES:
- Write a thorough analysis in markdown with these sections:
  **Root cause** (state the most specific file, function, commit, and mechanism
  that the supplied evidence supports; omit unavailable details rather than
  guessing),
  **Historical confirmation** (only if at least one candidate was supplied:
  name which candidate you compared against — job and build number — and
  whether its root cause matches yours, and why; omit this section entirely
  if no candidates were supplied),
  **Conclusion** (one concise sentence summarizing the likely cause and its impact),
  **Evidence** (each point tied to concrete current signals: console error, matching commit evidence and diff statistics, or measured metrics),
  End with exactly these two machine-readable lines, in this order:
  HISTORICAL_MATCH_ID: <incident_id or none>
  AGENT_CONFIDENCE: NN (integer 0-100)
  Do not create separate sections titled Ruled out, Contributing factors, or Confidence.
- Build on the Monitoring Report: refer to its findings instead of copying it word
  for word, but do not omit any reasoning that supports the root cause.
"""
        message = (
            f"Investigate Jenkins job {state['job_name']} build "
            f"#{state['build_number']} using the Monitoring Report and this "
            f"provider evidence bundle:\n\n"
            f"MONITORING REPORT:\n{state['monitoring_report']}\n\n"
            f"PROVIDER EVIDENCE:\n{json.dumps(_compact_diagnosis_evidence(evidence), separators=(',', ':'), default=str)}\n\n"
            f"BACKEND-RETRIEVED HISTORICAL CANDIDATES:\n{json.dumps(historical_candidates, separators=(',', ':'), default=str)}"
        )
        result = llm.invoke([
            ("system", grounded_prompt),
            ("human", message),
        ])
        report = result.content
        if not isinstance(report, str):
            raise TypeError(f"Expected string from diagnosis agent, got {type(report).__name__}")
    else:
        # Legacy path is LLM-only: no historical tools or memory.
        message = (
            f"Monitoring Report:\n{state['monitoring_report']}\n\n"
            f"Diagnose the current failure for Jenkins job: {state['job_name']}, build #{state['build_number']}. Use only current supplied evidence."
        )
        result = llm.invoke([
            ("system", SYSTEM_PROMPT + "\nDo not access historical incidents or memory."),
            ("human", message),
        ])
        report = result.content

    report, match_id = extract_historical_match_id(report)
    report, confidence = extract_agent_confidence(report)
    historical_incident = resolve_historical_match(
        match_id, evidence, user_id=state.get("user_id")
    )
    return {
        **state,
        "diagnosis_report": report,
        "diagnosis_confidence": confidence,
        "historical_incident": historical_incident,
    }

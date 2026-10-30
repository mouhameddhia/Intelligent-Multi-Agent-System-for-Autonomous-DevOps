import json
import time

from langchain_groq import ChatGroq
from langchain.agents import create_agent

from ..config import settings
from ..llm_log import record_call, append_call
from ..orchestrator.state import InvestigationState
from ..tools.build_tools import make_remediation_tools
from .steps import STEP_DELIMITER_START, STEP_DELIMITER_END, split_report_and_steps_json, build_step_dicts
from .confidence import extract_agent_confidence

SYSTEM_PROMPT = """\
You are a Remediation Agent in a DevOps investigation pipeline.

You receive a Monitoring Report and a Diagnosis Report for a failed Jenkins build.
Your role is to propose concrete, safe remediation using only the current Monitoring Report and Diagnosis Report. You have no access to historical incidents, past fixes, or memory.

Write a structured Remediation Report that includes:
1. Recommended action: the single most important thing to do right now
2. Step-by-step plan: numbered steps based on the runbook and past fixes
3. Owner: who should act on this (name and role, from team contacts)
4. Automation safety: which steps are safe to automate vs which require human approval
5. Priority: critical / high / medium with justification
6. End with AGENT_CONFIDENCE: NN (integer 0-100), estimating confidence that this proposal is safe and supported by current evidence.
"""

def _historical_context_block(historical_incident: dict | None) -> str:
    """historical_incident, when present, was confirmed by the Diagnosis
    agent and verified server-side — Remediation must NOT call any tool for
    this, it only reads this context."""
    if not historical_incident:
        return ""
    method = historical_incident.get("resolution_method", "Unknown")
    actions_text = "\n".join(
        f"- {a.get('text', '')}" for a in (historical_incident.get("actions") or []) if a.get("text")
    )
    past_steps_text = "\n".join(
        f"- {s.get('text', '')}" for s in (historical_incident.get("steps") or []) if s.get("text")
    )
    parts = [
        "\n\nHISTORICAL CONTEXT (Diagnosis confirmed this build's root cause "
        "matches a previously RESOLVED incident):",
        f"- Previous incident: {historical_incident.get('job_name', 'Unknown job')} "
        f"build #{historical_incident.get('build_number', '?')}",
        f"- Recorded resolution method: {method}",
    ]
    if actions_text:
        parts.append(f"- Recorded outcome of that resolution:\n{actions_text}")
    if past_steps_text:
        parts.append(f"- Steps proposed for that incident at the time:\n{past_steps_text}")
    return "\n".join(parts) + "\n"

GROUNDED_PROMPT = f"""You are a DevOps remediation advisor.
Prepare a safe, evidence-based proposal for the supplied Jenkins failure.

Rules:
- Do not call tools in this evidence-based path.
- Do not invent historical fixes, runbooks, team members, or on-call contacts.
- This is a proposal only; never claim an action was executed BY YOU — if
  HISTORICAL CONTEXT shows the past incident was resolved automatically, say
  the past one was handled automatically; do not claim you are applying it now.
- Recommend reversible diagnostic steps first.
- Require human approval for deployments, restarts, destructive changes,
  production modifications, or other disruptive actions.
- A HISTORICAL CONTEXT section may appear at the end of the human message:
  a previously resolved incident Diagnosis confirmed matches this build's
  root cause. You have no tool to look this up yourself — use only what is
  given. If present, your Step 2 proposal's Recommended action must state
  that this matches that previous incident, name its resolution method, and
  your steps should reuse/adapt its recorded actions where they still apply
  to this build. If no HISTORICAL CONTEXT is supplied, do not reference
  historical incidents, past fixes, or memory.
- Base this proposal on the current Monitoring Report and Diagnosis Report,
  and on HISTORICAL CONTEXT when supplied.

- Write your output in exactly this order, with nothing before it:

  STEP 1 of your output — write exactly this marker on its own line:
  {STEP_DELIMITER_START}
  Then, on the following line(s), write ONLY a JSON array of strings: the
  concrete remediation steps from your step-by-step plan, in the order you
  propose them, one string per step. Do not number the strings yourself.
  Then write exactly this marker on its own line:
  {STEP_DELIMITER_END}

  STEP 2 of your output - after that block, write a detailed but focused
  remediation proposal in markdown with the sections below. Do not repeat the
  step list: the steps already exist in the JSON block above.
    **Recommended action:** the single most important thing to do now, and why it
    addresses the diagnosed root cause (2-3 sentences). If HISTORICAL CONTEXT was
    supplied, state here that this matches that previous incident and its
    recorded resolution method (manual or automatic), and that your steps
    reuse it.
    **Owner:** role or team (use team contacts only if they were supplied).
    **Automation safety:** which steps are safe for PipeGuard to automate, which need
    a human, and why.
    **How to verify:** the concrete check that proves the fix worked (which test or
    build result to look for).
    End with AGENT_CONFIDENCE: NN (integer 0-100). Do not add sections titled How the steps work together, Risks and uncertainty, or Prevention.
  Do not assign priority labels. Be specific to THIS build (file, function, test
  and commit names), never generic.

  The JSON block (STEP 1) must always be complete and never be skipped or
  shortened, even if STEP 2 must stay brief to fit.
"""


def build_remediation_messages(state: InvestigationState) -> list[tuple[str, str]]:
    """Prompt for the evidence-grounded path. training/ reuses it, so
    fine-tuning examples match the production prompt exactly."""
    message = (
        f"Prepare a remediation proposal for Jenkins job {state['job_name']} "
        f"build #{state['build_number']}.\n\n"
        f"MONITORING REPORT:\n{state['monitoring_report']}\n\n"
        f"DIAGNOSIS REPORT:\n{state['diagnosis_report']}"
        f"{_historical_context_block(state.get('historical_incident'))}"
    )
    return [("system", GROUNDED_PROMPT), ("human", message)]


def run_remediation(state: InvestigationState) -> InvestigationState:
    llm = ChatGroq(
        model=settings.llm_model,
        api_key=settings.groq_api_key,
        temperature=0,
        max_tokens=2200,
        model_kwargs={"include_reasoning": settings.llm_include_reasoning},
    )

    evidence = state.get("evidence") or {}

    if evidence:
        messages = build_remediation_messages(state)
        started = time.perf_counter()
        result = llm.invoke(messages)
        raw_output = result.content

        report_text, step_texts = split_report_and_steps_json(raw_output)
        report_text, confidence = extract_agent_confidence(report_text)
        steps = build_step_dicts(step_texts) if step_texts else []

        return {
            **state,
            "remediation_report": report_text,
            "remediation_confidence": confidence,
            "steps": steps,
            "llm_calls": append_call(state, record_call("remediation", messages, result, started)),
        }

    # Legacy path is LLM-only: no past-fix tools or memory.
    message = (
        f"Monitoring Report:\n{state['monitoring_report']}\n\n"
        f"Diagnosis Report:\n{state['diagnosis_report']}\n\n"
        f"Recommend safe remediation for current Jenkins job: {state['job_name']}, build #{state['build_number']}. Use only current reports."
    )
    result = llm.invoke([
        ("system", SYSTEM_PROMPT + "\nDo not access historical incidents or memory."),
        ("human", message),
    ])
    report = result.content

    report, confidence = extract_agent_confidence(report)
    return {**state, "remediation_report": report, "remediation_confidence": confidence}

import json

from langchain_groq import ChatGroq
from langchain.agents import create_agent

from .evidence_utils import compact_evidence
from .confidence import extract_agent_confidence, extract_historical_match_id
from ..config import settings
from ..orchestrator.state import InvestigationState
from ..historical_match import resolve_historical_match
from ..tools.build_tools import make_historical_search_tool

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
        max_tokens=1600,
        model_kwargs={"include_reasoning": False},
    )

    evidence = state.get("evidence") or {}

    if evidence:
        grounded_prompt = """You are a DevOps diagnosis agent.
Analyze only the supplied Monitoring Report and provider evidence.

You have ONE tool: search_similar_incidents. It searches for past RESOLVED
incidents whose evidence matches this build's evidence.

Rules:
- First form your own root-cause hypothesis from the Monitoring Report and
  provider evidence alone, before using the tool.
- Then you MUST call search_similar_incidents EXACTLY ONCE, even if you are
  already confident, to check whether history confirms or conflicts with
  your hypothesis. Do not skip it and do not call it more than once.
- If it returns one or more candidates, compare EACH candidate's
  diagnosis_report and bounded provider evidence to your OWN hypothesis and
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
- A changed file is a lead, not proof that it caused the failure.
- Do not invent commit details, build history, or failure patterns.

FORMAT RULES:
- Write a thorough analysis in markdown with these sections:
  **Root cause** (be specific: file, function, commit, and the mechanism that makes
  the test fail),
  **Historical confirmation** (only if the tool returned at least one candidate:
  name which candidate you compared against — job and build number — and
  whether its root cause matches yours, and why; omit this section entirely
  if the tool returned no candidates),
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
            f"PROVIDER EVIDENCE:\n{json.dumps(compact_evidence(evidence, include_console_log=False, patch_limit=4000, file_list_limit=40), separators=(',', ':'), default=str)}"
        )
        tools = make_historical_search_tool(evidence, state["job_name"])
        agent = create_agent(model=llm, tools=tools, system_prompt=grounded_prompt)
        agent_result = agent.invoke({"messages": [{"role": "user", "content": message}]})
        report = agent_result["messages"][-1].content
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
    historical_incident = resolve_historical_match(match_id, evidence)
    return {
        **state,
        "diagnosis_report": report,
        "diagnosis_confidence": confidence,
        "historical_incident": historical_incident,
    }

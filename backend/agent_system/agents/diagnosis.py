import json

from langchain_groq import ChatGroq
from langchain.agents import create_agent

from .evidence_utils import compact_evidence
from .confidence import extract_agent_confidence
from ..config import settings
from ..orchestrator.state import InvestigationState
from ..tools.build_tools import make_diagnosis_tools

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

Rules:
- Do not call tools in this evidence-based path.
- Use the monitoring report and supplied evidence only.
- The GitHub commit is relevant only if the supplied lookup matched the build SHA.
- A changed file is a lead, not proof that it caused the failure.
- Do not invent commit details, build history, or failure patterns.
- Do not use, request, infer, or refer to historical incidents or past fixes.

FORMAT RULES:
- Write a thorough analysis in markdown with these sections:
  **Root cause** (be specific: file, function, commit, and the mechanism that makes
  the test fail),
  **Conclusion** (one concise sentence summarizing the likely cause and its impact),
  **Evidence** (each point tied to concrete current signals: console error, matching commit evidence and diff statistics, or measured metrics),
  End with one machine-readable line: AGENT_CONFIDENCE: NN (integer 0-100). Do not create separate sections titled Ruled out, Contributing factors, or Confidence.
- Build on the Monitoring Report: refer to its findings instead of copying them word
  for word, but do not omit any reasoning that supports the root cause.
"""
        message = (
            f"Investigate Jenkins job {state['job_name']} build "
            f"#{state['build_number']} using the Monitoring Report and this "
            f"provider evidence bundle:\n\n"
            f"MONITORING REPORT:\n{state['monitoring_report']}\n\n"
            f"PROVIDER EVIDENCE:\n{json.dumps(compact_evidence(evidence, include_console_log=False), separators=(',', ':'), default=str)}"
        )
        result = llm.invoke([
            ("system", grounded_prompt),
            ("human", message),
        ])
        report = result.content
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

    report, confidence = extract_agent_confidence(report)
    return {**state, "diagnosis_report": report, "diagnosis_confidence": confidence}

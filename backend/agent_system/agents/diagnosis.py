import json

from langchain_groq import ChatGroq
from langchain.agents import create_agent

from .evidence_utils import compact_evidence
from ..config import settings
from ..orchestrator.state import InvestigationState
from ..tools.build_tools import make_diagnosis_tools

SYSTEM_PROMPT = """\
You are a Diagnosis Agent in a DevOps investigation pipeline.

You receive a Monitoring Report describing what failed in a Jenkins build.
Your role is to find out WHY it failed using both live and historical data.
You have access to three tools:
- get_recent_commits   : returns the latest code changes pushed before the failure
- get_build_history    : returns the last several build results to identify patterns
- get_failure_patterns : returns known recurring failure types and their common causes

Use all three tools, then write a structured Diagnosis Report that includes:
1. Root cause: the most likely reason for the failure (specific commit, file, or pattern)
2. Evidence: which data points support this conclusion (commit SHA, files changed, history)
3. Contributing factors: any recurring patterns or historical context that matters
4. Confidence level: high / medium / low with a brief justification
5. What to rule out: anything that looks suspicious but is NOT the cause
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
        grounded_prompt = """You are a DevOps diagnosis agent.
Analyze only the supplied Monitoring Report and provider evidence.

Rules:
- Do not call tools in this evidence-based path.
- Use the monitoring report and supplied evidence only.
- The GitHub commit is relevant only if the supplied lookup matched the build SHA.
- A changed file is a lead, not proof that it caused the failure.
- Do not invent commit details, build history, or failure patterns.
- State when historical build data is unavailable; do not infer trends.
- Explain confidence based on actual evidence.
- If evidence is insufficient, leave the root cause unconfirmed.
- Return a complete, concise report with root cause, evidence,
  contributing factors, confidence, and what to rule out.
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
        # Compatibility path for the teammate's original standalone demo.
        tools = make_diagnosis_tools()
        agent = create_agent(model=llm, tools=tools, system_prompt=SYSTEM_PROMPT)
        message = (
            f"Monitoring Report:\n{state['monitoring_report']}\n\n"
            f"Diagnose the root cause for Jenkins job: {state['job_name']}, "
            f"build #{state['build_number']}"
        )
        result = agent.invoke({
            "messages": [{"role": "user", "content": message}]
        })
        report = result["messages"][-1].content

    return {**state, "diagnosis_report": report}

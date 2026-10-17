import json
import logging
import time


from langchain_groq import ChatGroq
from langchain.agents import create_agent

from .evidence_utils import compact_evidence
from .confidence import extract_agent_confidence
from ..config import settings
from ..orchestrator.state import InvestigationState
from ..tools.build_tools import make_monitoring_tools

SYSTEM_PROMPT = """\
You are a Monitoring Agent in a DevOps investigation pipeline.

Your role is to collect and report the raw facts about a failed Jenkins build.
You have access to three tools:
- get_build_metadata  : returns job name, build number, failed stage, duration, timestamp
- get_console_log     : returns the full build output including all error messages
- get_system_metrics  : returns CPU, memory, and I/O stats at the time of failure

Use all three tools, then write a structured Monitoring Report that includes:
1. Build summary: job name, build number, timestamp, total duration
2. Failed stage: which stage stopped the build and at what point
3. Exact error: copy the precise error message(s) from the console log
4. System health: whether infrastructure metrics indicate a resource problem
5. Conclusion: one sentence on what the data shows, without diagnosing or recommending yet
6. End with AGENT_CONFIDENCE: NN (integer 0-100), estimating confidence that the reported facts are supported by evidence; this is not a calibrated probability.
"""


def run_monitoring(state: InvestigationState) -> InvestigationState:
    llm = ChatGroq(
        model=settings.llm_model,
        api_key=settings.groq_api_key,
        temperature=0,
        timeout=45,
        max_tokens=1500,
        model_kwargs={"include_reasoning": False},
    )

    evidence = state.get("evidence") or {}

    if evidence:
        grounded_prompt = SYSTEM_PROMPT + """

IMPORTANT EVIDENCE RULES:
- Use only the supplied evidence bundle for factual observations.
- Do not call synthetic tools or invent build metadata, log messages, metrics,
  failed stages, or timestamps.
- If a field or provider is unavailable, explicitly say so.
- Distinguish a measured value from an interpretation.
- Report the selected build number exactly as supplied.

FORMAT RULES:
- Write a complete, well-organised markdown report with these sections:
  **Build summary** (job, build number, readable UTC timestamp, duration, result),
  **Failed stage** (which stage stopped the build and where),
  **Exact error** (quote the relevant console-log lines in a code block, including
  the failing test names and assertion messages),
  **Evidence from providers** (what GitHub and Prometheus returned for this build,
  or explicitly that a provider was unavailable),
  **System health** (what the measured metrics say, with the values),
  **Conclusion** (one sentence on what the data shows).
- Convert epoch timestamps to readable UTC. Include concrete values, not vague words.
- Report facts only. Do not diagnose and do not recommend actions.
- End with one line: AGENT_CONFIDENCE: NN (integer 0-100), based on evidence coverage and quality. Do not claim statistical calibration.
"""
        message = (
            f"Monitor Jenkins job {state['job_name']} build "
            f"#{state['build_number']}. The following evidence was collected "
            f"from configured providers for this investigation. Treat provider "
            f"statuses as authoritative and do not fill gaps with guesses:\n\n"
            f"{json.dumps(compact_evidence(evidence), separators=(',', ':'), default=str)}"
        )
        ###############################
        
        llm_started = time.perf_counter()


        try:
            result = llm.invoke([
                ("system", grounded_prompt),
                ("human", message),
            ])



            report = result.content

            if not isinstance(report, str):
                raise TypeError(
                    f"Expected string from Groq, got {type(report).__name__}"
                )

        except Exception:
            raise

        ###############################
    else:
        # Compatibility path for the teammate's original standalone demo.
        tools = make_monitoring_tools(
            jenkins_url=state.get("jenkins_url", ""),
            jenkins_username=state.get("jenkins_username", ""),
            jenkins_api_token=state.get("jenkins_api_token", ""),
        )
        agent = create_agent(model=llm, tools=tools, system_prompt=SYSTEM_PROMPT)
        result = agent.invoke({
            "messages": [{
                "role": "user",
                "content": (
                    f"Collect monitoring data for failed Jenkins job: "
                    f"{state['job_name']}, build #{state['build_number']}"
                ),
            }]
        })
        report = result["messages"][-1].content

    report, confidence = extract_agent_confidence(report)
    return {**state, "monitoring_report": report, "monitoring_confidence": confidence}

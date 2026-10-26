import json
import logging
import time

from langchain_groq import ChatGroq
from langchain.agents import create_agent

from .evidence_utils import compact_evidence, build_summary_markdown
from .confidence import extract_agent_confidence, monitoring_evidence_confidence
from ..config import settings
from ..llm_log import record_call, append_call
from ..orchestrator.state import InvestigationState
from ..tools.build_tools import make_monitoring_tools

logger = logging.getLogger(__name__)

# The Monitoring report has exactly three sections: Build summary, Failed stage,
# Exact error. Everything else (provider notes, system health, conclusion) is
# deliberately out of scope: Diagnosis receives the provider evidence directly.
SYSTEM_PROMPT = """\
You are a Monitoring Agent in a DevOps investigation pipeline.

Your role is to collect and report the raw facts about a failed Jenkins build.
You have access to three tools:
- get_build_metadata  : returns job name, build number, failed stage, duration, timestamp
- get_console_log     : returns the full build output including all error messages
- get_system_metrics  : returns CPU, memory, and I/O stats at the time of failure

Use the tools, then write a Monitoring Report with exactly these three sections:
1. Build summary: a markdown table with columns Field | Value and the rows Job name,
   Build number, Result, Timestamp (UTC), Duration, URL
2. Failed stage: which stage stopped the build and at what point
3. Exact error: copy the precise error message(s) from the console log
Report facts only. Do not diagnose, do not recommend actions, and add no other sections.
End with AGENT_CONFIDENCE: NN (integer 0-100), estimating confidence that the reported facts are supported by evidence; this is not a calibrated probability.
"""

# Build summary is rendered from evidence in Python (build_summary_markdown), so
# the model only writes the two sections that need reading the console log.
GROUNDED_PROMPT = """\
You are a Monitoring Agent in a DevOps investigation pipeline.

Your role is to report the raw facts about a failed Jenkins build from the
supplied evidence bundle.

IMPORTANT EVIDENCE RULES:
- Use only the supplied evidence bundle. Do not call tools and do not invent
  stages, test names, log lines or messages.
- If the console log is unavailable, say so in the relevant section.

OUTPUT FORMAT - exactly these two markdown sections, in this order, nothing else:

**Failed stage**
The name of the pipeline stage that stopped the build and where it stopped
(for example the first failing test). One or two sentences.

**Exact error**
A single fenced code block with the relevant console-log lines copied verbatim:
the failing test names, the traceback and the assertion message.

STRICT RULES:
- Do NOT write a Build summary: it is added automatically.
- No evidence annotations, no provider notes, no system health, no conclusion,
  no diagnosis, no recommendations.
- Final line, on its own: AGENT_CONFIDENCE: NN (integer 0-100) based on how well
  the console log supports what you reported. Do not claim statistical calibration.
"""


def build_monitoring_messages(state: InvestigationState) -> list[tuple[str, str]]:
    """Prompt for the evidence-grounded path. training/ reuses it, so
    fine-tuning examples match the production prompt exactly."""
    message = (
        f"Monitor Jenkins job {state['job_name']} build "
        f"#{state['build_number']}. The following evidence was collected "
        f"from configured providers for this investigation. Treat provider "
        f"statuses as authoritative and do not fill gaps with guesses:\n\n"
        f"{json.dumps(compact_evidence(state.get('evidence') or {}), separators=(',', ':'), default=str)}"
    )
    return [("system", GROUNDED_PROMPT), ("human", message)]


def run_monitoring(state: InvestigationState) -> InvestigationState:
    llm = ChatGroq(
        model=settings.llm_model,
        api_key=settings.groq_api_key,
        temperature=0,
        timeout=45,
        # Reasoning tokens count against this budget on gpt-oss models, so keep
        # generous headroom: a truncated answer loses the confidence line.
        max_tokens=2500,
        model_kwargs={"include_reasoning": False},
    )

    evidence = state.get("evidence") or {}

    if evidence:
        messages = build_monitoring_messages(state)
        started = time.perf_counter()
        result = llm.invoke(messages)

        raw = result.content
        if not isinstance(raw, str):
            raise TypeError(f"Expected string from Groq, got {type(raw).__name__}")

        finish_reason = (getattr(result, "response_metadata", None) or {}).get("finish_reason")
        if finish_reason == "length":
            logger.warning(
                "Monitoring output hit max_tokens; the confidence marker may be missing."
            )

        body, confidence = extract_agent_confidence(raw)
        source = "agent"
        if confidence is None:
            confidence = monitoring_evidence_confidence(evidence, body)
            source = "evidence_coverage"
            logger.warning(
                "Monitoring returned no usable AGENT_CONFIDENCE; "
                "using evidence-coverage score %s.", confidence,
            )

        report = build_summary_markdown(evidence) + "\n\n" + body
        return {
            **state,
            "monitoring_report": report,
            "monitoring_confidence": confidence,
            "monitoring_confidence_source": source,
            "llm_calls": append_call(state, record_call("monitoring", messages, result, started)),
        }

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

    report, confidence = extract_agent_confidence(result["messages"][-1].content)
    return {
        **state,
        "monitoring_report": report,
        "monitoring_confidence": confidence,
        "monitoring_confidence_source": "agent" if confidence is not None else "unavailable",
    }
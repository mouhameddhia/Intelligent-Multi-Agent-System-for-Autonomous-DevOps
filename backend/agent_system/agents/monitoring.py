import json
import logging
import time

from langchain_groq import ChatGroq
from langchain.agents import create_agent

from .evidence_utils import build_summary_markdown
from .confidence import extract_agent_confidence, monitoring_evidence_confidence
from ..config import settings
from ..llm_log import append_call, record_call
from ..local_llm import invoke_with_fallback
from ..orchestrator.state import InvestigationState
from ..tools.build_tools import make_monitoring_tools

logger = logging.getLogger(__name__)


def _monitoring_evidence(evidence: dict) -> dict:
    """Give Monitoring only the build facts it is responsible for reporting."""
    jenkins = evidence.get("jenkins") or {}
    jenkins_summary = {}
    if isinstance(jenkins, dict):
        jenkins_summary = {
            key: jenkins[key]
            for key in (
                "job_name", "build_number", "result", "timestamp", "duration_ms",
                "url", "failed_stage", "console_log",
            )
            if jenkins.get(key) is not None
        }
        if isinstance(jenkins_summary.get("console_log"), str):
            jenkins_summary["console_log"] = jenkins_summary["console_log"][-2200:]

    metrics = []
    for metric in evidence.get("metrics") or []:
        if not isinstance(metric, dict):
            continue
        metrics.append({
            "query": metric.get("query"),
            "status": metric.get("status"),
            "count": metric.get("count"),
            "message": str(metric.get("message", ""))[:240],
            "results": [
                {
                    "metric": result.get("metric", {}),
                    "values": (result.get("values") or [])[-3:],
                }
                for result in (metric.get("results") or [])[:2]
                if isinstance(result, dict)
            ],
        })

    loki = evidence.get("loki") or {}
    loki_summary = {
        "status": loki.get("status"),
        "message": str(loki.get("message", ""))[:240],
        "lines": [
            {
                "timestamp_ms": item.get("timestamp_ms"),
                "labels": item.get("labels", {}),
                "line": str(item.get("line", ""))[-400:],
            }
            for item in (loki.get("lines") or [])[-4:]
            if isinstance(item, dict)
        ],
    } if isinstance(loki, dict) else {}

    return {"jenkins": jenkins_summary, "metrics": metrics, "loki": loki_summary}

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
- Report a stage as failed only when the Jenkins output shows a failed command,
  test, or stage result. A value printed by a successful step is not itself a
  stage failure.
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

MAX_TOKENS = 4000


def build_monitoring_messages(state: InvestigationState) -> list[tuple[str, str]]:
    """Build the production prompt; shared with the offline training pipeline."""
    evidence = state.get("evidence") or {}
    message = (
        f"Monitor Jenkins job {state['job_name']} build "
        f"#{state['build_number']}. The following evidence was collected "
        f"from configured providers for this investigation. Treat provider "
        f"statuses as authoritative and do not fill gaps with guesses:\n\n"
        f"{json.dumps(_monitoring_evidence(evidence), separators=(',', ':'), default=str)}"
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
        max_tokens=MAX_TOKENS,
        model_kwargs={"include_reasoning": settings.llm_include_reasoning},
    )

    evidence = state.get("evidence") or {}

    if evidence:
        messages = build_monitoring_messages(state)
        started = time.perf_counter()
        use_local_llm = bool(state.get("use_local_llm", False))
        result = invoke_with_fallback(
            llm, messages, max_tokens=MAX_TOKENS, use_local_llm=use_local_llm
        )

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
            "llm_calls": append_call(
                state, record_call("monitoring", messages, result, started)
            ),
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

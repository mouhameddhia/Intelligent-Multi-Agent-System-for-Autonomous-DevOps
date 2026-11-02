from langgraph.graph import StateGraph, END
from langgraph.types import interrupt

try:
    from langgraph.checkpoint.memory import InMemorySaver as _Saver
except ImportError:
    from langgraph.checkpoint.memory import MemorySaver as _Saver

from .state import InvestigationState
from ..agents.monitoring import run_monitoring
from ..agents.diagnosis import run_diagnosis
from ..agents.remediation import run_remediation
from ..agents.steps import run_structure_steps
from ..agents.judge import run_llm_judge
from ..agents.fix import run_apply_fix


def run_human_approval(state: InvestigationState) -> InvestigationState:
    """Pause the graph here and wait for the devops engineer's decision."""
    decision_payload = interrupt({
        "type": "remediation_approval",
        "job_name": state["job_name"],
        "build_number": state["build_number"],
        "remediation_report": state["remediation_report"],
        "steps": state.get("steps", []),
        # Informational only — the engineer can read this before deciding,
        # it never affects routing or the approval outcome itself.
        "judge_verdict": state.get("judge_verdict", ""),
        "judge_scores": state.get("judge_scores", {}),
        "judge_hallucination_detected": state.get("judge_hallucination_detected"),
    })
    decision = (decision_payload or {}).get("decision", "deny")
    return {**state, "approval_status": decision}


def _route_after_approval(state: InvestigationState) -> str:
    return "apply_fix" if state.get("approval_status") == "approve" else END


def build_investigation_graph():
    """Build Monitoring -> Diagnosis -> Remediation -> approval -> optional fix."""
    graph = StateGraph(InvestigationState)

    graph.add_node("monitoring", run_monitoring)
    graph.add_node("diagnosis", run_diagnosis)
    graph.add_node("remediation", run_remediation)
    graph.add_node("structure_steps", run_structure_steps)
    graph.add_node("llm_judge", run_llm_judge)
    graph.add_node("human_approval", run_human_approval)
    graph.add_node("apply_fix", run_apply_fix)

    graph.set_entry_point("monitoring")
    graph.add_edge("monitoring", "diagnosis")
    graph.add_edge("diagnosis", "remediation")
    graph.add_edge("remediation", "structure_steps")
    graph.add_edge("structure_steps", "llm_judge")
    graph.add_edge("llm_judge", "human_approval")
    graph.add_conditional_edges(
        "human_approval",
        _route_after_approval,
        {"apply_fix": "apply_fix", END: END},
    )
    graph.add_edge("apply_fix", END)

    return graph.compile(checkpointer=_Saver())


investigation_graph = build_investigation_graph()

import json

from langchain_groq import ChatGroq
from langchain.agents import create_agent

from ..config import settings
from ..orchestrator.state import InvestigationState
from ..tools.build_tools import make_remediation_tools

SYSTEM_PROMPT = """\
You are a Remediation Agent in a DevOps investigation pipeline.

You receive a Monitoring Report and a Diagnosis Report for a failed Jenkins build.
Your role is to recommend concrete, actionable remediation steps using the knowledge base.
You have access to three tools:
- get_past_fixes     : returns how similar failures were resolved before
- get_runbook        : returns the step-by-step procedure for this type of failure
- get_team_contacts  : returns who to contact and who is currently on call

Use all three tools, then write a structured Remediation Report that includes:
1. Recommended action: the single most important thing to do right now
2. Step-by-step plan: numbered steps based on the runbook and past fixes
3. Owner: who should act on this (name and role, from team contacts)
4. Automation safety: which steps are safe to automate vs which require human approval
5. Priority: critical / high / medium with justification
6. Prevention: one recommendation to stop this from recurring
"""


def run_remediation(state: InvestigationState) -> InvestigationState:
    llm = ChatGroq(
        model=settings.llm_model,
        api_key=settings.groq_api_key,
        temperature=0,
        max_tokens=1200,
        model_kwargs={"include_reasoning": False},
    )

    evidence = state.get("evidence") or {}

    if evidence:
        grounded_prompt = """You are a DevOps remediation advisor.
Prepare a safe, evidence-based proposal for the supplied Jenkins failure.

Rules:
- Do not call tools in this evidence-based path.
- Do not invent historical fixes, runbooks, team members, or on-call contacts.
- This is a proposal only; never claim an action was executed.
- Recommend reversible diagnostic steps first.
- Require human approval for deployments, restarts, destructive changes,
  production modifications, or other disruptive actions.
- Identify uncertainty and distinguish verified facts from recommendations.
- No verified historical fix database or runbook was supplied; label general
  recommendations accordingly.
- Return a complete, concise report with recommended action, numbered steps,
  owner, automation safety, priority, and prevention.
"""
        message = (
            f"Prepare a remediation proposal for Jenkins job {state['job_name']} "
            f"build #{state['build_number']}.\n\n"
            f"MONITORING REPORT:\n{state['monitoring_report']}\n\n"
            f"DIAGNOSIS REPORT:\n{state['diagnosis_report']}\n"
        )
        result = llm.invoke([
            ("system", grounded_prompt),
            ("human", message),
        ])
        report = result.content
    else:
        # Compatibility path for the teammate's original standalone demo.
        tools = make_remediation_tools()
        agent = create_agent(model=llm, tools=tools, system_prompt=SYSTEM_PROMPT)
        message = (
            f"Monitoring Report:\n{state['monitoring_report']}\n\n"
            f"Diagnosis Report:\n{state['diagnosis_report']}\n\n"
            f"Recommend remediation for Jenkins job: {state['job_name']}, "
            f"build #{state['build_number']}"
        )
        result = agent.invoke({
            "messages": [{"role": "user", "content": message}]
        })
        report = result["messages"][-1].content

    return {**state, "remediation_report": report}

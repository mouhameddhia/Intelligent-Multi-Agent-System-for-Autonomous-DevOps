"""Teacher-data generator for fine-tuning (distillation of the production model).

For each single-token mutant of training/sample_project:
  1. run its test suite; mutants the tests do not catch are skipped,
  2. wrap the pytest output in a Jenkins-style console log and evidence bundle
     shaped exactly like the one the frontend sends,
  3. run the real Monitoring -> Diagnosis -> Remediation nodes (the teacher),
  4. label each output against the KNOWN root cause (file + function).

Resumable: one JSON line per mutant; mutants already in --out are skipped, so
a run stopped by Groq's daily limit continues the next day.

    docker compose exec backend python -m training.generate_dataset --limit 20
"""
import argparse
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_system.agents.confidence import extract_agent_confidence
from agent_system.agents.diagnosis import run_diagnosis
from agent_system.agents.monitoring import run_monitoring
from agent_system.agents.remediation import run_remediation
from agent_system.config import settings

from .mutations import SAMPLE_PROJECT, Mutant, enumerate_mutants

DEFAULT_OUT = Path(__file__).resolve().parent / "data" / "teacher_runs.jsonl"

JOBS = {
    "pricing.py": "shop-checkout",
    "inventory.py": "warehouse-stock",
    "text_tools.py": "blog-engine",
    "stats.py": "metrics-dashboard",
    "scheduling.py": "room-booking",
}
AUTHORS = ["Amira Ben Salem", "Youssef Trabelsi", "Lina Haddad", "Karim Mansour", "Sarra Jlassi"]
COMMIT_MESSAGES = [
    "Refactor {function} in {file}",
    "Simplify {function} logic",
    "Tidy up {file}",
    "Small cleanup in {function}",
    "Align {function} with code review comments",
]


# ---------------------------------------------------------------------------
# Failing build
# ---------------------------------------------------------------------------

def run_tests(mutant: Mutant) -> tuple[bool, str, list[str]]:
    """Run the mutated project's tests. Returns (passed, pytest output, failing test ids)."""
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "project"
        shutil.copytree(SAMPLE_PROJECT, project, ignore=shutil.ignore_patterns("__pycache__"))
        target = project / mutant.file
        target.write_text(mutant.apply(target.read_text(encoding="utf-8")), encoding="utf-8")

        report = Path(tmp) / "report.xml"
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider", f"--junitxml={report}"],
            cwd=project, capture_output=True, text=True, timeout=120,
        )
        output = "\n".join(
            line for line in completed.stdout.splitlines() if "generated xml file" not in line
        )
        failing = []
        if report.exists():
            for case in ET.parse(report).iter("testcase"):
                if case.find("failure") is not None or case.find("error") is not None:
                    module = case.get("classname", "").replace(".", "/")
                    failing.append(f"{module}.py::{case.get('name')}")
        return completed.returncode == 0, output.replace(str(project), "{workspace}"), failing


def build_console_log(job: str, sha: str, message: str, pytest_output: str) -> str:
    workspace = f"/var/jenkins_home/workspace/{job}"
    return "\n".join([
        "Started by user admin",
        f"Obtained Jenkinsfile from git https://github.com/acme/{job}.git",
        "[Pipeline] Start of Pipeline",
        "[Pipeline] node",
        f"Running on Jenkins in {workspace}",
        "[Pipeline] {",
        "[Pipeline] stage",
        "[Pipeline] { (Declarative: Checkout SCM)",
        "[Pipeline] checkout",
        f"Checking out Revision {sha} (refs/remotes/origin/main)",
        f'Commit message: "{message}"',
        "[Pipeline] }",
        "[Pipeline] // stage",
        "[Pipeline] stage",
        "[Pipeline] { (Test)",
        "[Pipeline] sh",
        "+ python3 -m pytest -v",
        pytest_output.replace("{workspace}", workspace),
        "[Pipeline] }",
        "[Pipeline] // stage",
        "[Pipeline] }",
        "[Pipeline] // node",
        "[Pipeline] End of Pipeline",
        "ERROR: script returned exit code 1",
        "Finished: FAILURE",
    ])


def build_evidence(mutant: Mutant, pytest_output: str, rng: random.Random) -> dict:
    """Evidence bundle shaped like the frontend's agentEvidence (App.jsx)."""
    job = JOBS[mutant.file]
    build_number = rng.randint(12, 480)
    sha = f"{rng.getrandbits(160):040x}"
    author = rng.choice(AUTHORS)
    message = rng.choice(COMMIT_MESSAGES).format(function=mutant.function, file=mutant.file)
    started = datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(minutes=rng.randint(0, 60 * 24 * 50))
    timestamp_ms = int(started.timestamp() * 1000)

    jenkins = {
        "job_name": job,
        "build_number": build_number,
        "result": "FAILURE",
        "building": False,
        "timestamp": timestamp_ms,
        "duration_ms": rng.randint(8_000, 95_000),
        "url": f"http://jenkins:8080/job/{job}/{build_number}/",
        "display_name": f"#{build_number}",
        "full_display_name": f"{job} #{build_number}",
        "commits": [{"sha": sha, "message": message, "timestamp": timestamp_ms, "author": author}],
        "console_log": build_console_log(job, sha, message, pytest_output)[-12000:],
    }

    # Some builds have no GitHub evidence, and some commits also touch an
    # unrelated file, so the model learns that a changed file is a lead, not proof.
    if rng.random() < 0.8:
        files = [{"filename": mutant.file, "status": "modified", "additions": 1, "deletions": 1, "changes": 2}]
        if rng.random() < 0.3:
            extra = rng.randint(1, 12)
            files.append({"filename": "README.md", "status": "modified", "additions": extra, "deletions": 0, "changes": extra})
        github = {
            "status": "success",
            "sha": sha,
            "message": message,
            "author": author,
            "date": started.isoformat().replace("+00:00", "Z"),
            "html_url": f"https://github.com/acme/{job}/commit/{sha}",
            "files": files,
        }
    else:
        github = {
            "status": "not_configured",
            "message": "Connect GitHub and select the repository containing this commit.",
            "sha": sha,
        }

    return {
        "jenkins": jenkins,
        "github": github,
        "metrics": [{
            "status": "not_configured",
            "message": "Connect Prometheus in Settings to retrieve time-aligned metrics.",
        }],
    }


# ---------------------------------------------------------------------------
# Teacher run
# ---------------------------------------------------------------------------

class DailyLimitReached(Exception):
    pass


def _retry_after(message: str) -> float:
    """Seconds Groq asks us to wait, from '... try again in 1m2.5s' (default 30)."""
    match = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", message)
    if not match:
        return 30.0
    return int(match.group(1) or 0) * 60 + float(match.group(2)) + 2


def _with_rate_limit(node, state: dict, attempts: int = 6) -> dict:
    for attempt in range(attempts):
        try:
            return node(state)
        except Exception as exc:
            message = str(exc)
            if "rate limit" not in message.lower() and "429" not in message:
                raise
            if "per day" in message.lower():
                raise DailyLimitReached(message) from exc
            wait = _retry_after(message)
            print(f"    rate limited, waiting {wait:.0f}s ({attempt + 1}/{attempts})", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"still rate limited after {attempts} attempts")


def run_teacher(job: str, build_number: int, evidence: dict) -> dict:
    state = {
        "incident_id": f"synthetic-{job}-{build_number}",
        "job_name": job,
        "build_number": build_number,
        "evidence": evidence,
        "steps": [],
        "monitoring_report": "",
        "diagnosis_report": "",
        "remediation_report": "",
        "historical_incident": None,
        "llm_calls": [],
    }
    for node in (run_monitoring, run_diagnosis, run_remediation):
        state = _with_rate_limit(node, state)
    return state


def label(mutant: Mutant, failing: list[str], state: dict) -> dict:
    """Check each agent's output against the known root cause."""
    calls = {call["agent"]: call for call in state.get("llm_calls", [])}
    monitoring = (calls.get("monitoring") or {}).get("output") or ""
    diagnosis = state.get("diagnosis_report") or ""
    steps_text = " ".join(step["text"] for step in state.get("steps") or [])
    remediation = (state.get("remediation_report") or "") + " " + steps_text

    return {
        "monitoring_ok": (
            "**Failed stage**" in monitoring
            and "**Exact error**" in monitoring
            and extract_agent_confidence(monitoring)[1] is not None
            and any(test.split("::")[-1] in monitoring for test in failing)
        ),
        "diagnosis_ok": mutant.file in diagnosis and mutant.function in diagnosis,
        "remediation_ok": (
            len(state.get("steps") or []) >= 2
            and state.get("remediation_confidence") is not None
            and mutant.function in remediation
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _done_ids(out: Path) -> set[str]:
    if not out.exists():
        return set()
    with out.open(encoding="utf-8") as handle:
        return {json.loads(line)["id"] for line in handle if line.strip()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=20, help="teacher runs to perform in this session")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--pause", type=float, default=20, help="seconds between teacher runs (Groq TPM)")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    done = _done_ids(args.out)
    mutants = enumerate_mutants()
    random.Random(args.seed).shuffle(mutants)  # spread early runs across all files and functions
    print(f"{len(mutants)} mutants, {len(done)} already processed, teacher model {settings.llm_model}")

    completed = 0
    for mutant in mutants:
        if completed >= args.limit:
            break
        if mutant.id in done:
            continue

        passed, output, failing = run_tests(mutant)
        record = {"id": mutant.id, "mutant": asdict(mutant), "created_at": datetime.now(timezone.utc).isoformat()}
        if passed:
            record["status"] = "survived"  # the tests do not catch it: no failing build to learn from
            print(f"[skip] {mutant.id} survived the tests")
        else:
            rng = random.Random(f"{args.seed}:{mutant.id}")
            evidence = build_evidence(mutant, output, rng)
            job, build_number = evidence["jenkins"]["job_name"], evidence["jenkins"]["build_number"]
            print(f"[run ] {mutant.id} -> {job} #{build_number}, {len(failing)} failing test(s)", flush=True)
            try:
                state = run_teacher(job, build_number, evidence)
            except DailyLimitReached as exc:
                print(f"Groq daily limit reached, stopping. Re-run tomorrow to resume.\n{exc}")
                break
            record.update({
                "status": "ok",
                "teacher_model": settings.llm_model,
                "job_name": job,
                "build_number": build_number,
                "failing_tests": failing,
                "evidence": evidence,
                "monitoring_report": state["monitoring_report"],
                "diagnosis_report": state["diagnosis_report"],
                "remediation_report": state["remediation_report"],
                "steps": state["steps"],
                "llm_calls": state["llm_calls"],
                "labels": label(mutant, failing, state),
            })
            completed += 1
            print(f"       labels {record['labels']}")

        with args.out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        if record["status"] == "ok" and completed < args.limit:
            time.sleep(args.pause)

    print(f"Done: {completed} teacher run(s) this session -> {args.out}")


if __name__ == "__main__":
    main()

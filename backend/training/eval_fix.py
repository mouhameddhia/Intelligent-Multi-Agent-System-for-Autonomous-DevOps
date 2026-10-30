"""Fix-success benchmark: does the build pass once the proposed fix is applied?

The notebook's checks (format, names the function) are saturated: every model
passes them. This one measures the outcome that matters. For each held-out
Remediation example the notebook evaluated:
  1. take that model's Remediation answer (fine-tuned student, base student,
     teacher, or no report at all as a control),
  2. hand it, with the teacher's Diagnosis, to the production Fix agent prompt
     (agents/fix.py, run by the production model),
  3. write the rewritten file into the mutated sample project and run all tests.

fix_ok means the whole suite passes; reverted means the file is back to the
original, bug-free source. --no-test-file hides the test file from the Fix
prompt (production shows it), which makes the fix depend on the reports.

Resumable: results already in --out are skipped.

    docker compose exec backend python -m training.eval_fix
    docker compose exec backend python -m training.eval_fix --no-test-file
"""
import argparse
import difflib
import json
import time
from collections import defaultdict
from pathlib import Path

from langchain_groq import ChatGroq

from agent_system.agents.confidence import extract_agent_confidence
from agent_system.agents.fix import build_fix_prompt, clean_fix_output
from agent_system.agents.steps import split_report_and_steps_json
from agent_system.config import settings

from .generate_dataset import DailyLimitReached, _with_rate_limit, run_suite
from .mutations import SAMPLE_PROJECT, Mutant

DATA_DIR = Path(__file__).resolve().parent / "data"
TEACHER = "teacher (gpt-oss-120b)"
CONTROL = "no remediation report"


def _read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _remediation_report(output: str) -> str:
    """What production passes to the Fix agent: the report without the steps
    JSON block and the confidence line."""
    report, _steps = split_report_and_steps_json(output or "")
    return extract_agent_confidence(report)[0]


def load_cases(val_path: Path, runs_path: Path, eval_paths: list[Path]) -> list[dict]:
    """One case per (held-out example, answer source)."""
    runs = [run for run in _read_jsonl(runs_path) if run.get("status") == "ok"]
    by_prompt = {}
    for run in runs:
        for call in run["llm_calls"]:
            if call["agent"] == "remediation":
                by_prompt[json.dumps(call["messages"], sort_keys=True)] = run

    # The notebook evaluates val_rows[:MAX_EVAL] in order, so the i-th
    # remediation result of each model belongs to the i-th remediation row.
    val = [row for row in _read_jsonl(val_path) if row["meta"]["agent"] == "remediation"]
    answers = defaultdict(list)
    for path in eval_paths:
        for result in _read_jsonl(path):
            if result["agent"] == "remediation" and result["model"] != TEACHER:
                answers[result["model"]].append(result)
    evaluated = max((len(rows) for rows in answers.values()), default=0)

    cases = []
    for index, row in enumerate(val[:evaluated]):
        run = by_prompt[json.dumps(row["messages"][:-1], sort_keys=True)]
        user = row["messages"][1]["content"]
        diagnosis = user.split("DIAGNOSIS REPORT:\n", 1)[1]
        base = {"mutant": run["mutant"], "group": row["meta"]["group"], "diagnosis": diagnosis}
        cases.append({**base, "source": TEACHER, "remediation": row["messages"][-1]["content"]})
        cases.append({**base, "source": CONTROL, "remediation": ""})
        for model, rows in answers.items():
            assert rows[index]["group"] == row["meta"]["group"], (model, index)
            cases.append({**base, "source": model, "remediation": rows[index]["output"] or ""})
    return cases


def run_fix(llm: ChatGroq, case: dict, with_test_file: bool) -> dict:
    mutant = Mutant(**case["mutant"])
    clean = (SAMPLE_PROJECT / mutant.file).read_text(encoding="utf-8")
    buggy = mutant.apply(clean)
    test_name = f"test_{mutant.file}"
    test_content = (SAMPLE_PROJECT / "tests" / test_name).read_text(encoding="utf-8") if with_test_file else None

    prompt = build_fix_prompt(
        mutant.file, buggy, test_name if with_test_file else None, test_content,
        case["diagnosis"], _remediation_report(case["remediation"]),
    )
    started = time.perf_counter()
    result = _with_rate_limit(lambda _state: llm.invoke([("human", prompt)]), {})
    new_content = clean_fix_output(result.content)
    passed, _output, failing = run_suite({mutant.file: new_content})

    changed = [
        line for line in difflib.unified_diff(clean.splitlines(), new_content.splitlines(), lineterm="", n=0)
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    usage = getattr(result, "usage_metadata", None) or {}
    return {
        "fix_ok": passed,
        "reverted": new_content.strip() == clean.strip(),
        "lines_off_original": len(changed),
        "still_failing": failing,
        "tokens": usage.get("total_tokens"),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "new_content": new_content,
    }


def _key(variant: str, source: str, mutant: dict) -> str:
    return f"{variant}|{source}|{Mutant(**mutant).id}"


def summarize(results: list[dict]) -> None:
    table = defaultdict(list)
    for result in results:
        table[(result["variant"], result["source"])].append(result)
    print(f"\n{'variant':<14} {'remediation from':<26} {'n':>3} {'fix_ok':>7} {'reverted':>9}")
    for (variant, source), rows in sorted(table.items()):
        ok = sum(r["fix_ok"] for r in rows)
        reverted = sum(r["reverted"] for r in rows)
        print(f"{variant:<14} {source:<26} {len(rows):>3} {ok / len(rows):>7.0%} {reverted / len(rows):>9.0%}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--val", type=Path, default=DATA_DIR / "sft" / "val.jsonl")
    parser.add_argument("--runs", type=Path, default=DATA_DIR / "teacher_runs.jsonl")
    parser.add_argument("--eval", type=Path, nargs="+",
                        default=sorted((DATA_DIR / "kaggle").glob("*/eval_results_*.jsonl")))
    parser.add_argument("--out", type=Path, default=DATA_DIR / "fix_eval.jsonl")
    parser.add_argument("--no-test-file", action="store_true", help="hide the test file from the Fix prompt")
    parser.add_argument("--tag", default="", help="label for this run, e.g. after a Fix prompt change")
    parser.add_argument("--pause", type=float, default=8, help="seconds between Fix calls (Groq TPM)")
    parser.add_argument("--summary", action="store_true", help="only print the table of saved results")
    args = parser.parse_args()

    done = _read_jsonl(args.out) if args.out.exists() else []
    if args.summary:
        summarize(done)
        return

    variant = ("no test file" if args.no_test_file else "production") + (f" [{args.tag}]" if args.tag else "")
    done_keys = {_key(r["variant"], r["source"], r["mutant"]) for r in done}
    cases = [c for c in load_cases(args.val, args.runs, args.eval)
             if _key(variant, c["source"], c["mutant"]) not in done_keys]
    print(f"{len(cases)} Fix runs to do ({variant}), {len(done)} already saved, fix model {settings.llm_model}")

    # Same settings as run_apply_fix in agents/fix.py.
    llm = ChatGroq(model=settings.llm_model, api_key=settings.groq_api_key, temperature=0,
                   max_tokens=1200, model_kwargs={"include_reasoning": False})
    for number, case in enumerate(cases, 1):
        try:
            outcome = run_fix(llm, case, with_test_file=not args.no_test_file)
        except DailyLimitReached as exc:
            print(f"Groq daily limit reached, stopping. Re-run later to resume.\n{exc}")
            break
        record = {"variant": variant, "source": case["source"], "group": case["group"],
                  "mutant": case["mutant"], **outcome}
        with args.out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        done.append(record)
        print(f"[{number}/{len(cases)}] {case['source']:<26} {Mutant(**case['mutant']).id:<40} "
              f"fix_ok={outcome['fix_ok']} reverted={outcome['reverted']} tokens={outcome['tokens']}", flush=True)
        time.sleep(args.pause)

    summarize(done)


if __name__ == "__main__":
    main()

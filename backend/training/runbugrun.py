"""Real bugs for evaluation, from RunBugRun (Prenner and Robbes, 2023): pairs of
buggy and fixed Python submissions that real people made to programming
contests (Project CodeNet), each problem with its judge's input/output tests.

Each selected bug becomes a small project under data/runbugrun/<problem>-<id>/:
main.py holds the fixed submission, tests/test_main.py runs main.py on each
judge input and compares the printed output, and bug.json holds the buggy
submission. bugs.runbugrun_bugs() turns them into Bugs, so the teacher runs and
the fix-success benchmark use them like the sample projects. A bug is kept only
if, on this Python, the fixed submission passes every test and the buggy one
fails at least one. Selection is held out from training: it is evaluation only.

Download the Python test split and the tests from
https://github.com/giganticode/run_bug_run_data/releases (v0.0.1) into
data/runbugrun_raw/, then:

    docker compose exec backend python -m training.runbugrun --count 40
"""
import argparse
import ast
import difflib
import gzip
import json
import random
import re
import shutil
from collections import defaultdict
from pathlib import Path

from .bugs import _owner
from .generate_dataset import run_suite
from .mutations import RUNBUGRUN

RAW_DIR = Path(__file__).resolve().parent / "data" / "runbugrun_raw"
THIRD_PARTY = re.compile(r"^\s*(import|from)\s+(numpy|scipy|pandas|networkx|sklearn|sortedcontainers)\b", re.M)
FLOAT = re.compile(r"\d\.\d")  # float outputs need a tolerance the judge tests do not record
MAX_TESTS = 6

TEST_FILE = '''"""Judge tests for contest problem {problem}: run main.py on each input and
compare what it prints, token by token, with the expected output."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

MAIN = Path(__file__).resolve().parent.parent / "main.py"
CASES = {cases}


@pytest.mark.parametrize("given, expected", CASES, ids=[f"case{{i}}" for i in range(1, len(CASES) + 1)])
def test_output(given, expected):
    run = subprocess.run([sys.executable, str(MAIN)], input=given, capture_output=True, text=True,
                         timeout=5, env={{"PATH": os.environ.get("PATH", "")}})
    printed, crash = run.stdout, run.stderr.strip()[-600:]
    assert not crash, f"main.py crashed:\\n{{crash}}"
    assert printed.split() == expected.split()
'''


def _read_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _candidates(bugs: list[dict]) -> list[dict]:
    """Small, standard-library submissions whose fix changes at most two places."""
    keep = []
    for bug in bugs:
        buggy, fixed = bug["buggy_code"], bug["fixed_code"]
        if buggy.strip() == fixed.strip() or int(bug.get("line_hunks") or 99) > 2:
            continue
        if not 5 <= len(fixed.splitlines()) <= 60 or not fixed.isascii() or not buggy.isascii():
            continue
        if THIRD_PARTY.search(fixed) or THIRD_PARTY.search(buggy):
            continue
        try:
            ast.parse(fixed)
            ast.parse(buggy)
        except SyntaxError:
            continue
        keep.append(bug)
    return keep


def _tests(path: Path, problems: set[str]) -> dict[str, list[tuple[str, str]]]:
    tests = defaultdict(list)
    for test in _read_gz(path):
        if test["problem_id"] not in problems:
            continue
        given, expected = test["input"], test["output"]
        if len(given) <= 400 and 0 < len(expected) <= 200 and not FLOAT.search(expected):
            tests[test["problem_id"]].append((given, expected))
    # Deterministic: the shortest inputs first, as a judge's sample cases usually are.
    return {problem: sorted(set(cases), key=lambda case: (len(case[0]), case))[:MAX_TESTS]
            for problem, cases in tests.items() if len(set(cases)) >= 3}


def _function(fixed: str, buggy: str) -> str:
    """The function holding the first changed line, or main.py for script-level code."""
    matcher = difflib.SequenceMatcher(a=fixed.splitlines(), b=buggy.splitlines())
    first = next(block for block in matcher.get_opcodes() if block[0] != "equal")
    lines = fixed.splitlines()
    if first[1] < len(lines) and lines[first[1]].strip():
        return _owner(fixed, lines[first[1]]) or "main.py"
    return "main.py"


def _write(project: Path, bug: dict, cases: list[tuple[str, str]]) -> None:
    (project / "tests").mkdir(parents=True)
    (project / "main.py").write_text(bug["fixed_code"].rstrip() + "\n", encoding="utf-8")
    (project / "tests" / "test_main.py").write_text(
        TEST_FILE.format(problem=bug["problem_id"], cases=json.dumps(cases, indent=4)), encoding="utf-8")
    labels = bug.get("labels") or []
    if isinstance(labels, str):
        labels = ast.literal_eval(labels) if labels.startswith("[") else []
    (project / "bug.json").write_text(json.dumps({
        "id": bug["id"], "problem_id": bug["problem_id"],
        "buggy_submission_id": bug["buggy_submission_id"], "fixed_submission_id": bug["fixed_submission_id"],
        "labels": labels, "kind": labels[0] if labels else "unknown",
        "function": _function(bug["fixed_code"], bug["buggy_code"]),
        "buggy_code": bug["buggy_code"].rstrip() + "\n",
    }, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", type=Path, default=RAW_DIR)
    parser.add_argument("--count", type=int, default=40, help="bugs to keep, one per problem")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    bugs = _candidates(list(_read_gz(args.raw / "python_test0.jsonl.gz")))
    tests = _tests(args.raw / "tests_all.jsonl.gz", {bug["problem_id"] for bug in bugs})
    bugs = [bug for bug in bugs if bug["problem_id"] in tests]
    random.Random(args.seed).shuffle(bugs)
    print(f"{len(bugs)} candidate bugs over {len({bug['problem_id'] for bug in bugs})} problems")

    if RUNBUGRUN.exists():
        shutil.rmtree(RUNBUGRUN)
    kept, problems = 0, set()
    for bug in bugs:
        if kept >= args.count:
            break
        if bug["problem_id"] in problems:
            continue
        project = RUNBUGRUN / f"{bug['problem_id']}-{bug['id']}"
        _write(project, bug, tests[bug["problem_id"]])
        fixed_ok, _output, _failing = run_suite({}, project)
        buggy_ok, _output, failing = run_suite({"main.py": bug["buggy_code"]}, project)
        if fixed_ok and not buggy_ok:
            kept += 1
            problems.add(bug["problem_id"])
            print(f"[keep {kept}] {project.name}: {len(failing)}/{len(tests[bug['problem_id']])} tests fail")
        else:
            shutil.rmtree(project)
            print(f"[drop] {project.name}: fixed passes={fixed_ok}, buggy passes={buggy_ok}")
    print(f"Done: {kept} bugs in {RUNBUGRUN}")


if __name__ == "__main__":
    main()

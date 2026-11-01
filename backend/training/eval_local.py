"""Report-quality check of the local students on new bugs, without Kaggle.

The notebook evaluates the students on the held-out split of the training
projects. For a suite the students never saw (the real RunBugRun bugs), this
runs them in Ollama on the exact Monitoring and Remediation prompts of the
teacher runs, and scores every answer with the notebook's checks:
format_ok (the report's required sections and confidence) and grounded
(Monitoring names a failing test, Remediation names the buggy function).
The teacher's own answers are scored the same way.

Both students are Q4_K_M GGUFs with the same chat template and settings: the
fine-tuned one built from the notebook's export, the base one from
unsloth/Qwen3-4B-GGUF (backend/training/README.md, "Run the local student").
Resumable: answers already in --out are skipped.

    docker compose exec backend python -m training.eval_local
"""
import argparse
import json
import re
import time
from pathlib import Path

from langchain_ollama import ChatOllama

from agent_system.agents.steps import STEP_DELIMITER_END, STEP_DELIMITER_START
from agent_system.config import settings
from agent_system.local_llm import split_thinking

from .bugs import load_bug

DATA_DIR = Path(__file__).resolve().parent / "data"
TEACHER = "teacher (gpt-oss-120b)"
MODELS = {"qwen3-4b fine-tuned": "pipeguard-qwen3-4b", "qwen3-4b base": "pipeguard-qwen3-4b-base"}
AGENTS = ("monitoring", "remediation")
MAX_NEW_TOKENS = 2500  # as in the notebook: reasoning + answer


def check(agent: str, output: str, failing_tests: list[str], function: str) -> dict:
    """The notebook's check()."""
    has_confidence = re.search(r"AGENT_CONFIDENCE:\s*\d{1,3}", output) is not None
    if agent == "monitoring":
        return {
            "format_ok": "**Failed stage**" in output and "**Exact error**" in output and has_confidence,
            "grounded": any(test.split("::")[-1] in output for test in failing_tests),
        }
    steps = []
    if STEP_DELIMITER_START in output:
        block = output.split(STEP_DELIMITER_START, 1)[1].split(STEP_DELIMITER_END, 1)[0]
        try:
            steps = json.loads(block[block.find("["):block.rfind("]") + 1])
        except ValueError:
            steps = []
    return {"format_ok": len(steps) >= 2 and has_confidence, "grounded": function in output}


def _read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=Path, default=DATA_DIR / "teacher_runs_runbugrun.jsonl")
    parser.add_argument("--out", type=Path, default=DATA_DIR / "eval_local_runbugrun.jsonl")
    parser.add_argument("--models", nargs="+", default=list(MODELS), choices=list(MODELS))
    args = parser.parse_args()

    runs = [run for run in _read_jsonl(args.runs) if run.get("status") == "ok"]
    done = {(r["id"], r["model"], r["agent"]) for r in _read_jsonl(args.out)} if args.out.exists() else set()
    chats = {model: ChatOllama(model=MODELS[model], base_url=settings.local_llm_url, temperature=0,
                               num_predict=MAX_NEW_TOKENS, num_ctx=settings.local_llm_num_ctx,
                               num_gpu=settings.local_llm_num_gpu) for model in args.models}
    print(f"{len(runs)} teacher runs, {len(done)} answers already saved")

    # One model at a time, so Ollama keeps a single model on the GPU.
    for model in [TEACHER, *args.models]:
        for number, run in enumerate(runs, 1):
            calls = {call["agent"]: call for call in run["llm_calls"]}
            function = load_bug(run["mutant"]).function
            for agent in AGENTS:
                if (run["id"], model, agent) in done or agent not in calls:
                    continue
                started = time.perf_counter()
                if model == TEACHER:
                    output, tokens = calls[agent]["output"] or "", calls[agent]["output_tokens"]
                else:
                    result = split_thinking(chats[model].invoke(calls[agent]["messages"]))
                    output = result.content
                    tokens = (result.usage_metadata or {}).get("output_tokens")
                record = {"id": run["id"], "model": model, "agent": agent, "group": run["id"],
                          **check(agent, output, run["failing_tests"], function),
                          "new_tokens": tokens, "seconds": round(time.perf_counter() - started, 2),
                          "output": output}
                with args.out.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(f"[{model} {number}/{len(runs)}] {agent:<11} format_ok={record['format_ok']} "
                      f"grounded={record['grounded']} tokens={tokens} {record['seconds']}s", flush=True)


if __name__ == "__main__":
    main()

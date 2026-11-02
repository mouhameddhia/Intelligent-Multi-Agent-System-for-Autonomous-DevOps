"""Report-quality check of the local students on new bugs, without Kaggle.

The notebook evaluates the students on the held-out split of the training
projects. For a suite the students never saw (the real RunBugRun bugs), this
runs them in Ollama on the exact Monitoring and Remediation prompts of the
teacher runs, and scores every answer with the notebook's checks:
format_ok (the report's required sections and confidence) and grounded
(Monitoring names a failing test, Remediation names the buggy function).
The confidence is read with the production parser, which also accepts
**AGENT_CONFIDENCE:** 92 (the notebook's stricter regex does not). The
teacher's own answers are scored the same way.

Both students are Q4_K_M GGUFs with the same chat template and settings: the
fine-tuned one built from the notebook's export, the base one from
unsloth/Qwen3-4B-GGUF (backend/training/README.md, "Run the local student").
Each agent gets its production token budget. Resumable: answers already in
--out are skipped. --refresh rescores the saved answers and redoes those cut
off by a budget that has since grown; generation is greedy, so every answer
that ended within the old budget would come out the same.

    docker compose exec backend python -m training.eval_local
    docker compose exec backend python -m training.eval_local --refresh
"""
import argparse
import json
import time
from pathlib import Path

from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama

from agent_system.agents import monitoring
from agent_system.agents.confidence import extract_agent_confidence
from agent_system.agents.steps import STEP_DELIMITER_END, STEP_DELIMITER_START
from agent_system.config import settings
from agent_system.local_llm import split_thinking

from .bugs import load_bug
from .generate_dataset import _with_rate_limit

DATA_DIR = Path(__file__).resolve().parent / "data"
TEACHER = "teacher (gpt-oss-120b)"
MODELS = {"qwen3-4b fine-tuned": "pipeguard-qwen3-4b", "qwen3-4b base": "pipeguard-qwen3-4b-base"}
AGENTS = ("monitoring", "remediation")
# Reasoning + answer. Monitoring's is production's (agents/monitoring.py);
# Remediation keeps the notebook's 2500, above production's 2200.
MAX_NEW_TOKENS = {"monitoring": monitoring.MAX_TOKENS, "remediation": 2500}
OLD_BUDGET = 2500  # the budget of answers saved before max_new_tokens was recorded


def check(agent: str, output: str, failing_tests: list[str], function: str) -> dict:
    """The notebook's check(), with the production confidence parser."""
    has_confidence = extract_agent_confidence(output)[1] is not None
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


def _cut_off(record: dict) -> bool:
    """Hit a budget smaller than the agent's current one."""
    budget = record.get("max_new_tokens", OLD_BUDGET)
    return budget < MAX_NEW_TOKENS[record["agent"]] and (record["new_tokens"] or 0) >= budget


def refresh(out: Path, runs: dict[str, dict]) -> None:
    kept, dropped = [], 0
    for record in _read_jsonl(out):
        if _cut_off(record):
            dropped += 1
            continue
        run = runs[record["id"]]
        record.update(check(record["agent"], record["output"], run["failing_tests"],
                            load_bug(run["mutant"]).function))
        kept.append(record)
    with out.open("w", encoding="utf-8") as handle:
        handle.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in kept)
    print(f"rescored {len(kept)} answers, {dropped} cut off by an old budget will be redone")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=Path, default=DATA_DIR / "teacher_runs_runbugrun.jsonl")
    parser.add_argument("--out", type=Path, default=DATA_DIR / "eval_local_runbugrun.jsonl")
    parser.add_argument("--models", nargs="+", default=list(MODELS), choices=list(MODELS))
    parser.add_argument("--refresh", action="store_true", help="rescore saved answers, redo cut-off ones")
    args = parser.parse_args()

    runs = [run for run in _read_jsonl(args.runs) if run.get("status") == "ok"]
    if args.refresh and args.out.exists():
        refresh(args.out, {run["id"]: run for run in runs})
    done = {(r["id"], r["model"], r["agent"]) for r in _read_jsonl(args.out)} if args.out.exists() else set()
    chats = {(model, agent): ChatOllama(model=MODELS[model], base_url=settings.local_llm_url, temperature=0,
                                        num_predict=MAX_NEW_TOKENS[agent], num_ctx=settings.local_llm_num_ctx,
                                        num_gpu=settings.local_llm_num_gpu)
             for model in args.models for agent in AGENTS}
    # The teacher's logged answer is used as is, unless it was cut off by a
    # smaller budget than the agent has now: then it is asked again, like production.
    teacher = {agent: ChatGroq(model=settings.llm_model, api_key=settings.groq_api_key, temperature=0,
                               max_tokens=MAX_NEW_TOKENS[agent], model_kwargs={"include_reasoning": True})
               for agent in AGENTS}
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
                call = calls[agent]
                if model == TEACHER and not _cut_off({"agent": agent, "new_tokens": call["output_tokens"]}):
                    output, tokens = call["output"] or "", call["output_tokens"]
                else:
                    chat = teacher[agent] if model == TEACHER else chats[(model, agent)]
                    result = _with_rate_limit(lambda _state: chat.invoke(call["messages"]), {})
                    result = split_thinking(result) if model != TEACHER else result
                    output = result.content
                    tokens = (result.usage_metadata or {}).get("output_tokens")
                record = {"id": run["id"], "model": model, "agent": agent, "group": run["id"],
                          **check(agent, output, run["failing_tests"], function),
                          "new_tokens": tokens, "max_new_tokens": MAX_NEW_TOKENS[agent],
                          "seconds": round(time.perf_counter() - started, 2), "output": output}
                with args.out.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(f"[{model} {number}/{len(runs)}] {agent:<11} format_ok={record['format_ok']} "
                      f"grounded={record['grounded']} tokens={tokens} {record['seconds']}s", flush=True)


if __name__ == "__main__":
    main()

"""Turn verified LLM calls into chat-format SFT files for the Kaggle notebook.

Sources:
  - teacher runs from generate_dataset.py, kept only when the labels confirm
    the output against the known root cause;
  - real incidents from MongoDB (--include-mongo), kept only when the engineer
    reported that the build passed after the fix.

Agents exported: monitoring and remediation (single-call agents). Remediation
is only kept when Diagnosis was also right, since its input is the diagnosis.

The validation split holds out whole functions (file::function), so the
evaluation measures generalisation to unseen code, not memorisation.

    docker compose exec backend python -m training.export_sft --include-mongo
"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
AGENTS = ("monitoring", "remediation")


def _keep(agent: str, labels: dict) -> bool:
    if agent == "monitoring":
        return labels["monitoring_ok"]
    return labels["diagnosis_ok"] and labels["remediation_ok"]


def _example(call: dict, meta: dict) -> dict:
    """The assistant turn keeps the teacher's reasoning under a neutral
    "reasoning" key; the notebook maps it to each model family's own field
    (gpt-oss "thinking", Qwen3 "reasoning_content")."""
    answer = {"role": "assistant", "content": call["output"]}
    if call.get("reasoning"):
        answer["reasoning"] = call["reasoning"]
    return {
        "messages": [*call["messages"], answer],
        "meta": {**meta, "agent": call["agent"], "teacher_model": call.get("model")},
    }


def teacher_examples(path: Path) -> tuple[list[dict], Counter]:
    examples, stats = [], Counter()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            run = json.loads(line)
            if run.get("status") != "ok":
                continue
            stats["runs"] += 1
            for key, ok in run["labels"].items():
                stats[key] += ok
            mutant = run["mutant"]
            meta = {
                "source": "synthetic",
                "group": f"{mutant['file']}::{mutant['function']}",
                "file": mutant["file"],
                "function": mutant["function"],
                "failing_tests": run["failing_tests"],
            }
            for call in run["llm_calls"]:
                if call["agent"] in AGENTS and _keep(call["agent"], run["labels"]):
                    stats["with_reasoning"] += bool(call.get("reasoning"))
                    examples.append(_example(call, meta))
    return examples, stats


def mongo_examples() -> list[dict]:
    from agent_system.db import _get_collection

    collection = _get_collection()
    if collection is None:
        print("MongoDB not configured: skipping real incidents.")
        return []
    examples = []
    query = {"feedback.build_passed": True, "llm_calls.0": {"$exists": True}}
    for doc in collection.find(query):
        meta = {"source": "incident", "group": f"incident::{doc['_id']}", "incident_id": doc["_id"]}
        examples += [_example(call, meta) for call in doc["llm_calls"] if call["agent"] in AGENTS]
    return examples


def _in_validation(group: str, percent: int) -> bool:
    return int(hashlib.sha256(group.encode()).hexdigest(), 16) % 100 < percent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=Path, default=DATA_DIR / "teacher_runs.jsonl")
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR / "sft")
    parser.add_argument("--val-percent", type=int, default=20)
    parser.add_argument("--include-mongo", action="store_true")
    args = parser.parse_args()

    examples, stats = teacher_examples(args.runs) if args.runs.exists() else ([], Counter())
    if stats["runs"]:
        print(f"Teacher accuracy over {stats['runs']} runs: " + ", ".join(
            f"{key} {stats[key] / stats['runs']:.0%}" for key in ("monitoring_ok", "diagnosis_ok", "remediation_ok")
        ))
        print(f"Kept examples with teacher reasoning: {stats['with_reasoning']} of {len(examples)}")
    if args.include_mongo:
        examples += mongo_examples()

    # Real incidents always train; synthetic functions are held out by group.
    train, val = [], []
    for example in examples:
        meta = example["meta"]
        holdout = meta["source"] == "synthetic" and _in_validation(meta["group"], args.val_percent)
        (val if holdout else train).append(example)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("train", train), ("val", val)):
        with (args.out_dir / f"{name}.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        per_agent = Counter(row["meta"]["agent"] for row in rows)
        print(f"{name}: {len(rows)} examples {dict(per_agent)} -> {args.out_dir / f'{name}.jsonl'}")


if __name__ == "__main__":
    main()

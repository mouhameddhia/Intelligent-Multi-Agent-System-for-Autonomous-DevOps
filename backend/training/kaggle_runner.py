"""Run the distillation notebook on Kaggle from the repo (Kaggle CLI).

Needs KAGGLE_API_TOKEN in .env (kaggle.com > Settings > API > Create New Token)
and a phone-verified Kaggle account for GPU sessions. Both the dataset and the
notebooks are created private.

    python -m training.kaggle_runner upload-data              # data/sft -> Kaggle dataset
    python -m training.kaggle_runner train --model qwen3-4b   # run, wait, download results

Results land in training/data/kaggle/<model>/ (adapter zip, eval summary, log).
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TRAINING = Path(__file__).resolve().parent
NOTEBOOK = TRAINING / "kaggle" / "qlora_distillation.ipynb"
SFT_DIR = TRAINING / "data" / "sft"
RESULTS_DIR = TRAINING / "data" / "kaggle"
DATASET_SLUG = "pipeguard-sft"
MODELS = ("qwen3-4b", "gpt-oss-20b", "qwen3-14b")
DEFAULT_MODEL_LINE = 'MODEL = "qwen3-4b"'


def kaggle(*args: str, check: bool = True) -> str:
    completed = subprocess.run(["kaggle", *args], capture_output=True, text=True)
    output = (completed.stdout + completed.stderr).strip()
    if check and completed.returncode != 0:
        sys.exit(f"kaggle {' '.join(args)} failed:\n{output}")
    return output


def username() -> str:
    match = re.search(r"username:\s*(\S+)", kaggle("config", "view"))
    if not match:
        sys.exit("Kaggle username unknown: set KAGGLE_API_TOKEN in .env.")
    return match.group(1)


def upload_data(user: str) -> None:
    files = [SFT_DIR / "train.jsonl", SFT_DIR / "val.jsonl"]
    missing = [str(path) for path in files if not path.exists()]
    if missing:
        sys.exit(f"Run `python -m training.export_sft` first; missing: {missing}")

    dataset = f"{user}/{DATASET_SLUG}"
    with tempfile.TemporaryDirectory() as tmp:
        for path in files:
            shutil.copy(path, tmp)
        metadata = {"title": DATASET_SLUG, "id": dataset, "licenses": [{"name": "CC0-1.0"}]}
        (Path(tmp) / "dataset-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        if "ready" in kaggle("datasets", "status", dataset, check=False).lower():
            print(kaggle("datasets", "version", "-p", tmp, "-m", "Updated teacher data"))
        else:
            print(kaggle("datasets", "create", "-p", tmp))


def push_notebook(user: str, model: str) -> str:
    """Push the notebook with MODEL set, on a T4 with internet. Returns the kernel ref."""
    slug = f"pipeguard-qlora-{model}"
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    replaced = False
    for cell in notebook["cells"]:
        source = "".join(cell["source"])
        if DEFAULT_MODEL_LINE in source:
            cell["source"] = source.replace(DEFAULT_MODEL_LINE, f'MODEL = "{model}"').splitlines(keepends=True)
            replaced = True
    if not replaced:
        sys.exit(f"Could not find {DEFAULT_MODEL_LINE!r} in {NOTEBOOK}")

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "notebook.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
        metadata = {
            "id": f"{user}/{slug}",
            "title": slug,
            "code_file": "notebook.ipynb",
            "language": "python",
            "kernel_type": "notebook",
            "is_private": True,
            "enable_gpu": True,
            "enable_internet": True,
            "dataset_sources": [f"{user}/{DATASET_SLUG}"],
            "competition_sources": [],
            "kernel_sources": [],
        }
        (Path(tmp) / "kernel-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        print(kaggle("kernels", "push", "-p", tmp, "--accelerator", "NvidiaTeslaT4"))
    return f"{user}/{slug}"


def wait_and_download(ref: str, model: str, poll_seconds: int) -> None:
    started = time.monotonic()
    while True:
        status = kaggle("kernels", "status", ref, check=False)
        elapsed = (time.monotonic() - started) / 60
        print(f"[{elapsed:5.1f} min] {status}", flush=True)
        if re.search(r"complete|error|cancel", status, re.I):
            break
        time.sleep(poll_seconds)

    out = RESULTS_DIR / model
    out.mkdir(parents=True, exist_ok=True)
    print(kaggle("kernels", "output", ref, "-p", str(out), "--force", check=False))
    print(f"Results and log in {out}")
    if not re.search(r"complete", status, re.I):
        sys.exit(f"Kaggle run did not complete: {status}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("upload-data", help="create or version the private training dataset")
    train = commands.add_parser("train", help="run the notebook for one student and download its results")
    train.add_argument("--model", choices=MODELS, required=True)
    train.add_argument("--no-wait", action="store_true", help="push and return without waiting")
    train.add_argument("--poll", type=int, default=120, help="seconds between status checks")
    args = parser.parse_args()

    user = username()
    if args.command == "upload-data":
        upload_data(user)
        return
    ref = push_notebook(user, args.model)
    if not args.no_wait:
        wait_and_download(ref, args.model, args.poll)


if __name__ == "__main__":
    main()

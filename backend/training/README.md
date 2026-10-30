# Fine-tuning: QLoRA distillation of gpt-oss-120b into smaller reasoning models

The production agents run `openai/gpt-oss-120b` on Groq. This folder distils its
**reasoning and answers** into LoRA adapters for the two single-call agents,
**Monitoring** and **Remediation**. Diagnosis (a tool-calling loop) and the code fix
stay on the large model.

| Student | Why | Runs where |
|---|---|---|
| `Qwen3-4B` | Usable locally: ~2.5 GB in 4-bit, fits a 4 GB laptop GPU | Ollama, after GGUF export |
| `gpt-oss-20b` | Strongest that trains on a free T4; same chat format and reasoning style as the teacher | Kaggle (evaluation) |
| `Qwen3-14B` | Fallback if gpt-oss-20b runs out of memory | Kaggle (evaluation) |

```
sample_project/  ──mutations.py──►  failing builds with a known root cause
        │
generate_dataset.py   teacher = the real Monitoring → Diagnosis → Remediation nodes
        │             labels every output against the known file and function
        ▼
data/teacher_runs.jsonl ──export_sft.py──► data/sft/train.jsonl, val.jsonl
        │                                      (+ real incidents the engineer confirmed fixed)
        ▼
kaggle/qlora_distillation.ipynb  QLoRA on a free Kaggle T4, evaluation vs. the base model,
                                 and GGUF export of the local student for Ollama
```

## 1. Generate teacher data (local, Docker)

```powershell
docker compose exec backend python -m training.generate_dataset --limit 30
```

- The sample project has 103 single-token mutants; the tests catch 92 of them.
  Each caught mutant becomes one failing build investigated by the teacher.
- Groq's free tier allows 8,000 tokens per minute and one teacher run uses about
  10–15k tokens, so the script pauses between runs and waits out rate limits.
  If the daily limit is reached it stops; run the same command again later and it
  resumes where it left off.
- The generator turns on `llm_include_reasoning`, so each recorded call also keeps
  the teacher's reasoning text (production leaves it off).

## 2. Export the training files

```powershell
docker compose exec backend python -m training.export_sft --include-mongo
```

Prints the teacher's accuracy per agent, then writes `data/sft/train.jsonl` and
`data/sft/val.jsonl`. Only verified outputs are kept:

| Agent | Kept when |
|---|---|
| Monitoring | it has both sections and `AGENT_CONFIDENCE`, and names a failing test |
| Remediation | Diagnosis named the right file and function, and the plan has ≥ 2 steps naming the function |

The validation split holds out whole functions (about 20%), so evaluation measures
generalisation to unseen code. With `--include-mongo`, real incidents whose feedback
says the build passed are added to the training split; investigations store their
exact prompts and outputs in `llm_calls` for this purpose.

## 3. Train and evaluate on Kaggle

From the repo, with `KAGGLE_API_TOKEN` in `.env` (the CLI is not in the backend image):

```powershell
docker compose run --rm --no-deps -T backend sh -c "pip install -q kaggle && python -m training.kaggle_runner upload-data"
docker compose run --rm --no-deps -T backend sh -c "pip install -q kaggle && python -m training.kaggle_runner train --model qwen3-4b --poll 120"
```

`train` pushes a private GPU notebook, waits for it, and downloads the results to
`data/kaggle/<model>/`. Or by hand:

1. Create a Kaggle dataset named `pipeguard-sft` and upload `train.jsonl` and `val.jsonl`.
2. Import `kaggle/qlora_distillation.ipynb` as a notebook, attach the dataset, set the
   accelerator to **GPU T4 x2** and turn **Internet** on.
3. Set `MODEL` (`qwen3-4b`, `gpt-oss-20b` or `qwen3-14b`) and *Run All*; run once per
   student. Outputs: the adapter (`pipeguard-<model>-lora.zip`),
   `eval_summary_<model>.csv` (fine-tuned vs. base vs. teacher) and the per-example
   results. For `qwen3-4b` it also writes a 4-bit GGUF file for Ollama:
   `ollama create pipeguard-qwen3-4b -f Modelfile` with `FROM ./<the .gguf file>`.

Settings: 4-bit base model, LoRA r = 8, α = 16 on all attention and MLP projections,
2 epochs, learning rate 2e-4, effective batch size 8, loss on the assistant turn only.
The teacher's reasoning is trained into each family's own field (gpt-oss's analysis
channel, Qwen3's `<think>` block), so the student learns to reason the way the teacher
did, not only to copy its answers. Examples with reasoning measure ~2,200 tokens.

## 4. Fix-success benchmark

```powershell
docker compose exec backend python -m training.eval_fix                  # production Fix prompt
docker compose exec backend python -m training.eval_fix --no-test-file   # Fix prompt without the tests
docker compose exec backend python -m training.eval_fix --summary        # table of saved results
```

The notebook's checks (sections present, names the function) only test the shape of
an answer. This benchmark tests the outcome: for each held-out Remediation example the
notebook evaluated, it hands that model's answer and the teacher's Diagnosis to the
production Fix agent prompt (`build_fix_prompt` in `agents/fix.py`, run by
gpt-oss-120b), writes the rewritten file into the mutated project and runs all 34
tests. Answer sources: each student fine-tuned and base, the teacher, and a control
with no Remediation report. One run is 60 Fix calls, about 130k Groq tokens; it
resumes after a rate limit. Results go to `data/fix_eval.jsonl`.

## 5. Results (10 held-out bugs per agent)

**Notebook checks** (`format_ok` / mean generated tokens; `grounded` was 100% for all):

| | Monitoring | Remediation |
|---|---|---|
| Teacher, gpt-oss-120b | 100% / ~875 | 100% / ~835 |
| gpt-oss-20b base → fine-tuned | 80% → 90% / 1,344 → 1,013 | 100% → 100% / 633 → 919 |
| Qwen3-4B base → fine-tuned | 100% → 100% / 675 → 1,080 | 90% → 90% / 1,291 → 951 |

Training: Qwen3-4B 19 min, gpt-oss-20b 54.5 min (8.6 GB peak, 0.44% of 21B
parameters trained) on one T4.

**Fix success** (build passes / Fix restored the original file exactly):

| Remediation from | Production prompt, before | No test file | Production prompt, minimal-diff |
|---|---|---|---|
| Teacher, gpt-oss-120b | 100% / 10% | 100% / 90% | 100% / 100% |
| Qwen3-4B fine-tuned | 100% / 10% | 100% / 80% | 100% / 100% |
| Qwen3-4B base | 100% / 30% | 100% / 100% | 100% / 100% |
| gpt-oss-20b fine-tuned | 100% / 10% | 100% / 90% | 100% / 100% |
| gpt-oss-20b base | 90% / 10% | 100% / 90% | 100% / 100% |
| No Remediation report | 80% / 10% | 100% / 90% | 100% / 100% |

What the numbers say:

- **Fine-tuning gives no measurable accuracy gain here.** The only change is
  gpt-oss-20b Monitoring format, 80% → 90%, which is one example of ten. Students do
  learn the teacher's answer length (~850 tokens), so some got slower.
- **These bugs do not separate the Remediation models.** Even without a Remediation
  report the Fix agent fixes all ten from the code and the Diagnosis, so a local
  Qwen3-4B, base or fine-tuned, gives the same end result as gpt-oss-120b.
- **The Fix prompt was the real lever.** Its old instruction to correct docstrings made
  it rewrite 20 lines on average (up to 68) to undo a one-token bug, and it broke the
  build in 3 of 60 runs. With the minimal-diff rule every fix is the exact one-line
  undo and every build passes.

To make fine-tuning measurable, the benchmark needs harder failures: multi-line and
multi-file bugs, and a second, larger sample project.

## Limits

- **Data volume.** 92 mutants give at most ~180 examples, enough to measure the
  approach but small for SFT. Add modules to `sample_project/` (with tests), or let real
  incidents accumulate, to grow it.
- **Failure types.** Mutants cover logic bugs caught by unit tests only, not dependency,
  infrastructure or flaky-test failures.
- **Serving.** gpt-oss-20b and Qwen3-14B need 9–14 GB even in 4-bit, so they cannot run
  on a 4 GB laptop GPU: they are evaluated on Kaggle as the upper bound of what
  distillation achieves. Qwen3-4B is the student meant for local use.
- **Local latency.** Reasoning adds hundreds of generated tokens per answer. Measured on
  a GTX 1650 (llama3.2:3b, similar size): ~27 tokens/s, so ~500 reasoning tokens add
  ~20 s per call.

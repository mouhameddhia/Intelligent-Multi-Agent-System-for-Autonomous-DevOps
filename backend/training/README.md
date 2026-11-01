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
sample_shop/     ──bugs.py──────►   (single-token mutants; hand-written bugs)
        │
generate_dataset.py   teacher = the real Monitoring → Diagnosis → Remediation nodes
        │             labels every output against the known file and function
        ▼
data/teacher_runs*.jsonl ──export_sft.py──► data/sft/train.jsonl, val.jsonl
        │                                      (+ real incidents the engineer confirmed fixed)
        ▼
kaggle/qlora_distillation.ipynb  QLoRA on a free Kaggle T4, evaluation vs. the base model,
                                 and GGUF export of the local student for Ollama
```

## 1. Generate teacher data (local, Docker)

```powershell
docker compose exec backend python -m training.generate_dataset --limit 30                # sample_project mutants
docker compose exec backend python -m training.generate_dataset --suite shop --limit 100  # sample_shop bugs
```

Two bug sets, each failing build investigated by the teacher:

| Suite | Project | Bugs | Output |
|---|---|---|---|
| `mutants` | `sample_project/`: 5 independent utility modules, 34 tests | 103 single-token mutants (`mutations.py`), 92 caught by the tests | `data/teacher_runs.jsonl` |
| `shop` | `sample_shop/`: a 10-module webshop backend (money, dates, catalog, cart, discounts, tax, shipping, loyalty, orders, invoices), 65 tests | 83 hand-written bugs (`bugs.py`): wrong rules, boundaries, swallowed errors, wrong configuration, multi-line edits. 33 of them fail a test in another module (e.g. a `tax.py` bug caught by `test_orders.py`) | `data/teacher_runs_shop.jsonl` |

- The single-token mutants turned out too easy to separate the models (section 5),
  hence the shop. Each shop bug stays within one file, since the Fix agent rewrites
  one file.
- Groq's free tier allows 8,000 tokens per minute and one teacher run uses about
  10–15k tokens, so the script pauses between runs and waits out rate limits.
  If the daily limit is reached it stops; run the same command again later and it
  resumes where it left off.
- The generator turns on `llm_include_reasoning`, so each recorded call also keeps
  the teacher's reasoning text (production leaves it off).

## 2. Export the training files

```powershell
docker compose exec backend python -m training.export_sft --runs training/data/teacher_runs_shop.jsonl --train-only training/data/teacher_runs.jsonl
```

Prints the teacher's accuracy per agent, then writes `data/sft/train.jsonl` and
`data/sft/val.jsonl`. Only verified outputs are kept:

| Agent | Kept when |
|---|---|
| Monitoring | it has both sections and `AGENT_CONFIDENCE`, and names a failing test |
| Remediation | Diagnosis named the right file and function, and the plan has ≥ 2 steps naming the function |

The validation split holds out whole functions (about 20%), so evaluation measures
generalisation to unseen code. Runs passed with `--train-only` (here the easy
mutants) go to training whole, so the validation set only holds shop bugs: 291
training and 39 validation examples (22 Monitoring, 17 Remediation, from 10 unseen
functions). With `--include-mongo`, real incidents whose feedback
says the build passed are added to the training split; investigations store their
exact prompts and outputs in `llm_calls` for this purpose.

## 3. Train and evaluate on Kaggle

From the repo, with `KAGGLE_API_TOKEN` in `.env` (the CLI is not in the backend image):

```powershell
docker compose run --rm --no-deps -T backend sh -c "pip install -q kaggle && python -m training.kaggle_runner upload-data"
docker compose run --rm --no-deps -T backend sh -c "pip install -q kaggle && python -m training.kaggle_runner train --model qwen3-4b --poll 120"
```

`train` pushes a private GPU notebook, waits for it, and downloads the results to
`data/kaggle/<model>/`. The run continues on Kaggle if the PC is shut down;
`kaggle_runner download --model <model>` fetches the results later. Or by hand:

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
gpt-oss-120b), writes the rewritten file into the buggy project and runs its whole
test suite. Answer sources: each student fine-tuned and base, the teacher, and a
control with no Remediation report. On the shop's 17 held-out Remediation examples
one run is 102 Fix calls, about 280k Groq tokens; it resumes after a rate limit.
Results go to `data/fix_eval.jsonl` (`--out` to choose another file).

## 5. Results, sample_project (10 held-out mutants per agent)

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
multi-file bugs, and a second, larger sample project. Section 6 does this.

## 6. Results, sample_shop (39 held-out examples from 10 unseen functions)

**Teacher on the 83 shop bugs:** Monitoring 98%, Diagnosis 87%, Remediation 95%
correct, all three 83% (on the mutants Diagnosis was almost always right). Most
Diagnosis misses are bugs caught by a test in another module. Only the runs right
on all three are kept for training.

**Notebook checks**, base → fine-tuned, paired McNemar exact test:

| Student | Agent | `format_ok` | `grounded` | Mean tokens |
|---|---|---|---|---|
| Qwen3-4B | Monitoring (22) | 100% → 100% | 73% → **100%** (p = 0.03) | 738 → 1,346 |
| Qwen3-4B | Remediation (17) | 94% → 94% | 94% → 100% (p = 1.0) | 992 → 801 |
| gpt-oss-20b | Monitoring (22) | 59% → **95%** (p = 0.02) | 86% → 95% (p = 0.5) | 1,893 → 1,397 |
| gpt-oss-20b | Remediation (17) | 94% → 88% (p = 1.0) | 94% → 88% (p = 1.0) | 755 → 863 |
| Teacher, gpt-oss-120b | Monitoring / Remediation | 100% / 94% | 100% / 100% | ~1,280 / ~850 |

Training (291 examples, 72 steps on one T4): Qwen3-4B 46 min, 4.9 GB peak, loss
1.22 → 0.31; gpt-oss-20b 102 min, 9.0 GB peak, loss 4.16 → 3.87. gpt-oss-20b's
reported loss barely moves in either run, although its behaviour does change; the
cause is not established (Unsloth's gpt-oss path may report it on another scale), and
a validation-loss run would settle it.

**Fix success** on the 17 held-out Remediation examples (production prompt):

| Remediation from | Build passes | Restored the original exactly | Extra lines, mean |
|---|---|---|---|
| gpt-oss-20b fine-tuned | **16/17 (94%)** | 47% | 2.4 |
| Qwen3-4B fine-tuned | **15/17 (88%)** | 47% | 2.8 |
| Teacher, gpt-oss-120b | 15/17 (88%) | 53% | 2.1 |
| gpt-oss-20b base | 14/17 (82%) | 59% | 4.4 |
| No Remediation report | 14/17 (82%) | 53% | 2.4 |
| Qwen3-4B base | 13/17 (76%) | 47% | 6.7 |

What the numbers say:

- **On harder bugs, fine-tuning gives significant gains on Monitoring**: Qwen3-4B
  learns to name the failing test (73% → 100%), gpt-oss-20b the report format
  (59% → 95%). Both reach the teacher on these checks.
- **Remediation does not improve measurably.** The base models already score 94%.
- **The end result leans towards fine-tuning but is not proven.** Each fine-tuned
  student fixes 2 more bugs than its base model and loses none (p = 0.5 at n = 17).
  The control still fixes 82%: the Fix agent works mostly from the Diagnosis and the
  test file. One bug (`bulk-any-sku`) fails for every source, because the Fix model
  writes the wrong comparison.
- **Fix agent robustness.** At `max_tokens=1200`, 6 of 102 Fix answers were empty:
  gpt-oss-120b's hidden reasoning counts against the budget. `agents/fix.py` now
  allows 4,000 tokens and retries once on an empty answer, and never judges or
  pushes an empty file. The table above is after that change.

## Limits

- **Data volume.** 175 teacher runs give 330 examples, small for SFT, and the test
  set (39 examples, 17 for the Fix benchmark) only shows large effects as
  significant. One training run per student, one seed.
- **Synthetic bugs.** Both projects and all bugs are written for this purpose. A
  real-bug test set (e.g. BugsInPy) would show whether the gains hold on real code.
- **Failure types.** The bugs are logic bugs caught by unit tests only, not
  dependency, infrastructure or flaky-test failures.
- **Serving.** gpt-oss-20b and Qwen3-14B need 9–14 GB even in 4-bit, so they cannot run
  on a 4 GB laptop GPU: they are evaluated on Kaggle as the upper bound of what
  distillation achieves. Qwen3-4B is the student meant for local use.
- **Local latency.** Reasoning adds hundreds of generated tokens per answer. Measured on
  a GTX 1650 (llama3.2:3b, similar size): ~27 tokens/s, so ~500 reasoning tokens add
  ~20 s per call.

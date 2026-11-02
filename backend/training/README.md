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
sample_shop/     ──bugs.py──────►   (single-token mutants; hand-written bugs;
RunBugRun        ──runbugrun.py─►    real contest bugs, evaluation only)
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

```bash
cd backend
source .venv/bin/activate
pip install -r training/requirements.txt
python -m training.generate_dataset --limit 30                # sample_project mutants
python -m training.generate_dataset --suite shop --limit 100  # sample_shop bugs
python -m training.runbugrun --count 40                        # real bugs, see below
python -m training.generate_dataset --suite runbugrun --limit 40
```

Three bug sets, each failing build investigated by the teacher. The first two are
for training and evaluation, the third for evaluation only:

| Suite | Project | Bugs | Output |
|---|---|---|---|
| `mutants` | `sample_project/`: 5 independent utility modules, 34 tests | 103 single-token mutants (`mutations.py`), 92 caught by the tests | `data/teacher_runs.jsonl` |
| `shop` | `sample_shop/`: a 10-module webshop backend (money, dates, catalog, cart, discounts, tax, shipping, loyalty, orders, invoices), 65 tests | 83 hand-written bugs (`bugs.py`): wrong rules, boundaries, swallowed errors, wrong configuration, multi-line edits. 33 of them fail a test in another module (e.g. a `tax.py` bug caught by `test_orders.py`) | `data/teacher_runs_shop.jsonl` |
| `runbugrun` | `data/runbugrun/<problem>-<id>/`: one small project per bug, `main.py` plus a pytest file that runs it on the contest judge's inputs | 40 real bugs from RunBugRun (Prenner and Robbes, 2023): a buggy and a fixed Python submission that people made to the same contest problem (Project CodeNet), one bug per problem | `data/teacher_runs_runbugrun.jsonl` |

- The single-token mutants turned out too easy to separate the models (section 5),
  hence the shop. Each shop bug stays within one file, since the Fix agent rewrites
  one file.
- Groq's free tier allows 8,000 tokens per minute and one teacher run uses about
  10–15k tokens, so the script pauses between runs and waits out rate limits.
  If the daily limit is reached it stops; run the same command again later and it
  resumes where it left off.
- `runbugrun.py` reads the Python test split and the tests of the
  [RunBugRun data release](https://github.com/giganticode/run_bug_run_data/releases)
  (v0.0.1, downloaded into `data/runbugrun_raw/`). It keeps submissions of 5–60
  lines, standard library only, whose fix changes at most two places, with 3–6
  judge tests with exact (not float) outputs. A bug is kept only if, on this
  Python, the fixed version passes every test and the buggy one fails one. The
  programs run with an empty environment except `PATH`, so they cannot read the
  API keys.
- The generator turns on `llm_include_reasoning`, so each recorded call also keeps
  the teacher's reasoning text (production leaves it off).

## 2. Export the training files

```bash
cd backend
source .venv/bin/activate
python -m training.export_sft --runs training/data/teacher_runs_shop.jsonl --train-only training/data/teacher_runs.jsonl
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

From `backend/`, with `KAGGLE_API_TOKEN` in the repository `.env`:

```bash
cd backend
source .venv/bin/activate
pip install -r training/requirements.txt
python -m training.kaggle_runner upload-data
python -m training.kaggle_runner train --model qwen3-4b --poll 120
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

```bash
cd backend
source .venv/bin/activate
python -m training.eval_fix                  # production Fix prompt
python -m training.eval_fix --no-test-file   # Fix prompt without the tests
python -m training.eval_fix --summary        # table of saved results
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

The notebook only evaluates the held-out split of the training projects. For the
real bugs, `eval_local.py` runs both Qwen3-4B students in Ollama on the teacher's
exact Monitoring and Remediation prompts, with the notebook's checks and the
production confidence parser, and `eval_fix.py --local` takes their answers from
there (the base student is set up in section 8):

```bash
cd backend
source .venv/bin/activate
python -m training.eval_local       # 160 local answers, ~2 h on a GTX 1650
python -m training.eval_fix --runs training/data/teacher_runs_runbugrun.jsonl --local training/data/eval_local_runbugrun.jsonl --out training/data/fix_eval_runbugrun.jsonl
```

`compare.py` compares each source with the teacher on the same cases: both rates,
the difference with a 95% interval (Newcombe's method for paired proportions),
McNemar's exact test, and non-inferiority, i.e. whether the whole interval lies
above `--margin` (10 points by default):

```bash
cd backend
source .venv/bin/activate
python -m training.compare training/data/fix_eval_shop.jsonl training/data/fix_eval_runbugrun.jsonl --metric fix_ok
python -m training.compare training/data/eval_local_runbugrun.jsonl --agent monitoring --metric grounded
```

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
| Qwen3-4B fine-tuned | 15/17 (88%) | 47% | 2.8 |
| Teacher, gpt-oss-120b | 15/17 (88%) | 53% | 2.1 |
| gpt-oss-20b base | 15/17 (88%) | 65% | 2.8 |
| No Remediation report | 14/17 (82%) | 53% | 2.4 |
| Qwen3-4B base | 14/17 (82%) | 53% | 5.8 |

What the numbers say:

- **On harder bugs, fine-tuning gives significant gains on Monitoring**: Qwen3-4B
  learns to name the failing test (73% → 100%), gpt-oss-20b the report format
  (59% → 95%). Both reach the teacher on these checks.
- **Remediation does not improve measurably.** The base models already score 94%.
- **The end result does not separate the models.** Each fine-tuned student fixes
  one more bug than its base model and loses none (p = 1.0 at n = 17), and the
  control still fixes 82%: the Fix agent works mostly from the Diagnosis and the
  test file. `bulk-any-sku` fails for every source, because the Fix model writes
  the wrong comparison.
- **Fix agent robustness**, two production bugs found by this benchmark, both fixed
  in `agents/fix.py` and the table is after the fixes:
  - At `max_tokens=1200`, 6 of 102 Fix answers were empty: gpt-oss-120b's hidden
    reasoning counts against the budget. It now allows 4,000 tokens, retries once on
    an empty answer, and never judges or pushes an empty file.
  - The Fix agent was shown the first test file named in the console log. A verbose
    pytest log lists the passing ones too, so on the 175 recorded builds it picked a
    file without a failing test 148 times (85%), and fetched it from the repository
    root, missing a `tests/` folder. It now reads pytest's failure markers, prefers
    `test_<target file>`, and resolves the full path: right on 175 of 175. The
    benchmark uses the same choice; it changed the test file for 4 of the 17 bugs.

## 7. Results, real bugs (40 RunBugRun bugs, none seen in training)

**Teacher:** Monitoring 95%, Diagnosis 100%, Remediation 98% correct. Only the
Qwen3-4B students were run, since gpt-oss-20b does not fit a local GPU.

**Report checks** (out of 40; the teacher's answers are scored the same way):

| | Monitoring format | Monitoring names the failing test | Remediation format | Remediation names the function | Mean tokens, Mon. / Rem. |
|---|---|---|---|---|---|
| Teacher, gpt-oss-120b | 40 | 40 | 40 | 39 | 1,466 / 779 |
| Qwen3-4B fine-tuned | 40 | **40** | 39 | 40 | 1,314 / 710 |
| Qwen3-4B base | 40 | 18 | 40 | 38 | 569 / 1,074 |

**Fix success** (production prompt, the Fix agent on gpt-oss-120b):

| Remediation from | Build passes | Restored the original exactly |
|---|---|---|
| Qwen3-4B fine-tuned | **40/40 (100%)** | 38% |
| Teacher, gpt-oss-120b | 38/40 (95%) | 35% |
| Qwen3-4B base | 38/40 (95%) | 30% |
| No Remediation report | 36/40 (90%) | 30% |

Contest fixes often pass the tests without undoing the exact edit, hence the low
"restored exactly" for every source.

**Against the teacher**, 95% intervals for the difference, non-inferior if the
whole interval lies above −10 points. Fix success pools the shop and the real bugs
(57 cases):

| Measure | Qwen3-4B fine-tuned | Qwen3-4B base | No report |
|---|---|---|---|
| Build passes, 57 cases (teacher 93%) | 96%, +3.5 [−2.8, +11.6], **non-inferior** | 91%, −1.8 [−9.9, +5.8], non-inferior | 88%, −5.3 [−14.7, +3.1], not shown |
| Restored exactly, 57 cases (teacher 40%) | 40%, +0.0 [−8.2, +8.2], **non-inferior** | 37%, −3.5 [−8.4, +1.3], non-inferior | 37%, −3.5 [−12.9, +6.0], not shown |
| Monitoring names the failing test, 40 (teacher 100%) | 100%, +0 [−8.8, +8.8], **non-inferior** | 45%, −55 [−69.3, −37.5], p < 0.001 | |
| Remediation format, 40 (teacher 100%) | 98%, −2.5 [−12.9, +6.5], not shown | 100%, +0 [−8.8, +8.8] | |

What the numbers say:

- **The Monitoring gain holds on real code.** Fine-tuned Qwen3-4B names the failing
  test in 40 of 40 reports, the base model in 18 (+55 points, 22 to 0, p < 0.001).
- **Fine-tuned Qwen3-4B is non-inferior to the teacher** on builds fixed, exact
  restores and Monitoring grounding, within the 10-point margin. On Remediation
  format it misses one answer of 40, so non-inferiority is not shown there (the
  interval reaches −12.9).
- **What this does not show:** that it is *better* than the teacher (2 builds to 0,
  p = 0.5), or that it holds for large codebases: these are short single-file
  contest programs.
- **Monitoring's budget**, a production change found here: 5 of 120 Monitoring
  answers (2 teacher, 3 fine-tuned) hit the 2,500-token limit on long pytest logs
  and lost their confidence line. Monitoring now allows 4,000 tokens, locally and
  on Groq, and all 120 complete. Redone answers came out close to, but not exactly,
  the first ones: generation at temperature 0 is not fully deterministic, on Groq or
  in Ollama.
- **The notebook's confidence check is stricter than production's.** It misses
  `**AGENT_CONFIDENCE:** 92`, which the production parser accepts; `eval_local.py`
  uses the production parser. Rescored that way, the section 6 results change by
  one answer at most (gpt-oss-20b Remediation format, base and fine-tuned).

## 8. Run the local student in PipeGuard

```bash
cd backend/training/data/kaggle/qwen3-4b/pipeguard-qwen3-4b-gguf_gguf
ollama create pipeguard-qwen3-4b -f Modelfile
```

For the base model in `eval_local.py`, copy that Modelfile into a new folder, set
its first line to `FROM hf.co/unsloth/Qwen3-4B-GGUF:Q4_K_M` and run
`ollama create pipeguard-qwen3-4b-base -f Modelfile`: the same quantisation,
template and settings as the fine-tuned model.

In PipeGuard, enable **Local QLoRA model** under Integrations. The choice is
per signed-in user and persists across refreshes. Monitoring and Remediation use
the local model when that investigation selects QLoRA; Remediation's tool-enabled
GitHub source inspection path stays on Groq. If Ollama is unavailable or its
response is empty/truncated, PipeGuard falls back to Groq. Diagnosis, Fix, and
both judges keep their existing providers. Turn the switch off to request the
standard Groq pipeline. Qwen3's `<think>` block is moved out of the answer into
the logged `reasoning`, so reports keep their expected format.

| Setting | Default | |
|---|---|---|
| `LOCAL_LLM_MODEL` | `pipeguard-qwen3-4b` | Ollama model name used when QLoRA is enabled |
| `LOCAL_LLM_URL` | `http://localhost:11434` | Ollama endpoint reachable by the backend |
| `LOCAL_LLM_NUM_CTX` | 8192 | prompts are ~2–3k tokens plus up to 4,000 generated (Monitoring) |
| `LOCAL_LLM_NUM_GPU` | 99 | Ollama GPU layers |

Raw prompts and outputs can include console logs and source excerpts. Set
`STORE_LLM_CALLS=true` only when you want these records saved with incidents for
offline dataset generation; it defaults to false.

## Compare the Groq baseline with QLoRA

Run the same failed Jenkins build twice with the same evidence and integration
settings. First switch Local QLoRA off; then turn it on and repeat the build.
Each incident stores a compact `llm_performance` record in MongoDB: requested mode,
actual provider/model per logged call, input/output tokens, and agent latency.
`pipeline_duration_ms` records investigation time through the approval pause.
The incident's existing judge scores and eventual `feedback.build_passed` provide
quality and outcome measures. Raw prompts stay out of MongoDB unless
`STORE_LLM_CALLS=true` is explicitly enabled.

Check the recorded provider per agent: when GitHub source inspection is available,
Remediation stays on Groq by design, while Monitoring can use QLoRA. For a direct
Remediation comparison, keep source inspection unavailable for both runs so the
two runs exercise the same single-call path.
| `LOCAL_LLM_NUM_GPU` | 99 (all layers) | Ollama's estimate left 34–45% of the model on the CPU |

Measured on a GTX 1650 (4 GB) with a held-out shop bug: everything on the GPU
(3.9 GB), ~43 tokens/s, against 8 tokens/s with Ollama's default split. Monitoring
took 67 s (989 tokens, including loading the model) and Remediation 34 s (611 tokens),
against a few seconds on Groq; both named the right failing test and function.

## Limits

- **Data volume.** 175 teacher runs give 330 examples, small for SFT. The test sets
  (39 shop examples, 40 real bugs) rule out differences larger than about 10 points,
  not smaller ones. One training run per student, one seed.
- **Real bugs, but small programs.** The training bugs are written for this
  purpose. The 40 RunBugRun bugs are real, but short single-file contest
  programs; a test set from real projects (e.g. BugsInPy) would show whether the
  results hold on larger codebases.
- **Failure types.** The bugs are logic bugs caught by unit tests only, not
  dependency, infrastructure or flaky-test failures.
- **Serving.** gpt-oss-20b and Qwen3-14B need 9–14 GB even in 4-bit, so they cannot run
  on a 4 GB laptop GPU: they are evaluated on Kaggle as the upper bound of what
  distillation achieves. Qwen3-4B is the student meant for local use.
- **Local latency.** Reasoning adds hundreds of generated tokens per answer: on a
  GTX 1650 the local student takes 30–70 s per call instead of a few seconds
  (section 8; medians of 55 s for Monitoring and 37 s for Remediation on the real
  bugs).
- **Nondeterminism.** Repeating a run at temperature 0 can change an answer
  slightly, so a single run is one sample of each model.

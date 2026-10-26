# Fine-tuning: QLoRA distillation of gpt-oss-120b into gpt-oss-20b

The production agents run `openai/gpt-oss-120b` on Groq. This folder distils it into a
LoRA adapter on `gpt-oss-20b` for the two single-call agents, **Monitoring** and
**Remediation**. Diagnosis (a tool-calling loop) and the code fix stay on the large model.

```
sample_project/  ──mutations.py──►  failing builds with a known root cause
        │
generate_dataset.py   teacher = the real Monitoring → Diagnosis → Remediation nodes
        │             labels every output against the known file and function
        ▼
data/teacher_runs.jsonl ──export_sft.py──► data/sft/train.jsonl, val.jsonl
        │                                      (+ real incidents the engineer confirmed fixed)
        ▼
kaggle/qlora_gpt_oss_20b.ipynb   QLoRA on a free Kaggle T4, then evaluation vs. the base model
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

1. Create a Kaggle dataset named `pipeguard-sft` and upload `train.jsonl` and `val.jsonl`.
2. Import `kaggle/qlora_gpt_oss_20b.ipynb` as a notebook, attach the dataset, set the
   accelerator to **GPU T4 x2** and turn **Internet** on.
3. *Run All*. Outputs: the adapter (`pipeguard-gpt-oss-20b-lora.zip`),
   `eval_summary.csv` (fine-tuned vs. base vs. teacher) and `eval_results.jsonl`.

Settings: 4-bit base model, LoRA r = 8, α = 16 on all attention and MLP projections,
2 epochs, learning rate 2e-4, effective batch size 8, loss on the answer only. The
student is trained and evaluated with `reasoning_effort="low"`, so it answers directly
instead of spending tokens on hidden reasoning.

## Limits

- **Data volume.** 92 mutants give at most ~180 examples, enough to measure the
  approach but small for SFT. Add modules to `sample_project/` (with tests), or let real
  incidents accumulate, to grow it.
- **Failure types.** Mutants cover logic bugs caught by unit tests only, not dependency,
  infrastructure or flaky-test failures.
- **Serving.** gpt-oss-20b needs about 14 GB of memory even in 4-bit, so the adapter
  cannot run on a PC with a 4 GB GPU and 8 GB of RAM. Evaluate it on Kaggle; to use it in
  PipeGuard, serve it with vLLM on a GPU server or a hosted LoRA endpoint and point the
  agent at that OpenAI-compatible URL.

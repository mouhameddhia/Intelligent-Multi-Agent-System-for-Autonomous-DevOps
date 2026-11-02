# PipeGuard QLoRA benchmark

**Date:** 2026-11-02  
**Result type:** one paired, synthetic two-agent investigation (Monitoring → Remediation). This was not run against a live Jenkins job or persisted as a production incident.

## Results

| Measure | Groq baseline | QLoRA enabled |
|---|---:|---:|
| Actual provider | Groq for both agents | Ollama for both agents |
| Models | `openai/gpt-oss-120b` | `pipeguard-qwen3-4b` |
| Agent latency | 3.02 s total | 135.49 s total |
| End-to-end agent workflow | 3.24 s | 135.61 s |
| Input tokens | 1,673 | 1,602 |
| Output tokens | 841 | 962 |
| Monitoring latency | 1.34 s | 40.04 s |
| Remediation latency | 1.68 s | 95.46 s |
| Monitoring confidence marker | 100 | 95 |
| Remediation confidence marker | 85 | 90 |
| Failing test identified | Yes | Yes |
| Target function named | Yes | Yes |
| Structured remediation steps | 5 | 7 |

The tested case was a fabricated `PipeGuard-Lab` unit-test failure: `test_calculate_total` expected 30 but received 31. Both routes identified the failing test and `calculate_total`. The QLoRA run used `keep_alive=0` to unload the model after each call. It took about **42× longer** end-to-end and generated about **14% more output tokens** in this single run.

## Method and limits

- Both routes received the same synthetic Jenkins evidence and diagnosis.
- Each route used its own Monitoring report as the Remediation input, matching normal workflow. Therefore, the Remediation prompts were not byte-identical.
- One sample per route is a smoke benchmark, not a statistically reliable model evaluation. QLoRA timings include the configured unload-after-call behavior. Confidence values are model-reported markers, not calibrated scores. No independent judge or actual fixed-build outcome was measured here.
- These results show the trade-off on this machine: the local model ran successfully and produced usable structured reports, while being far slower than Groq. Keep QLoRA optional and use Groq as the default/fallback.

## Raw measurement record

Compact per-agent timing, token, provider, and output-quality checks are preserved in the ignored local artifact `backend/training/data/qlora_smoke_comparison.json`. Raw prompts and model outputs are excluded.

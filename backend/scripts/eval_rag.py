"""RAG evaluation harness for the Incident Advisor chatbot.

Measures retrieval quality (Hit Rate@1, Hit Rate@5, MRR, score margin)
against a small hand-labeled query set built from real incidents already
in this project's MongoDB (not synthetic fixtures) — the same incidents
used during manual testing of this feature. Also runs:
- a generation-quality pass (LLM-as-judge: faithfulness, relevance,
  citation correctness) over every retrieval-eval answer, against the
  SAME retrieved context the chatbot actually saw — not an idealized one.
  The judge is Gemini, a different provider/model family from the Groq
  model being judged, specifically so this isn't the same model grading
  its own homework.
- a qualitative abstention check (out-of-scope questions, where declining
  is the correct answer)
- end-to-end latency
Writes a full report to RAG_EVALUATION.md at the project root.

Usage (from backend/):
    python -m scripts.eval_rag
"""

import re
import time
from pathlib import Path

from langchain_google_genai import ChatGoogleGenerativeAI

from agent_system.agents.chatbot import answer_question
from agent_system.agents.llm_retry import invoke_with_retry
from agent_system.config import settings
from agent_system.db import rank_incidents_by_similarity, search_incidents_semantic

# Each case names a real query and how to recognize a "correct" retrieved
# incident: either an exact _id (topics with one known incident) or a
# job_name (the refund-bug cluster, which has many near-duplicate
# incidents from repeated test runs — any of them is a valid match).
#
# Covers 9 distinct topics, each created through the real /api/investigate
# pipeline (not synthetic fixtures), 3-4 phrasings per topic — including
# phrasings that never name the job, to test retrieval by meaning rather
# than keyword/job-name overlap.
RETRIEVAL_EVAL_SET = [
    # Topic: payment-service — requests 2.32.0 / urllib3 import break
    {"topic": "payment-service", "query": "why did the payment-service build fail?",
     "match": {"_id": "ab0c7e61-d3ac-4123-bfdf-0cd481a09edc"}},
    {"topic": "payment-service", "query": "have we seen this requests urllib3 error before?",
     "match": {"_id": "ab0c7e61-d3ac-4123-bfdf-0cd481a09edc"}},
    {"topic": "payment-service", "query": "a module not found error for urllib3.contrib after upgrading requests",
     "match": {"_id": "ab0c7e61-d3ac-4123-bfdf-0cd481a09edc"}},

    # Topic: frontend-e2e — ChromeDriver/Chrome version mismatch
    {"topic": "frontend-e2e", "query": "the browser tests keep timing out, what's wrong with the chromedriver version?",
     "match": {"_id": "8bd3f1df-1418-4db9-a65b-c0598eff310c"}},
    {"topic": "frontend-e2e", "query": "how do I fix the chromedriver mismatch?",
     "match": {"_id": "8bd3f1df-1418-4db9-a65b-c0598eff310c"}},
    {"topic": "frontend-e2e", "query": "selenium session not created exception after a chrome upgrade",
     "match": {"_id": "8bd3f1df-1418-4db9-a65b-c0598eff310c"}},

    # Topic: pipepline — refund fee calculation bug (many near-duplicate incidents)
    {"topic": "pipepline", "query": "why does calculate_refund return the wrong amount?",
     "match": {"job_name": "pipepline"}},
    {"topic": "pipepline", "query": "why did the pipepline build fail?",
     "match": {"job_name": "pipepline"}},
    {"topic": "pipepline", "query": "test_standard_refund keeps failing, what's the root cause?",
     "match": {"job_name": "pipepline"}},

    # Topic: checkout-api — Kubernetes pod OOMKilled after a caching feature
    {"topic": "checkout-api", "query": "why was the checkout-api pod OOMKilled?",
     "match": {"_id": "a0b5fb7a-1c70-45e2-a17b-c9d1bce6c808"}},
    {"topic": "checkout-api", "query": "a kubernetes deployment keeps getting killed for exceeding its memory limit, why?",
     "match": {"_id": "a0b5fb7a-1c70-45e2-a17b-c9d1bce6c808"}},
    {"topic": "checkout-api", "query": "readiness probe failing because the pod ran out of memory after a new cache feature",
     "match": {"_id": "a0b5fb7a-1c70-45e2-a17b-c9d1bce6c808"}},

    # Topic: inventory-service — SonarQube quality gate failure (duplication/coverage)
    {"topic": "inventory-service", "query": "why did the inventory-service quality gate fail?",
     "match": {"_id": "2a1b2c6a-ece5-4e4f-a748-a0f92b123497"}},
    {"topic": "inventory-service", "query": "sonarqube is blocking the deploy because of code duplication and low coverage, why?",
     "match": {"_id": "2a1b2c6a-ece5-4e4f-a748-a0f92b123497"}},

    # Topic: infra-apply — Terraform security group reference after a module split
    {"topic": "infra-apply", "query": "why did the terraform apply fail on infra-apply?",
     "match": {"_id": "84561345-b1bc-4bee-8755-fd9e963166a0"}},
    {"topic": "infra-apply", "query": "terraform error InvalidGroup.NotFound after splitting the vpc module, what happened?",
     "match": {"_id": "84561345-b1bc-4bee-8755-fd9e963166a0"}},

    # Topic: billing-db-migrate — Alembic migration column conflict
    {"topic": "billing-db-migrate", "query": "why did the billing database migration fail?",
     "match": {"_id": "8d9152d0-a799-43c2-8cb5-1109f2143768"}},
    {"topic": "billing-db-migrate", "query": "alembic migration says a column already exists, what's going on?",
     "match": {"_id": "8d9152d0-a799-43c2-8cb5-1109f2143768"}},

    # Topic: image-publish — expired ECR auth token during docker push
    {"topic": "image-publish", "query": "why did the docker image push to ECR fail?",
     "match": {"_id": "c43f9ee0-c327-401b-b27a-66c44d16e33c"}},
    {"topic": "image-publish", "query": "denied, authorization token has expired, during a long-running build push step",
     "match": {"_id": "c43f9ee0-c327-401b-b27a-66c44d16e33c"}},

    # Topic: notification-worker — asyncio race condition in dedup cache
    {"topic": "notification-worker", "query": "why is the notification-worker test flaky with a dedup cache assertion error?",
     "match": {"_id": "96607a1a-690b-4874-b8fd-ef2008d1355f"}},
    {"topic": "notification-worker", "query": "two async tasks racing to write the same cache key after adding asyncio.gather",
     "match": {"_id": "96607a1a-690b-4874-b8fd-ef2008d1355f"}},
]

# Out-of-scope questions where the correct behavior is to decline rather
# than invent an answer — reported qualitatively, not scored numerically.
ABSTENTION_EVAL_SET = [
    "what's the weather like today?",
    "which job fails most often?",
    "what's our overall build approval rate?",
    "can you write me a poem about deployments?",
    "has the inventory-service ever had a database migration issue?",  # plausible-sounding but no such incident exists
]


def _is_match(doc: dict, match: dict) -> bool:
    if "_id" in match:
        return doc["_id"] == match["_id"]
    return doc.get("job_name") == match["job_name"]


def evaluate_retrieval() -> list[dict]:
    rows = []
    for case in RETRIEVAL_EVAL_SET:
        ranked = rank_incidents_by_similarity(case["query"])
        rank = next((i + 1 for i, (doc, _) in enumerate(ranked) if _is_match(doc, case["match"])), None)
        correct_score = next((score for doc, score in ranked if _is_match(doc, case["match"])), None)
        wrong_score = next((score for doc, score in ranked if not _is_match(doc, case["match"])), None)
        margin = (correct_score - wrong_score) if correct_score is not None and wrong_score is not None else None
        rows.append({
            "topic": case["topic"],
            "query": case["query"],
            "rank": rank,
            # Binary hit/miss within top-k — correctly named Hit Rate@k, not
            # Precision@k. Textbook Precision@k is "what fraction of the k
            # retrieved items are relevant"; with exactly one relevant
            # incident per query here, a hit would give 1/5 = 0.2, not 1.0.
            # Don't rename back to "precision" without changing the math.
            "hit_at_1": 1 if rank == 1 else 0,
            "hit_at_5": 1 if rank and rank <= 5 else 0,
            "reciprocal_rank": (1 / rank) if rank else 0.0,
            "correct_score": correct_score,
            "margin": margin,
        })
    return rows


def evaluate_abstention() -> list[dict]:
    rows = []
    for query in ABSTENTION_EVAL_SET:
        result = answer_question(query)
        rows.append({
            "query": query,
            "answer": result["answer"],
            "sources_count": len(result["sources"]),
        })
    return rows


# LLM-as-judge for generation quality. Deliberately a binary pass/fail
# rubric per criterion, not open-ended 1-5 scoring — open scoring from an
# LLM judge is noisy and hard to defend; binary criteria are at least
# checkable by re-reading the transcript.
JUDGE_SYSTEM_PROMPT = """You are an evaluator judging ONE answer produced by an incident-retrieval chatbot. You are given the user's question, the chatbot's answer, and the full text of the incident report(s) it retrieved and was allowed to use. Judge strictly against that retrieved text only — do not use outside knowledge, and do not reward an answer for being plausible if it is not actually supported.

FAITHFUL: "yes" only if every factual claim in the answer (cause, file, commit, fix, number) is supported by the retrieved incident text below; "no" if the answer states anything not present in that text.
RELEVANT: "yes" if the answer actually addresses the question asked; "no" if it is off-topic or evasive.
CITATION_CORRECT: "yes" if the incident(s) the chatbot's answer is actually about are the ones it cited; "no" if the citation is unrelated to the claims made.

Respond with exactly these three lines and nothing else:
FAITHFUL: yes|no
RELEVANT: yes|no
CITATION_CORRECT: yes|no
"""


def _build_judge_context(incidents: list[dict]) -> str:
    if not incidents:
        return "(no incidents retrieved)"
    blocks = []
    for doc in incidents:
        parts = [doc.get(f) for f in ("diagnosis_report", "remediation_report", "custom_solution") if doc.get(f)]
        blocks.append(
            f"Incident {doc.get('_id')} (job={doc.get('job_name')} build=#{doc.get('build_number')}):\n"
            + "\n\n".join(parts)
        )
    return "\n\n---\n\n".join(blocks)


def _parse_judge_response(text: str) -> dict:
    result = {}
    for key in ("FAITHFUL", "RELEVANT", "CITATION_CORRECT"):
        match = re.search(rf"{key}:\s*(yes|no)", text, re.IGNORECASE)
        result[key.lower()] = (match.group(1).lower() == "yes") if match else None
    return result


def judge_answer(query: str, answer: str, incidents: list[dict]) -> dict:
    """LLM-as-judge pass, graded against the SAME retrieved incidents the
    chatbot actually saw (not an idealized ground truth), so this measures
    whether the generator stayed faithful to its own context — not whether
    retrieval found the objectively best incident (that's evaluate_retrieval's job)."""
    context = _build_judge_context(incidents)
    # Gemini, not the Groq model being judged — a different provider/model
    # family on purpose, so this isn't the same model grading its own
    # homework. Using the "flash-lite" tier deliberately: the full
    # gemini-3.8-flash model (a) is a thinking model that burns its token
    # budget on hidden thought tokens before any visible output, needing
    # thinking_budget=0 to avoid empty responses, and (b) has only a
    # 20-requests/day free-tier cap — too low to run a 22-query sweep at
    # all. gemini-3.5-flash-lite has fixed sampling (temperature and
    # thinking_budget aren't accepted — passing them causes a 400) and a
    # much higher free-tier daily quota.
    llm = ChatGoogleGenerativeAI(
        model=settings.gemini_judge_model,
        google_api_key=settings.gemini_api_key,
        max_tokens=500,
    )

    def _ask() -> dict:
        result = invoke_with_retry(lambda: llm.invoke([
            ("system", JUDGE_SYSTEM_PROMPT),
            ("human", f"Question: {query}\n\nChatbot's answer:\n{answer}\n\nRetrieved incident text:\n{context}"),
        ]))
        raw = result.content if isinstance(result.content, str) else str(result.content)
        return _parse_judge_response(raw)

    parsed = _ask()
    if all(v is None for v in parsed.values()):
        parsed = _ask()
    return parsed


def evaluate_generation() -> list[dict]:
    """For each retrieval-eval query: get the chatbot's real answer, then
    judge it against the exact incidents it retrieved. A None value means
    the judge's response didn't parse — reported honestly as unknown, not
    silently counted as a pass."""
    rows = []
    for case in RETRIEVAL_EVAL_SET:
        incidents = search_incidents_semantic(case["query"], limit=5)
        result = answer_question(case["query"])
        judged = judge_answer(case["query"], result["answer"], incidents)
        rows.append({
            "topic": case["topic"],
            "query": case["query"],
            "faithful": judged["faithful"],
            "relevant": judged["relevant"],
            "citation_correct": judged["citation_correct"],
        })
    return rows


def measure_latency(sample_size: int = 6) -> list[float]:
    """Samples spread across the eval set (every Nth query) so the average
    reflects different topics, not just whichever ones happen to be listed first."""
    step = max(1, len(RETRIEVAL_EVAL_SET) // sample_size)
    samples = []
    for case in RETRIEVAL_EVAL_SET[::step][:sample_size]:
        start = time.perf_counter()
        answer_question(case["query"])
        samples.append(time.perf_counter() - start)
    return samples


def _row_to_markdown(r: dict) -> str:
    margin = f"{r['margin']:.4f}" if r["margin"] is not None else "—"
    score = f"{r['correct_score']:.4f}" if r["correct_score"] is not None else "—"
    return (
        f"| {r['topic']} | {r['query']} | {r['rank']} | {r['hit_at_1']} | {r['hit_at_5']} | "
        f"{r['reciprocal_rank']:.3f} | {score} | {margin} |"
    )


def _topic_summary_rows(retrieval_rows: list[dict]) -> list[str]:
    topics = sorted({r["topic"] for r in retrieval_rows}, key=lambda t: [r["topic"] for r in retrieval_rows].index(t))
    lines = []
    for topic in topics:
        topic_rows = [r for r in retrieval_rows if r["topic"] == topic]
        n = len(topic_rows)
        p1 = sum(r["hit_at_1"] for r in topic_rows) / n
        mrr = sum(r["reciprocal_rank"] for r in topic_rows) / n
        margins = [r["margin"] for r in topic_rows if r["margin"] is not None]
        mean_margin = sum(margins) / len(margins) if margins else None
        margin_str = f"{mean_margin:.4f}" if mean_margin is not None else "—"
        lines.append(f"| {topic} | {n} | {p1:.2f} | {mrr:.3f} | {margin_str} |")
    return lines


def _bool_cell(value) -> str:
    if value is None:
        return "?"
    return "✅" if value else "❌"


def _generation_summary(generation_rows: list[dict]) -> tuple[float, float, float]:
    def rate(key: str) -> float:
        values = [r[key] for r in generation_rows if r[key] is not None]
        return (sum(values) / len(values)) if values else float("nan")
    return rate("faithful"), rate("relevant"), rate("citation_correct")


def render_report(
    retrieval_rows: list[dict],
    generation_rows: list[dict],
    abstention_rows: list[dict],
    latency_samples: list[float],
) -> str:
    n = len(retrieval_rows)
    mean_p1 = sum(r["hit_at_1"] for r in retrieval_rows) / n
    mean_p5 = sum(r["hit_at_5"] for r in retrieval_rows) / n
    mrr = sum(r["reciprocal_rank"] for r in retrieval_rows) / n
    margins = [r["margin"] for r in retrieval_rows if r["margin"] is not None]
    mean_margin = sum(margins) / len(margins) if margins else None
    mean_latency = sum(latency_samples) / len(latency_samples) if latency_samples else None

    lines = [
        "# RAG evaluation report — Incident Advisor chatbot",
        "",
        f"Generated by `backend/scripts/eval_rag.py`. {n} labeled retrieval queries across "
        "9 distinct failure topics (3-4 phrasings each, including phrasings that never name "
        "the job) plus 5 abstention checks, all against real incidents created through the "
        "actual investigation pipeline — not synthetic fixtures.",
        "",
        "## Summary",
        "",
        f"- **Hit Rate@1:** {mean_p1:.2f}",
        f"- **Hit Rate@5:** {mean_p5:.2f}",
        f"- **MRR (Mean Reciprocal Rank):** {mrr:.3f}",
    ]
    if mean_margin is not None:
        lines.append(f"- **Mean score margin** (correct topic vs. best wrong-topic score): {mean_margin:.4f}")
    if mean_latency is not None:
        lines.append(f"- **Mean end-to-end latency** (embed query + retrieve + Groq answer): {mean_latency:.2f}s")
    faithful_rate, relevant_rate, citation_rate = _generation_summary(generation_rows)
    lines.append(f"- **Faithfulness (LLM-judged):** {faithful_rate:.2f}")
    lines.append(f"- **Relevance (LLM-judged):** {relevant_rate:.2f}")
    lines.append(f"- **Citation correctness (LLM-judged):** {citation_rate:.2f}")
    lines += [
        "",
        "## Per-topic breakdown",
        "",
        "| Topic | Queries | Hit Rate@1 | MRR | Mean margin |",
        "|---|---|---|---|---|",
    ]
    lines += _topic_summary_rows(retrieval_rows)
    lines += [
        "",
        "## Retrieval detail",
        "",
        "| Topic | Query | Rank | Hit@1 | Hit@5 | RR | Score | Margin |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines += [_row_to_markdown(r) for r in retrieval_rows]

    lines += [
        "",
        "## Generation quality (LLM-judged)",
        "",
        "Answers are generated by Groq (`" + settings.llm_model + "`) and judged by Gemini "
        "(`" + settings.gemini_judge_model + "`) — a different provider/model family from the one being "
        "judged, so this isn't the same model grading its own homework. Judged against the exact incident "
        "text the chatbot actually retrieved for that query — not an idealized ground truth. A binary "
        "pass/fail rubric per criterion, not open-ended scoring (less noisy, and each verdict is checkable "
        "by re-reading the transcript). `?` means the judge's response didn't parse into yes/no and is "
        "reported as unknown, not silently counted as a pass.",
        "",
        "**Using a different model family is a real improvement over same-model judging, but it is still "
        "not a substitute for human review** — it has not itself been validated against human-labeled "
        "ground truth, and a different model can still share correlated blind spots or simply be wrong "
        "with confidence.",
        "",
        "| Topic | Query | Faithful | Relevant | Citation correct |",
        "|---|---|---|---|---|",
    ]
    for r in generation_rows:
        lines.append(
            f"| {r['topic']} | {r['query']} | {_bool_cell(r['faithful'])} | "
            f"{_bool_cell(r['relevant'])} | {_bool_cell(r['citation_correct'])} |"
        )

    lines += [
        "",
        "## Abstention check (qualitative)",
        "",
        "Out-of-scope questions, where the correct behavior is to decline rather than invent an answer. "
        "Note: `search_incidents_semantic` always returns its top-k regardless of how low the scores are "
        "(no cutoff threshold) — abstention happens at the LLM layer, which is told to ignore irrelevant "
        "context, not at retrieval. That's why `sources returned` is non-zero below even on a correct decline.",
        "",
    ]
    for r in abstention_rows:
        lines.append(f"**Q:** {r['query']}")
        lines.append("")
        lines.append(f"> {r['answer']}")
        lines.append("")
        lines.append(f"_(sources returned: {r['sources_count']})_")
        lines.append("")

    lines += [
        "## What this still does NOT measure",
        "",
        "Retrieval (Hit Rate@1/@5, MRR, margin) is measured directly against ground",
        "truth — those numbers are solid. Faithfulness, relevance, and citation",
        "correctness above are now measured too, by Gemini judging Groq's answers —",
        "a different model family, not the same model grading its own homework. That's",
        "a real improvement, but still not proof:",
        "",
        "- **No human has verified the judge's verdicts.** A different-provider judge",
        "  removes same-model blind spots, but can still be fooled by a confident-sounding",
        "  wrong answer, or wrong in a different, equally unverified way. Treat the",
        "  Faithful/Relevant/Citation-correct numbers above as a stronger signal than a",
        "  same-model judge would give, not as proof.",
        "- **Accuracy of remediation recommendations is still fully unmeasured.**",
        "  Several transcripts in this session included remediation steps the LLM added",
        "  on its own (e.g. the ChromeDriver and requests/urllib3 fixes). Faithfulness",
        "  checks whether a claim is *supported by the retrieved text* — it does NOT",
        "  check whether a recommended fix *actually works*. That needs either a human",
        "  domain expert or a hand-written known-good fix to compare against, neither of",
        "  which exists here.",
        "- **The judge rubric is binary per criterion, not exhaustive.** It catches",
        "  \"this claim isn't in the source text\" but would not catch a subtly wrong",
        "  number restated from a source that does contain it, or a technically-faithful",
        "  answer that's still misleading.",
        "",
        "Closing these gaps for real needs a human reviewer scoring a sample of these",
        "same transcripts against the same rubric, to check the judge against ground truth.",
    ]

    return "\n".join(lines)


def main() -> None:
    print("Running retrieval evaluation...")
    retrieval_rows = evaluate_retrieval()
    print("Running generation evaluation (LLM-as-judge)...")
    generation_rows = evaluate_generation()
    print("Running abstention checks...")
    abstention_rows = evaluate_abstention()
    print("Measuring latency...")
    latency_samples = measure_latency()

    report = render_report(retrieval_rows, generation_rows, abstention_rows, latency_samples)
    out_path = Path(__file__).resolve().parents[2] / "RAG_EVALUATION.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"Report written to {out_path}")


if __name__ == "__main__":
    main()

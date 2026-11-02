"""Renders a policy version as the advisory section for the Remediation prompt,
capped at about MAX_RENDER_TOKENS. Only `active` rules are shown; candidates
are unvalidated and stay out of prompts. Returns "" when there is nothing
relevant, so an empty or irrelevant policy leaves the prompt untouched."""

import math

from .models import PolicyVersion, Rule

SECTION_TITLE = "Learned guidance from past incidents (advisory)"
MAX_RENDER_TOKENS = 800
MAX_RULES_RENDERED = 8

_INTRO = (
    "Lessons distilled from earlier incidents. They are hints only: the current "
    "evidence takes precedence, and every action still requires human approval."
)


def estimate_tokens(text: str) -> int:
    """Rough estimate (about 4 characters per token); no tokenizer dependency."""
    return math.ceil(len(text) / 4)


def _rank(rule: Rule) -> tuple[int, int]:
    return (rule.success_count - rule.failure_count, rule.support_count)


def _prior_lines(policy: PolicyVersion, failure_category: str) -> list[str]:
    rows = []
    for key, counts in policy.priors.items():
        category, _, action = key.partition("|")
        total = counts.success + counts.failure
        if category == failure_category and total > 0:
            rows.append((counts.success / total, total, action, counts))
    rows.sort(key=lambda r: (-r[0], -r[1], r[2]))
    return [
        f"- {action}: {c.success} succeeded, {c.failure} failed (n={c.success + c.failure})"
        for _, _, action, c in rows
    ]


def render_guidance(
    policy: PolicyVersion | None,
    failure_category: str,
    max_tokens: int = MAX_RENDER_TOKENS,
) -> str:
    if policy is None:
        return ""
    rules = sorted(
        (r for r in policy.rules if r.status == "active" and r.failure_category == failure_category),
        key=_rank,
        reverse=True,
    )[:MAX_RULES_RENDERED]
    priors = _prior_lines(policy, failure_category)
    if not rules and not priors:
        return ""

    def build(rule_subset: list[Rule]) -> str:
        parts = [SECTION_TITLE, _INTRO]
        if priors:
            parts.append(f"Past outcomes for '{failure_category}' failures, by fix type:")
            parts.extend(priors)
        if rule_subset:
            parts.append("Lessons:")
            parts.extend(f"- {r.text} (seen in {r.support_count} incidents)" for r in rule_subset)
        return "\n".join(parts)

    # Drop the lowest-ranked rules until the section fits the budget.
    while True:
        text = build(rules)
        if estimate_tokens(text) <= max_tokens or not rules:
            break
        rules = rules[:-1]
    if estimate_tokens(text) > max_tokens:
        # Even priors alone overflow: keep the title and the first lines that fit.
        lines, kept = text.splitlines(), []
        for line in lines:
            if estimate_tokens("\n".join(kept + [line])) > max_tokens:
                break
            kept.append(line)
        text = "\n".join(kept)
    return text

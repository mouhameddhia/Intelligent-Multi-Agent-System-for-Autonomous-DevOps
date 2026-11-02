from agent_system.learning.models import PolicyVersion, PriorCount, Rule
from agent_system.learning.render import (
    MAX_RENDER_TOKENS,
    SECTION_TITLE,
    estimate_tokens,
    render_guidance,
)


def rule(rid, text, category="dependency", status="active", support=3, success=3):
    return Rule(id=rid, text=text, failure_category=category, status=status,
                support_count=support, success_count=success)


def policy(rules=(), priors=None):
    return PolicyVersion(version=1, rules=list(rules), priors=priors or {})


def test_no_policy_renders_nothing():
    assert render_guidance(None, "dependency") == ""


def test_policy_with_nothing_relevant_renders_nothing():
    p = policy([rule("a", "Pin the dependency version first", category="config")])
    assert render_guidance(p, "dependency") == ""


def test_title_and_active_rules_for_the_category_only():
    p = policy([
        rule("a", "Pin the dependency version first"),
        rule("b", "Candidate rule that must stay out", status="candidate", support=1, success=1),
        rule("c", "Retired rule that must stay out", status="retired"),
        rule("d", "Config rule for another category", category="config"),
    ])
    text = render_guidance(p, "dependency")
    assert text.startswith(SECTION_TITLE)
    assert "Pin the dependency version first" in text
    assert "must stay out" not in text and "another category" not in text
    assert "human approval" in text


def test_priors_are_rendered_for_the_category():
    p = policy(priors={
        "dependency|code_patch": PriorCount(success=6, failure=1),
        "dependency|rerun": PriorCount(success=0, failure=3),
        "config|code_patch": PriorCount(success=9, failure=0),
    })
    text = render_guidance(p, "dependency")
    assert "code_patch: 6 succeeded, 1 failed (n=7)" in text
    assert "rerun: 0 succeeded, 3 failed (n=3)" in text
    assert text.index("code_patch") < text.index("rerun")  # best success rate first
    assert "config" not in text.split("fix type:")[1]


def test_rules_are_ranked_by_net_outcome():
    p = policy([
        rule("lo", "Lower ranked rule text here", support=2, success=2),
        rule("hi", "Higher ranked rule text here", support=6, success=6),
    ])
    text = render_guidance(p, "dependency")
    assert text.index("Higher ranked") < text.index("Lower ranked")


def test_output_is_capped_by_tokens_dropping_lowest_ranked_first():
    rules = [rule(f"r{i}", f"Rule number {i}: " + "x" * 280, support=10 - i, success=10 - i) for i in range(8)]
    text = render_guidance(policy(rules), "dependency", max_tokens=300)
    assert estimate_tokens(text) <= 300
    assert "Rule number 0" in text
    assert "Rule number 7" not in text


def test_default_cap_holds_for_the_largest_possible_policy():
    rules = [rule(f"r{i}", "x" * 300) for i in range(30)]
    priors = {f"dependency|action{i}": PriorCount(success=i, failure=1) for i in range(10)}
    text = render_guidance(policy(rules, priors), "dependency")
    assert estimate_tokens(text) <= MAX_RENDER_TOKENS


def test_small_budget_is_respected():
    p = policy([rule("a", "Pin the dependency version first")], {"dependency|rerun": PriorCount(success=1)})
    text = render_guidance(p, "dependency", max_tokens=30)
    assert estimate_tokens(text) <= 30 and text.startswith(SECTION_TITLE)


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1 and estimate_tokens("abcde") == 2

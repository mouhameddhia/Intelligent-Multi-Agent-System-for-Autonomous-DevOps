"""Paired comparison of answer sources, with the uncertainty a few dozen cases leave.

For each source against a reference (the teacher by default), on the cases
both were run on:
  - the two success rates and their difference,
  - a 95% interval for that difference (Newcombe's method for paired
    proportions, method 10 of Newcombe 1998), i.e. the range of true
    differences these results are compatible with,
  - McNemar's exact test (are the discordant cases one-sided?),
  - non-inferiority: the source is at most --margin points worse than the
    reference if the whole interval lies above -margin.

Inputs are fix_eval.py results (metric fix_ok or reverted) or eval_local.py /
notebook results (format_ok, grounded; pick the agent with --agent).

    docker compose exec backend python -m training.compare \\
        training/data/fix_eval_shop.jsonl training/data/fix_eval_runbugrun.jsonl
    docker compose exec backend python -m training.compare training/data/eval_local_runbugrun.jsonl \\
        --metric grounded --agent monitoring --reference "qwen3-4b base"
"""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

TEACHER = "teacher (gpt-oss-120b)"


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


def newcombe_paired(both: int, only_a: int, only_b: int, neither: int) -> tuple[float, float, float]:
    """Difference p_a - p_b of paired proportions and its 95% interval."""
    n = both + only_a + only_b + neither
    p_a, p_b = (both + only_a) / n, (both + only_b) / n
    l_a, u_a = wilson(both + only_a, n)
    l_b, u_b = wilson(both + only_b, n)
    denominator = (both + only_a) * (only_b + neither) * (both + only_b) * (only_a + neither)
    phi = (both * neither - only_a * only_b) / math.sqrt(denominator) if denominator else 0.0
    delta = p_a - p_b
    lower = delta - math.sqrt(max(0.0, (p_a - l_a) ** 2 - 2 * phi * (p_a - l_a) * (u_b - p_b) + (u_b - p_b) ** 2))
    upper = delta + math.sqrt(max(0.0, (u_a - p_a) ** 2 - 2 * phi * (u_a - p_a) * (p_b - l_b) + (p_b - l_b) ** 2))
    return delta, lower, upper


def mcnemar_exact(only_a: int, only_b: int) -> float:
    discordant = only_a + only_b
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, k) for k in range(min(only_a, only_b) + 1)) / 2 ** discordant
    return min(1.0, 2 * tail)


def _case(record: dict) -> str:
    mutant = record.get("mutant")
    if mutant:
        return json.dumps(mutant, sort_keys=True)
    return f"{record.get('id') or record.get('group')}|{record.get('index', '')}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", type=Path, nargs="+")
    parser.add_argument("--metric", default="fix_ok")
    parser.add_argument("--agent", help="for report-quality results: monitoring or remediation")
    parser.add_argument("--variant", default="production", help="fix_eval.py variant to keep")
    parser.add_argument("--reference", default=TEACHER)
    parser.add_argument("--margin", type=float, default=10, help="non-inferiority margin, in points")
    args = parser.parse_args()

    outcome = defaultdict(dict)  # source -> case -> bool
    for path in args.results:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                if "variant" in record and record["variant"] != args.variant:
                    continue
                if args.agent and record.get("agent") != args.agent:
                    continue
                source = record.get("source") or record.get("model")
                outcome[source][f"{path.name}|{_case(record)}"] = bool(record[args.metric])

    reference = outcome[args.reference]
    print(f"{args.metric}{' (' + args.agent + ')' if args.agent else ''} against {args.reference}, "
          f"95% intervals, non-inferiority margin {args.margin:g} points\n")
    print(f"{'source':<26} {'n':>3} {'source':>7} {'ref':>6} {'diff':>6} {'95% interval':>16} "
          f"{'+/-':>5} {'McNemar p':>10}  within margin")
    for source in sorted(outcome):
        if source == args.reference:
            continue
        cases = sorted(set(outcome[source]) & set(reference))
        if not cases:
            continue
        pairs = [(outcome[source][case], reference[case]) for case in cases]
        both = sum(a and b for a, b in pairs)
        only_a = sum(a and not b for a, b in pairs)
        only_b = sum(b and not a for a, b in pairs)
        neither = len(pairs) - both - only_a - only_b
        delta, lower, upper = newcombe_paired(both, only_a, only_b, neither)
        n = len(pairs)
        print(f"{source:<26} {n:>3} {(both + only_a) / n:>7.0%} {(both + only_b) / n:>6.0%} "
              f"{delta * 100:>+6.1f} {f'[{lower * 100:+.1f}, {upper * 100:+.1f}]':>16} "
              f"{only_a:>2}/{only_b:<2} {mcnemar_exact(only_a, only_b):>10.3f}  "
              f"{'yes' if lower * 100 > -args.margin else 'no'}")


if __name__ == "__main__":
    main()

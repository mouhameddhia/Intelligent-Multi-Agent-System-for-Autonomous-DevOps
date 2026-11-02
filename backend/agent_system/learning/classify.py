"""Deterministic classification of an incident from structured fields only
(the Diagnosis report text, incident_type, touched file names, fix_result,
proposed steps). No LLM call and no raw console log, so it is safe to run in
the Remediation hot path later."""

import hashlib
import re
from typing import Any

OTHER = "other"

# Keyword lists per failure category. Highest hit count wins; ties go to the
# earlier entry.
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "dependency": (
        "dependency", "dependencies", "requirements.txt", "pip install", "npm install",
        "package version", "modulenotfounderror", "importerror", "module not found",
        "lockfile", "version conflict",
    ),
    "build_compile": (
        "syntaxerror", "syntax error", "compilation", "compile error", "compiler",
        "cannot find symbol", "linker", "build script",
    ),
    "infra_flaky": (
        "timeout", "timed out", "flaky", "out of memory", "oom", "connection refused",
        "disk space", "agent offline", "network", "intermittent",
    ),
    "config": (
        "config", "configuration", "environment variable", "env var", "jenkinsfile",
        "credentials", "permission denied", "yaml", "yml",
    ),
    "test_assertion": (
        "assertion", "assertionerror", "test failure", "failed test", "test fails",
        "regression", "expected", "unit test",
    ),
}

_PATTERNS = {
    category: re.compile(r"\b(?:" + "|".join(re.escape(k) for k in words) + r")\b")
    for category, words in _CATEGORY_KEYWORDS.items()
}

CATEGORIES = tuple(_CATEGORY_KEYWORDS) + (OTHER,)

_INCIDENT_TYPE_HINT = {"test_failure": "test_assertion"}

_ACTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rollback", ("rollback", "roll back", "revert")),
    ("restart", ("restart", "reboot", "redeploy")),
    ("dependency_update", ("dependency", "pip install", "npm install", "upgrade", "downgrade", "version bump", "requirements.txt")),
    ("config_change", ("config", "environment variable", "env var", "jenkinsfile", "credentials", "yaml", "yml")),
    ("rerun", ("rerun", "re-run", "retry", "rebuild", "re-trigger")),
    ("code_patch", ("fix the", "patch", "correct the", "update the function", "change the code", "modify")),
)
_ACTION_PATTERNS = [
    (name, re.compile(r"(?:\b" + r"|\b".join(re.escape(k) for k in words) + r")"))
    for name, words in _ACTION_KEYWORDS
]

ACTION_TYPES = tuple(name for name, _ in _ACTION_KEYWORDS) + (OTHER,)


def classify_failure(diagnosis_report: str, incident_type: str = "") -> str:
    text = (diagnosis_report or "").lower()
    scores = {category: len(pattern.findall(text)) for category, pattern in _PATTERNS.items()}
    hint = _INCIDENT_TYPE_HINT.get((incident_type or "").lower()) or (
        (incident_type or "").lower() if (incident_type or "").lower() in _PATTERNS else None
    )
    if hint in scores:
        scores[hint] += 1
    best = max(scores.values(), default=0)
    if best == 0:
        return OTHER
    return next(category for category in _CATEGORY_KEYWORDS if scores[category] == best)


def files_touched(evidence: dict[str, Any] | None) -> list[str]:
    github = (evidence or {}).get("github") or {}
    files = github.get("files") or []
    return sorted({
        f["filename"].lower()
        for f in files
        if isinstance(f, dict) and isinstance(f.get("filename"), str) and f["filename"]
    })


def failure_signature(category: str, incident_type: str, files: list[str]) -> str:
    basis = "|".join([category, (incident_type or "").lower(), ",".join(sorted(files))])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def classify_fix_action(step_texts: list[str], fix_result: dict[str, Any] | None) -> str:
    if isinstance(fix_result, dict) and fix_result.get("applied") is True:
        return "code_patch"
    text = " ".join(step_texts or []).lower()
    scores = {name: len(pattern.findall(text)) for name, pattern in _ACTION_PATTERNS}
    best = max(scores.values(), default=0)
    if best == 0:
        return OTHER
    return next(name for name, _ in _ACTION_KEYWORDS if scores[name] == best)

"""Apply the approved remediation for real: identify the file responsible for
the failure, fetch it (and its test file, if any) from GitHub, have the LLM
rewrite it based on the diagnosis/remediation reports AND the actual test
assertions, then push the corrected file back to the repository."""

import base64
import re

import httpx
from langchain_groq import ChatGroq

from ..config import settings
from ..orchestrator.state import InvestigationState
from .judge import judge_fix

GITHUB_API_URL = "https://api.github.com"

_SOURCE_EXTENSIONS = (
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rb",
    ".php", ".c", ".cpp", ".cs", ".rs", ".kt", ".swift",
)
_NEVER_TARGET = (
    "jenkinsfile", "dockerfile", "readme", "license", "changelog",
    ".yml", ".yaml", ".md", ".txt", ".json", ".lock", ".toml", ".cfg", ".ini",
)
_IGNORED_MODULES = {
    "os", "sys", "re", "json", "unittest", "pytest", "math", "time",
    "datetime", "collections", "itertools", "functools", "typing",
    "logging", "subprocess", "pathlib", "random", "copy", "io",
    "unittest.mock", "mock", "abc", "enum", "dataclasses",
}


def _is_plausible_source_file(path: str) -> bool:
    lowered = path.lower()
    if any(token in lowered for token in _NEVER_TARGET):
        return False
    if not lowered.endswith(_SOURCE_EXTENSIONS):
        return False
    return True


def _extract_paths_from_text(text: str) -> list[str]:
    pattern = re.compile(r"[\w\-/\.]+\.(?:py|js|jsx|ts|tsx|java|go|rb|php|c|cpp|cs|rs|kt|swift)\b")
    return pattern.findall(text or "")


def _headers(token: str) -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _list_repo_root_files(owner: str, repo: str, token: str) -> list[str]:
    try:
        resp = httpx.get(
            f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/",
            headers=_headers(token),
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            return []
        return [item["name"] for item in data if isinstance(item, dict) and item.get("type") == "file"]
    except Exception:
        return []


def _explain_http_error(exc: Exception, owner: str, repo: str, path: str = "") -> str:
    """Turn a GitHub/httpx failure into one sentence an engineer can act on."""
    code = getattr(getattr(exc, "response", None), "status_code", None)
    target = f"{owner}/{repo}" + (f" ({path})" if path else "")
    if code == 401:
        return (
            "GitHub rejected the token (401): it is revoked, expired or mistyped. "
            "Reconnect GitHub with a new token."
        )
    if code == 403:
        return (
            f"GitHub refused access to {target} (403): the token needs "
            "'Contents: Read and write' on this repository, or the API rate limit was reached."
        )
    if code == 404:
        return (
            f"GitHub returned 404 for {target}: the file does not exist at that path, "
            "or the token cannot see this repository."
        )
    if code in (409, 422):
        return (
            f"GitHub rejected the commit ({code}): the file changed on GitHub after "
            "PipeGuard read it. Run the investigation again."
        )
    if code:
        return f"GitHub returned HTTP {code} for {target}."
    return f"Could not reach GitHub for {target}: {exc}"


def _is_auth_problem(reason: str) -> bool:
    return reason.startswith(("GitHub rejected the token", "GitHub refused access"))


def _try_fetch(owner: str, repo: str, token: str, path: str):
    """Return (content, None) on success or (None, reason) on failure."""
    try:
        resp = httpx.get(
            f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{path}",
            headers=_headers(token),
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or "content" not in data:
            return None, f"{path} is not a file in {owner}/{repo}."
        return base64.b64decode(data["content"]).decode("utf-8"), None
    except UnicodeDecodeError:
        return None, f"{path} is not a UTF-8 text file."
    except Exception as exc:
        return None, _explain_http_error(exc, owner, repo, path)


def _fetch_file_content(owner: str, repo: str, token: str, path: str) -> str | None:
    return _try_fetch(owner, repo, token, path)[0]


def _resolve_repo_path(path: str, owner: str, repo: str, token: str, trace: list):
    """Map a path quoted in a log or an LLM report to a path that exists in the repo.

    Jenkins logs and LLM reports quote paths such as
    /var/lib/jenkins/workspace/PipeGuard-Lab/app.py. GitHub only knows paths
    relative to the repository root, so drop leading folders one at a time and
    return (repo_relative_path, content) for the first one GitHub finds."""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    last_reason = ""
    for i in range(len(parts)):
        candidate = "/".join(parts[i:])
        content, reason = _try_fetch(owner, repo, token, candidate)
        if content is not None:
            return candidate, content
        last_reason = reason or ""
        if _is_auth_problem(last_reason):
            break  # a bad token cannot be fixed by trying other paths
    if last_reason:
        trace.append(last_reason)
    return None, None


def _parse_local_imports(source_code: str) -> list[str]:
    modules = []
    for line in source_code.splitlines():
        line = line.strip()
        m = re.match(r"from\s+([\w\.]+)\s+import\s+", line)
        if not m:
            m = re.match(r"import\s+([\w\.]+)", line)
        if m:
            module = m.group(1).split(".")[0]
            if module and module not in _IGNORED_MODULES:
                modules.append(module)
    return modules


def _find_test_filename_from_text(text: str) -> str | None:
    """Pull the first test-file path mentioned in a console log / traceback."""
    for path in _extract_paths_from_text(text or ""):
        if "test" in path.lower() and _is_plausible_source_file(path):
            return path.split("/")[-1]
    return None


def _pick_target_file(state: InvestigationState, token: str, owner: str, repo: str, trace=None) -> str | None:
    trace = trace if trace is not None else []
    can_verify = bool(token and owner and repo)
    evidence = state.get("evidence") or {}

    console_log = ""
    jenkins_evidence = evidence.get("jenkins") or evidence
    if isinstance(jenkins_evidence, dict):
        console_log = jenkins_evidence.get("console_log", "") or ""

    diagnosis_text = state.get("diagnosis_report", "") or ""

    candidates: dict[str, int] = {}
    test_paths: list[str] = []

    for source_text in (console_log, diagnosis_text):
        for path in _extract_paths_from_text(source_text):
            if not _is_plausible_source_file(path):
                continue
            # Judge the file name only: folders such as /tests/ or a workspace
            # name must not turn a source file into a "test" file.
            if "test" in path.split("/")[-1].lower():
                test_paths.append(path)
                continue
            candidates[path] = candidates.get(path, 0) + 1

    if candidates:
        if not can_verify:
            return max(candidates, key=candidates.get)
        # Most-mentioned first; use the first path that really exists in the repo.
        for path in sorted(candidates, key=candidates.get, reverse=True):
            resolved, _content = _resolve_repo_path(path, owner, repo, token, trace)
            if resolved:
                return resolved

    if test_paths and can_verify:
        test_path, test_content = _resolve_repo_path(test_paths[0], owner, repo, token, trace)
        if test_content:
            local_modules = _parse_local_imports(test_content)
            root_files = _list_repo_root_files(owner, repo, token)
            root_py_files = {
                f: f for f in root_files
                if _is_plausible_source_file(f) and "test" not in f.lower()
            }
            for module in local_modules:
                candidate = f"{module}.py"
                if candidate in root_py_files:
                    return root_py_files[candidate]
            if len(root_py_files) == 1:
                return next(iter(root_py_files.values()))

    if can_verify:
        root_files = _list_repo_root_files(owner, repo, token)
        root_py_files = [
            f for f in root_files
            if _is_plausible_source_file(f) and "test" not in f.lower()
        ]
        if len(root_py_files) == 1:
            return root_py_files[0]

    github = evidence.get("github") or {}
    files = github.get("files") or []
    for file in files:
        filename = file.get("filename") if isinstance(file, dict) else None
        if filename and "test" not in filename.lower() and _is_plausible_source_file(filename):
            return filename

    return None


def build_fix_prompt(
    target_file: str,
    original_content: str,
    test_filename: str | None,
    test_content: str | None,
    diagnosis_report: str,
    remediation_report: str,
) -> str:
    """The Fix agent's prompt. training/eval_fix.py reuses it, so the
    fix-success benchmark measures exactly what production would push."""
    test_file_block = ""
    overfit_rules = ""
    if test_content:
        test_file_block = f"""

FULL TEST FILE ({test_filename}) — this is the ground truth for correct behavior:
{test_content}
"""
        overfit_rules = """
- You are given the FULL test file above. Fix the implementation so that
  every test in it passes, not just the single failure quoted in the
  diagnosis/remediation reports above — do not overfit to one assertion.
- EXCEPTION: if a test's own comment states it is intentionally designed to
  fail (e.g. "intentionally failing", "control test", "expected to fail"),
  do NOT change the implementation to satisfy it. Leave that one test
  failing on purpose and do not mention changing it.
- Do not treat stale or incorrect documentation as ground truth over the
  tests. Only if a docstring or comment explicitly contradicts what the real
  (non-intentional) tests expect, correct that one docstring or comment.
"""

    return f"""You are a senior engineer fixing a broken Jenkins build.

FILE: {target_file}

CURRENT FILE CONTENT:
{original_content}
{test_file_block}
DIAGNOSIS REPORT:
{diagnosis_report}

REMEDIATION REPORT:
{remediation_report}

Rewrite the ENTIRE file with the bug fixed, based on the diagnosis and
remediation above. Make the smallest change that fixes the bug: every line
you do not need to change must stay exactly as it is, including docstrings,
comments, names and formatting. Do not refactor, reword or tidy anything.
{overfit_rules}
Return ONLY the complete corrected file content — no markdown fences,
no explanation, no commentary.
"""


def clean_fix_output(text: str) -> str:
    """Strip a markdown fence the model may wrap the file in."""
    new_content = text.strip()
    if new_content.startswith("```"):
        new_content = new_content.split("\n", 1)[1]
        if new_content.endswith("```"):
            new_content = new_content.rsplit("```", 1)[0]
    return new_content


def run_apply_fix(state: InvestigationState) -> InvestigationState:
    token = state.get("github_token") or ""
    owner = state.get("github_owner") or ""
    repo = state.get("github_repo") or ""

    if not (token and owner and repo):
        return {**state, "fix_result": {
            "applied": False,
            "message": "Missing GitHub token/owner/repo — cannot apply a real fix.",
        }}

    trace: list[str] = []
    target_file = _pick_target_file(state, token, owner, repo, trace)
    if not target_file:
        return {**state, "fix_result": {
            "applied": False,
            "message": (
                "Could not determine which file is responsible from the evidence. "
                + " ".join(dict.fromkeys(trace))
            ).strip(),
        }}

    headers = _headers(token)

    try:
        get_resp = httpx.get(
            f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{target_file}",
            headers=headers,
            timeout=15.0,
        )
        get_resp.raise_for_status()
        file_data = get_resp.json()
        original_content = base64.b64decode(file_data["content"]).decode("utf-8")
        blob_sha = file_data["sha"]
    except Exception as exc:
        return {**state, "fix_result": {
            "applied": False,
            "message": f"Could not fetch {target_file}. {_explain_http_error(exc, owner, repo, target_file)}",
        }}

    # Fetch the associated test file so the LLM sees ALL assertions, not just
    # the one quoted in the diagnosis report. This is what prevents overfitting.
    evidence = state.get("evidence") or {}
    jenkins_evidence = evidence.get("jenkins") or evidence
    console_log = jenkins_evidence.get("console_log", "") if isinstance(jenkins_evidence, dict) else ""
    test_filename = _find_test_filename_from_text(console_log)
    test_content = None
    if test_filename:
        test_content = _fetch_file_content(owner, repo, token, test_filename)

    llm = ChatGroq(
        model=settings.llm_model,
        api_key=settings.groq_api_key,
        temperature=0,
        max_tokens=1200,
        model_kwargs={"include_reasoning": False},
    )

    fix_prompt = build_fix_prompt(
        target_file, original_content, test_filename, test_content,
        state.get('diagnosis_report', ''), state.get('remediation_report', ''),
    )

    try:
        result = _invoke_with_retry(llm, fix_prompt)
        new_content = clean_fix_output(result.content)
    except Exception as exc:
        return {**state, "fix_result": {
            "applied": False,
            "message": f"LLM failed to generate a fix: {exc}",
        }}

    # Independent judge gate: a different model (Gemini) reviews the diff
    # against the original file, the diagnosis and the test file BEFORE any
    # GitHub write. judge_fix() fails closed, so a judge outage or an
    # unparseable response rejects the fix just like an explicit "fail" —
    # nothing is pushed unless the verdict is exactly "pass".
    judge_result = judge_fix(
        target_file=target_file,
        original_content=original_content,
        new_content=new_content,
        test_filename=test_filename,
        test_content=test_content,
        diagnosis_report=state.get("diagnosis_report", ""),
        remediation_report=state.get("remediation_report", ""),
    )
    if judge_result["verdict"] != "pass":
        return {**state, "fix_result": {
            "applied": False,
            "message": f"Judge rejected the generated fix before any GitHub push: {judge_result['reasoning']}",
            "judge_result": judge_result,
        }}

    commit_message = (
        f"fix: PipeGuard automated remediation for "
        f"{state['job_name']} build #{state['build_number']}"
    )

    try:
        put_resp = httpx.put(
            f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{target_file}",
            headers=headers,
            json={
                "message": commit_message,
                "content": base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
                "sha": blob_sha,
            },
            timeout=15.0,
        )
        put_resp.raise_for_status()
        commit_data = put_resp.json()
    except Exception as exc:
        return {**state, "fix_result": {
            "applied": False,
            "message": f"Generated a fix but could not push it. {_explain_http_error(exc, owner, repo, target_file)}",
        }}

    return {**state, "fix_result": {
        "applied": True,
        "file": target_file,
        "commit_sha": (commit_data.get("commit") or {}).get("sha"),
        "commit_url": (commit_data.get("commit") or {}).get("html_url"),
        "message": (
            f"Pushed a fix to {target_file}. Rebuild "
            f"{state['job_name']} in Jenkins to see it pass."
        ),
        "judge_result": judge_result,
    }}


def _invoke_with_retry(llm, prompt_text: str, max_attempts: int = 3):
    """Call the LLM, retrying with backoff on Groq 429 rate-limit errors."""
    import time

    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return llm.invoke([("human", prompt_text)])
        except Exception as exc:
            last_exc = exc
            message = str(exc)
            is_rate_limit = "429" in message or "rate_limit_exceeded" in message
            if not is_rate_limit or attempt == max_attempts:
                raise
            wait_match = re.search(r"try again in ([\d.]+)s", message)
            wait_seconds = float(wait_match.group(1)) + 1.0 if wait_match else 10.0
            time.sleep(wait_seconds)
    raise last_exc

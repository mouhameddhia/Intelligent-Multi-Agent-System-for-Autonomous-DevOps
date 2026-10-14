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


def _fetch_file_content(owner: str, repo: str, token: str, path: str) -> str | None:
    try:
        resp = httpx.get(
            f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{path}",
            headers=_headers(token),
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return base64.b64decode(data["content"]).decode("utf-8")
    except Exception:
        return None


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


def _pick_target_file(state: InvestigationState, token: str, owner: str, repo: str) -> str | None:
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
            if "test" in path.lower():
                test_paths.append(path)
                continue
            candidates[path] = candidates.get(path, 0) + 1

    if candidates:
        return max(candidates, key=candidates.get)

    if test_paths and token and owner and repo:
        test_filename = test_paths[0].split("/")[-1]
        test_content = _fetch_file_content(owner, repo, token, test_filename)
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

    if token and owner and repo:
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


def run_apply_fix(state: InvestigationState) -> InvestigationState:
    token = state.get("github_token") or ""
    owner = state.get("github_owner") or ""
    repo = state.get("github_repo") or ""

    if not (token and owner and repo):
        return {**state, "fix_result": {
            "applied": False,
            "message": "Missing GitHub token/owner/repo — cannot apply a real fix.",
        }}

    target_file = _pick_target_file(state, token, owner, repo)
    if not target_file:
        return {**state, "fix_result": {
            "applied": False,
            "message": "Could not determine which file is responsible from the evidence.",
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
            "message": f"Could not fetch {target_file} from GitHub: {exc}",
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
- If the file's existing docstring or comments describe behavior that
  contradicts what the real (non-intentional) tests expect, correct the
  docstring/comments to match the fixed, correct behavior. Do not treat
  stale or incorrect documentation as ground truth over the tests.
"""

    fix_prompt = f"""You are a senior engineer fixing a broken Jenkins build.

FILE: {target_file}

CURRENT FILE CONTENT:
{original_content}
{test_file_block}
DIAGNOSIS REPORT:
{state.get('diagnosis_report', '')}

REMEDIATION REPORT:
{state.get('remediation_report', '')}

Rewrite the ENTIRE file with the bug fixed, based on the diagnosis and
remediation above. Preserve everything unrelated to the bug.
{overfit_rules}
Return ONLY the complete corrected file content — no markdown fences,
no explanation, no commentary.
"""

    try:
        result = _invoke_with_retry(llm, fix_prompt)
        new_content = result.content.strip()
        if new_content.startswith("```"):
            new_content = new_content.split("\n", 1)[1]
            if new_content.endswith("```"):
                new_content = new_content.rsplit("```", 1)[0]
    except Exception as exc:
        return {**state, "fix_result": {
            "applied": False,
            "message": f"LLM failed to generate a fix: {exc}",
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
            "message": f"Generated a fix but could not push it to GitHub: {exc}",
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

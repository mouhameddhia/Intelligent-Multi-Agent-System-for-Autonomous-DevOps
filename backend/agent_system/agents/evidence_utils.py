"""Bound provider evidence before sending it to an LLM, and render the
deterministic parts of the Monitoring report straight from evidence."""
from datetime import datetime, timezone


def build_summary_markdown(evidence):
    """The Monitoring 'Build summary' section as a Field | Value table.

    Built from Jenkins metadata in Python rather than by the LLM, so the table
    is identical on every run and never costs output tokens.
    """
    jenkins = (evidence or {}).get("jenkins") or {}

    def text(key):
        value = jenkins.get(key)
        return str(value) if value not in (None, "") else "Not available"

    timestamp = jenkins.get("timestamp")
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        stamp = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
        timestamp_text = stamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        timestamp_text = "Not available"

    duration = jenkins.get("duration_ms")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        duration_text = f"{int(duration):,} ms (\u2248 {duration / 1000:.1f} s)"
    else:
        duration_text = "Not available"

    rows = [
        ("Job name", text("job_name")),
        ("Build number", text("build_number")),
        ("Result", text("result")),
        ("Timestamp (UTC)", timestamp_text),
        ("Duration", duration_text),
        ("URL", text("url")),
    ]
    lines = ["**Build summary**", "", "| Field | Value |", "|---|---|"]
    lines += [f"| {field} | {str(value).replace('|', chr(92) + '|')} |" for field, value in rows]
    return "\n".join(lines)

def _is_console_log(key):
    key = key.lower().replace("-", "_").replace(" ", "_")
    return (
        ("console" in key and "log" in key)
        or key in {"consoletext", "console_output", "build_log", "log_text"}
    )


def compact_evidence(
    evidence,
    include_console_log=True,
    *,
    patch_limit=1500,
    file_list_limit=8,
    console_log_limit=4000,
):
    """Return a bounded copy; never mutate the original evidence."""
    def compact(value, key=""):
        if isinstance(value, dict):
            result = {}
            for child_key, child_value in value.items():
                if not include_console_log and _is_console_log(str(child_key)):
                    continue
                result[child_key] = compact(child_value, str(child_key))
            return result

        if isinstance(value, list):
            # Keep representative evidence instead of large provider payloads.
            limit = file_list_limit if "file" in key.lower() else 5
            return [compact(item, key) for item in value[:limit]]

        if isinstance(value, str):
            normalized_key = key.lower().replace("-", "_").replace(" ", "_")
            if normalized_key in {"patch", "diff"}:
                limit = patch_limit
            elif _is_console_log(key):
                limit = console_log_limit
            else:
                limit = 1500

            if len(value) > limit:
                return value[:limit] + "\\n[truncated for LLM context]"
            return value

        return value

    return compact(evidence)

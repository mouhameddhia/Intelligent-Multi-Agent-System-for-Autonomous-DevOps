"""Bound provider evidence before sending it to an LLM."""


def _is_console_log(key):
    key = key.lower().replace("-", "_").replace(" ", "_")
    return (
        ("console" in key and "log" in key)
        or key in {"consoletext", "console_output", "build_log", "log_text"}
    )


def compact_evidence(evidence, include_console_log=True):
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
            limit = 8 if "file" in key.lower() else 5
            return [compact(item, key) for item in value[:limit]]

        if isinstance(value, str):
            if _is_console_log(key):
                limit = 4000
            else:
                limit = 1500

            if len(value) > limit:
                return value[:limit] + "\\n[truncated for LLM context]"
            return value

        return value

    return compact(evidence)

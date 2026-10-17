
"""Parse the explicit confidence marker emitted by each investigation agent."""
import logging
import re
import time


# Match one line at a time. Avoid \s because it also matches newlines,
# and avoid nested repetitions that can cause excessive regex backtracking.
_MARKER = re.compile(
    r"^[ \t]*[*_`#>\-]*[ \t]*"
    r"AGENT_CONFIDENCE[ \t]*[:=][ \t]*"
    r"(100|[1-9]?\d)[ \t]*%?[ \t]*[*_`.,;]*[ \t]*$",
    re.IGNORECASE,
)


def extract_agent_confidence(report: str) -> tuple[str, int | None]:
    started = time.perf_counter()
    text = report or ""



    try:
        cleaned_lines = []
        confidence = None
        marker_count = 0

        for line_number, line in enumerate(text.splitlines(keepends=True), start=1):
            match = _MARKER.fullmatch(line.rstrip("\r\n"))

            if match:
                confidence = int(match.group(1))
                marker_count += 1
            else:
                cleaned_lines.append(line)

        cleaned = "".join(cleaned_lines).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

        

        return cleaned, confidence

    except Exception:
        raise

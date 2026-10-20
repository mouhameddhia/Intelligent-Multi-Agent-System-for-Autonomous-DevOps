from urllib.parse import urlsplit

import httpx


class LokiConnectionError(Exception):
    """Raised when PipeGuard cannot communicate with Loki."""


class LokiConnector:
    def __init__(self, base_url: str):
        parsed = urlsplit(base_url.strip().rstrip("/"))

        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(
                "Enter a valid Loki URL starting with http:// or https://."
            )

        if parsed.username or parsed.password:
            raise ValueError(
                "Do not put credentials inside the Loki URL."
            )

        if parsed.query or parsed.fragment:
            raise ValueError(
                "The Loki URL must not contain a query or fragment."
            )

        self.base_url = base_url.strip().rstrip("/")
        self.timeout = 10.0

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"

        try:
            response = httpx.get(
                url,
                params=params,
                timeout=self.timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise LokiConnectionError(
                "Loki did not respond within 10 seconds."
            ) from exc
        except httpx.RequestError as exc:
            raise LokiConnectionError(
                "Could not reach Loki. Check its URL and network access."
            ) from exc

        if response.status_code in (401, 403):
            raise LokiConnectionError(
                "Loki rejected the request. Check its auth configuration."
            )

        if response.is_redirect:
            raise LokiConnectionError(
                "Loki redirected the request. Check the configured base URL."
            )

        if response.status_code >= 400:
            raise LokiConnectionError(
                f"Loki returned HTTP {response.status_code}."
            )

        return response

    def _get_json(self, path: str, params: dict | None = None) -> dict:
        response = self._get(path, params)

        try:
            data = response.json()
        except ValueError as exc:
            raise LokiConnectionError(
                "Loki returned an unexpected response instead of JSON."
            ) from exc

        if not isinstance(data, dict):
            raise LokiConnectionError(
                "Loki returned an unexpected JSON structure."
            )

        return data

    def test_connection(self) -> dict:
        ready = self._get("/ready")
        if "ready" not in ready.text.lower():
            raise LokiConnectionError(
                "Loki responded, but its readiness check was unexpected."
            )

        labels = self.get_labels()
        return {
            "connected": True,
            "label_count": len(labels),
        }

    def get_labels(self) -> list[str]:
        data = self._get_json("/loki/api/v1/labels")
        labels = data.get("data", [])
        if not isinstance(labels, list):
            raise LokiConnectionError("Loki did not return a valid label list.")
        return [label for label in labels if isinstance(label, str)]

    def query_range(
        self,
        expression: str,
        start: float,
        end: float,
        limit: int = 100,
    ) -> list[dict]:
        """start/end are unix seconds (same convention as the Prometheus
        connector); Loki's own API wants nanoseconds."""
        if not expression.strip():
            raise ValueError("Enter a LogQL query.")
        if start >= end:
            raise ValueError("The log-query start time must be before the end time.")

        data = self._get_json(
            "/loki/api/v1/query_range",
            {
                "query": expression.strip(),
                "start": int(start * 1_000_000_000),
                "end": int(end * 1_000_000_000),
                "limit": min(max(limit, 1), 500),
            },
        )

        result = (data.get("data") or {}).get("result", [])
        if not isinstance(result, list):
            raise LokiConnectionError("Loki returned an invalid query result.")

        lines = []
        for stream in result:
            if not isinstance(stream, dict):
                continue
            labels = stream.get("stream") or {}
            for value in stream.get("values") or []:
                if not isinstance(value, list) or len(value) != 2:
                    continue
                timestamp_ns, line = value
                try:
                    timestamp_ms = int(timestamp_ns) // 1_000_000
                except (TypeError, ValueError):
                    timestamp_ms = None
                lines.append({
                    "labels": labels,
                    "timestamp_ms": timestamp_ms,
                    "line": line,
                })

        lines.sort(key=lambda item: item["timestamp_ms"] or 0)
        return lines[:limit]

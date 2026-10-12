from urllib.parse import urlsplit

import httpx


class PrometheusConnectionError(Exception):
    """Raised when PipeGuard cannot communicate with Prometheus."""


class PrometheusConnector:
    def __init__(self, base_url: str):
        parsed = urlsplit(base_url.strip().rstrip("/"))

        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(
                "Enter a valid Prometheus URL starting with http:// or https://."
            )

        if parsed.username or parsed.password:
            raise ValueError(
                "Do not put credentials inside the Prometheus URL."
            )

        if parsed.query or parsed.fragment:
            raise ValueError(
                "The Prometheus URL must not contain a query or fragment."
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
            raise PrometheusConnectionError(
                "Prometheus did not respond within 10 seconds."
            ) from exc
        except httpx.RequestError as exc:
            raise PrometheusConnectionError(
                "Could not reach Prometheus. Check its URL and network access."
            ) from exc

        if response.is_redirect:
            raise PrometheusConnectionError(
                "Prometheus redirected the request. Check the configured base URL."
            )

        if response.status_code >= 400:
            raise PrometheusConnectionError(
                f"Prometheus returned HTTP {response.status_code}."
            )

        return response

    def _get_json(self, path: str, params: dict | None = None) -> dict:
        response = self._get(path, params)

        try:
            data = response.json()
        except ValueError as exc:
            raise PrometheusConnectionError(
                "Prometheus returned an invalid JSON response."
            ) from exc

        if not isinstance(data, dict):
            raise PrometheusConnectionError(
                "Prometheus returned an unexpected response structure."
            )

        return data

    def _get_api_data(self, path: str, params: dict | None = None) -> dict:
        payload = self._get_json(path, params)

        if payload.get("status") != "success":
            error = payload.get("error") or "Unknown Prometheus API error."
            raise PrometheusConnectionError(
                f"Prometheus API query failed: {error}"
            )

        data = payload.get("data")
        if not isinstance(data, dict):
            raise PrometheusConnectionError(
                "Prometheus API returned an unexpected data structure."
            )

        return data

    def test_connection(self) -> dict:
        ready = self._get("/-/ready")

        if "ready" not in ready.text.lower():
            raise PrometheusConnectionError(
                "Prometheus responded, but its readiness check was unexpected."
            )

        build_info = self._get_api_data("/api/v1/status/buildinfo")
        targets_data = self._get_api_data(
            "/api/v1/targets",
            {"state": "active"},
        )
        up_data = self._get_api_data(
            "/api/v1/query",
            {"query": "up"},
        )

        targets = targets_data.get("activeTargets", [])
        if not isinstance(targets, list):
            targets = []

        healthy = sum(
            1 for target in targets
            if isinstance(target, dict) and target.get("health") == "up"
        )
        unhealthy = sum(
            1 for target in targets
            if isinstance(target, dict) and target.get("health") == "down"
        )

        up_results = up_data.get("result", [])
        if not isinstance(up_results, list):
            up_results = []

        return {
            "connected": True,
            "version": build_info.get("version", "unknown"),
            "targets_total": len(targets),
            "targets_healthy": healthy,
            "targets_unhealthy": unhealthy,
            "up_metric_series": len(up_results),
        }

    def get_targets(self) -> list[dict]:
        data = self._get_api_data(
            "/api/v1/targets",
            {"state": "active"},
        )

        targets = data.get("activeTargets", [])
        if not isinstance(targets, list):
            raise PrometheusConnectionError(
                "Prometheus did not return a valid targets list."
            )

        results = []

        for target in targets:
            if not isinstance(target, dict):
                continue

            labels = target.get("labels") or {}

            results.append({
                "job": labels.get("job", "unknown"),
                "instance": labels.get("instance", "unknown"),
                "health": target.get("health", "unknown"),
                "last_error": target.get("lastError", ""),
                "last_scrape": target.get("lastScrape"),
                "scrape_url": target.get("scrapeUrl"),
            })

        return results

    def query(self, expression: str) -> list[dict]:
        if not expression.strip():
            raise ValueError("Enter a PromQL query.")

        data = self._get_api_data(
            "/api/v1/query",
            {"query": expression.strip()},
        )

        results = data.get("result", [])
        if not isinstance(results, list):
            raise PrometheusConnectionError(
                "Prometheus returned an invalid query result."
            )

        return results

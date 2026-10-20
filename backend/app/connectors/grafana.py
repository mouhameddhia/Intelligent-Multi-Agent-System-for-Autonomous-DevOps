from urllib.parse import urlsplit

import httpx


class GrafanaConnectionError(Exception):
    """Raised when PipeGuard cannot communicate with Grafana."""


class GrafanaConnector:
    def __init__(self, base_url: str, api_token: str):
        parsed = urlsplit(base_url.strip().rstrip("/"))

        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(
                "Enter a valid Grafana URL starting with http:// or https://."
            )

        if parsed.username or parsed.password:
            raise ValueError(
                "Do not put credentials inside the Grafana URL."
            )

        if parsed.query or parsed.fragment:
            raise ValueError(
                "The Grafana URL must not contain a query or fragment."
            )

        api_token = api_token.strip()
        if not api_token:
            raise ValueError("A Grafana service account token is required.")

        self.base_url = base_url.strip().rstrip("/")
        self.headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_token}",
        }
        self.timeout = 10.0

    def _get(self, path: str, params: dict | None = None, auth: bool = True) -> httpx.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"

        try:
            response = httpx.get(
                url,
                headers=self.headers if auth else None,
                params=params,
                timeout=self.timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise GrafanaConnectionError(
                "Grafana did not respond within 10 seconds."
            ) from exc
        except httpx.RequestError as exc:
            raise GrafanaConnectionError(
                "Could not reach Grafana. Check its URL and network access."
            ) from exc

        if response.status_code in (401, 403):
            raise GrafanaConnectionError(
                "Grafana rejected the request. Check the service account token "
                "and its permissions."
            )

        if response.is_redirect:
            raise GrafanaConnectionError(
                "Grafana redirected the request. Use the correct Grafana base URL."
            )

        if response.status_code >= 400:
            raise GrafanaConnectionError(
                f"Grafana returned HTTP {response.status_code}."
            )

        return response

    def _get_json(self, path: str, params: dict | None = None, auth: bool = True) -> dict:
        response = self._get(path, params, auth=auth)

        try:
            data = response.json()
        except ValueError as exc:
            raise GrafanaConnectionError(
                "Grafana returned an unexpected response instead of JSON."
            ) from exc

        if not isinstance(data, dict):
            raise GrafanaConnectionError(
                "Grafana returned an unexpected JSON structure."
            )

        return data

    def test_connection(self) -> dict:
        # /api/health needs no auth and confirms Grafana itself is reachable;
        # /api/org then confirms the token is valid and has at least Viewer access.
        health = self._get_json("/api/health", auth=False)
        org = self._get_json("/api/org")

        return {
            "connected": True,
            "version": health.get("version", "unknown"),
            "org_name": org.get("name"),
        }

    def get_dashboards(self, limit: int = 50) -> list[dict]:
        data = self._get(
            "/api/search",
            {"type": "dash-db", "limit": min(max(limit, 1), 100)},
        )

        try:
            items = data.json()
        except ValueError as exc:
            raise GrafanaConnectionError(
                "Grafana returned an unexpected response instead of JSON."
            ) from exc

        if not isinstance(items, list):
            raise GrafanaConnectionError(
                "Grafana did not return a valid dashboard list."
            )

        return [
            {
                "uid": item.get("uid"),
                "title": item.get("title"),
                "url": f"{self.base_url}{item.get('url')}" if item.get("url") else None,
                "folder_title": item.get("folderTitle"),
                "tags": item.get("tags", []),
            }
            for item in items
            if isinstance(item, dict)
        ]

    def get_annotations(self, start: float, end: float, limit: int = 100) -> list[dict]:
        """start/end are unix seconds (same convention as the Prometheus
        connector); Grafana's own API wants them in milliseconds."""
        if start >= end:
            raise ValueError("The annotations start time must be before the end time.")

        data = self._get(
            "/api/annotations",
            {
                "from": int(start * 1000),
                "to": int(end * 1000),
                "limit": min(max(limit, 1), 200),
            },
        )

        try:
            items = data.json()
        except ValueError as exc:
            raise GrafanaConnectionError(
                "Grafana returned an unexpected response instead of JSON."
            ) from exc

        if not isinstance(items, list):
            raise GrafanaConnectionError(
                "Grafana did not return a valid annotations list."
            )

        return [
            {
                "id": item.get("id"),
                "time": item.get("time"),
                "time_end": item.get("timeEnd"),
                "text": item.get("text"),
                "tags": item.get("tags", []),
                "dashboard_uid": item.get("dashboardUID"),
                "panel_id": item.get("panelId"),
            }
            for item in items
            if isinstance(item, dict)
        ]

from urllib.parse import urlsplit

import httpx


class GrafanaConnectionError(Exception):
    """Raised when PipeGuard cannot reach Grafana."""


class GrafanaConnector:
    def __init__(self, base_url: str):
        parsed = urlsplit((base_url or "").strip().rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Enter a valid Grafana URL starting with http:// or https://.")
        if parsed.username or parsed.password:
            raise ValueError("Do not put credentials inside the Grafana URL.")
        if parsed.query or parsed.fragment:
            raise ValueError("The Grafana URL must not contain a query or fragment.")
        self.base_url = base_url.strip().rstrip("/")

    def test_connection(self) -> dict:
        try:
            response = httpx.get(
                f"{self.base_url}/api/health",
                timeout=8,
                follow_redirects=False,
            )
        except httpx.RequestError as exc:
            raise GrafanaConnectionError(
                f"Could not reach Grafana at {self.base_url}. "
                "Use an address reachable from the PipeGuard backend; for the "
                "Vagrant setup, Grafana is exposed at http://192.168.56.10:3000. "
                f"Connection detail: {exc}"
            ) from exc
        if response.is_redirect:
            raise GrafanaConnectionError("Grafana redirected the request. Check the configured base URL.")
        if response.status_code >= 400:
            raise GrafanaConnectionError(f"Grafana returned HTTP {response.status_code}.")
        try:
            data = response.json()
        except ValueError as exc:
            raise GrafanaConnectionError("Grafana returned an invalid health response.") from exc
        if not isinstance(data, dict) or data.get("database") != "ok":
            raise GrafanaConnectionError("Grafana responded, but its database is not healthy.")
        return {"connected": True, "version": data.get("version", "unknown")}

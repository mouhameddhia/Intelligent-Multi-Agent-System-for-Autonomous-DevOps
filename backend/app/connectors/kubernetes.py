from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx


class KubernetesConnectionError(Exception):
    """Raised when PipeGuard cannot communicate with the Kubernetes API server."""


class KubernetesConnector:
    def __init__(self, base_url: str, token: str, verify_tls: bool = True):
        parsed = urlsplit(base_url.strip().rstrip("/"))

        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(
                "Enter a valid Kubernetes API server URL starting with http:// or https://."
            )

        if parsed.username or parsed.password:
            raise ValueError(
                "Do not put credentials inside the Kubernetes URL."
            )

        if parsed.query or parsed.fragment:
            raise ValueError(
                "The Kubernetes URL must not contain a query or fragment."
            )

        token = token.strip()
        if not token:
            raise ValueError("A Kubernetes service account token is required.")

        self.base_url = base_url.strip().rstrip("/")
        self.headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        self.timeout = 10.0
        self.verify_tls = verify_tls

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"

        try:
            response = httpx.get(
                url,
                headers=self.headers,
                params=params,
                timeout=self.timeout,
                follow_redirects=False,
                verify=self.verify_tls,
            )
        except httpx.TimeoutException as exc:
            raise KubernetesConnectionError(
                "The Kubernetes API server did not respond within 10 seconds."
            ) from exc
        except httpx.ConnectError as exc:
            message = str(exc)
            if "certificate verify failed" in message.lower():
                raise KubernetesConnectionError(
                    "TLS certificate verification failed. If this is a "
                    "self-signed cluster certificate, disable TLS verification "
                    "for this connection."
                ) from exc
            raise KubernetesConnectionError(
                "Could not reach the Kubernetes API server. Check its URL and network access."
            ) from exc
        except httpx.RequestError as exc:
            raise KubernetesConnectionError(
                "Could not reach the Kubernetes API server. Check its URL and network access."
            ) from exc

        if response.status_code in (401, 403):
            raise KubernetesConnectionError(
                "The Kubernetes API server rejected the request. Check the "
                "service account token and its RBAC permissions."
            )

        if response.is_redirect:
            raise KubernetesConnectionError(
                "The Kubernetes API server redirected the request. Check the configured base URL."
            )

        if response.status_code >= 400:
            raise KubernetesConnectionError(
                f"The Kubernetes API server returned HTTP {response.status_code}."
            )

        return response

    def _get_json(self, path: str, params: dict | None = None) -> dict:
        response = self._get(path, params)

        try:
            data = response.json()
        except ValueError as exc:
            raise KubernetesConnectionError(
                "The Kubernetes API server returned an unexpected response instead of JSON."
            ) from exc

        if not isinstance(data, dict):
            raise KubernetesConnectionError(
                "The Kubernetes API server returned an unexpected JSON structure."
            )

        return data

    def test_connection(self) -> dict:
        version = self._get_json("/version")
        namespaces = self.get_namespaces()

        return {
            "connected": True,
            "git_version": version.get("gitVersion", "unknown"),
            "namespace_count": len(namespaces),
        }

    def get_namespaces(self) -> list[dict]:
        data = self._get_json("/api/v1/namespaces")

        items = data.get("items", [])
        if not isinstance(items, list):
            raise KubernetesConnectionError(
                "The Kubernetes API server did not return a valid namespace list."
            )

        return [
            {
                "name": (item.get("metadata") or {}).get("name"),
                "phase": (item.get("status") or {}).get("phase"),
            }
            for item in items
            if isinstance(item, dict)
        ]

    @staticmethod
    def _parse_timestamp(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    def get_events(
        self,
        namespace: str,
        start: float,
        end: float,
        limit: int = 50,
    ) -> list[dict]:
        """start/end are unix seconds. Events are filtered client-side on
        lastTimestamp, since the core/v1 events API has no server-side time
        range filter."""
        if not namespace.strip():
            raise ValueError("A Kubernetes namespace is required.")
        if start >= end:
            raise ValueError("The events start time must be before the end time.")

        data = self._get_json(f"/api/v1/namespaces/{namespace.strip()}/events")

        items = data.get("items", [])
        if not isinstance(items, list):
            raise KubernetesConnectionError(
                "The Kubernetes API server did not return a valid events list."
            )

        events = []
        for item in items:
            if not isinstance(item, dict):
                continue

            timestamp = self._parse_timestamp(
                item.get("lastTimestamp") or item.get("eventTime")
            )
            if timestamp is None or not (start <= timestamp <= end):
                continue

            involved = item.get("involvedObject") or {}
            events.append({
                "time": timestamp,
                "type": item.get("type"),
                "reason": item.get("reason"),
                "message": item.get("message"),
                "involved_kind": involved.get("kind"),
                "involved_name": involved.get("name"),
                "count": item.get("count", 1),
            })

        events.sort(key=lambda event: event["time"], reverse=True)
        return events[:limit]

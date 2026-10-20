"""Docker Engine API connector. Unlike every other connector in this app,
Docker's API has no bearer-token auth: access is gated by Unix-socket file
permissions (the normal case when PipeGuard runs on the Docker host) or by
mutual-TLS client certificates over TCP (not implemented here — out of scope
for this prototype). So this connector takes a host string, not a token:
  unix:///var/run/docker.sock   (local socket, the common case)
  tcp://host:2375 or http://host:2375   (remote daemon, insecure by default)
"""
import json

import httpx


class DockerConnectionError(Exception):
    """Raised when PipeGuard cannot communicate with the Docker Engine API."""


class DockerConnector:
    def __init__(self, host: str):
        host = host.strip()
        if not host:
            raise ValueError("A Docker host is required.")

        self.timeout = 10.0

        if host.startswith("unix://"):
            socket_path = host[len("unix://"):]
            if not socket_path:
                raise ValueError("A Unix socket path is required after unix://.")
            self._client = httpx.Client(
                transport=httpx.HTTPTransport(uds=socket_path),
                timeout=self.timeout,
            )
            # The host in this URL is ignored by the uds transport, but httpx
            # still needs a well-formed URL to build requests against.
            self.base_url = "http://docker"
        elif host.startswith(("tcp://", "http://", "https://")):
            scheme_fixed = host.replace("tcp://", "http://", 1)
            self._client = httpx.Client(timeout=self.timeout)
            self.base_url = scheme_fixed.rstrip("/")
        else:
            raise ValueError(
                "Enter a Docker host starting with unix://, tcp://, http:// or https://."
            )

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"

        try:
            response = self._client.get(url, params=params, follow_redirects=False)
        except httpx.TimeoutException as exc:
            raise DockerConnectionError(
                "The Docker Engine API did not respond within 10 seconds."
            ) from exc
        except httpx.RequestError as exc:
            raise DockerConnectionError(
                "Could not reach the Docker Engine API. Check the host and that the daemon is running."
            ) from exc

        if response.status_code in (401, 403):
            raise DockerConnectionError(
                "The Docker Engine API rejected the request. Check socket "
                "permissions or TLS client certificate configuration."
            )

        if response.is_redirect:
            raise DockerConnectionError(
                "The Docker Engine API redirected the request. Check the configured host."
            )

        if response.status_code >= 400:
            raise DockerConnectionError(
                f"The Docker Engine API returned HTTP {response.status_code}."
            )

        return response

    def _get_json(self, path: str, params: dict | None = None):
        response = self._get(path, params)
        try:
            return response.json()
        except ValueError as exc:
            raise DockerConnectionError(
                "The Docker Engine API returned an unexpected response instead of JSON."
            ) from exc

    def test_connection(self) -> dict:
        data = self._get_json("/version")
        if not isinstance(data, dict):
            raise DockerConnectionError("The Docker Engine API returned an unexpected response.")

        containers = self.get_containers()
        return {
            "connected": True,
            "version": data.get("Version", "unknown"),
            "api_version": data.get("ApiVersion", "unknown"),
            "os": data.get("Os", "unknown"),
            "container_count": len(containers),
        }

    def get_containers(self) -> list[dict]:
        data = self._get_json("/containers/json", {"all": "true"})
        if not isinstance(data, list):
            raise DockerConnectionError("The Docker Engine API did not return a valid container list.")

        return [
            {
                "id": (item.get("Id") or "")[:12],
                "names": [name.lstrip("/") for name in (item.get("Names") or [])],
                "image": item.get("Image"),
                "state": item.get("State"),
                "status": item.get("Status"),
            }
            for item in data
            if isinstance(item, dict)
        ]

    def get_events(self, start: float, end: float, limit: int = 50) -> list[dict]:
        """start/end are unix seconds. When both `since` and `until` are
        given, the Docker Engine API returns the bounded backlog of past
        events as a sequence of concatenated JSON objects (not a JSON array)
        and then closes the connection — not a live stream."""
        if start >= end:
            raise ValueError("The events start time must be before the end time.")

        response = self._get("/events", {"since": int(start), "until": int(end)})

        decoder = json.JSONDecoder()
        text = response.text
        events = []
        idx = 0
        while idx < len(text):
            remainder = text[idx:].lstrip()
            if not remainder:
                break
            skipped = len(text[idx:]) - len(remainder)
            try:
                obj, consumed = decoder.raw_decode(remainder)
            except ValueError:
                break
            idx += skipped + consumed
            if isinstance(obj, dict):
                events.append(obj)

        results = [
            {
                "time": event.get("time"),
                "type": event.get("Type"),
                "action": event.get("Action"),
                "container_name": ((event.get("Actor") or {}).get("Attributes") or {}).get("name"),
                "image": ((event.get("Actor") or {}).get("Attributes") or {}).get("image"),
            }
            for event in events
        ]
        results.sort(key=lambda event: event["time"] or 0, reverse=True)
        return results[:limit]

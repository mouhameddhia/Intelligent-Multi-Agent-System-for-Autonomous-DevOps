from urllib.parse import urlsplit

import httpx


class SonarQubeConnectionError(Exception):
    """Raised when PipeGuard cannot communicate with SonarQube."""


class SonarQubeConnector:
    def __init__(self, base_url: str, token: str):
        parsed = urlsplit(base_url.strip().rstrip("/"))

        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(
                "Enter a valid SonarQube URL starting with http:// or https://."
            )

        if parsed.username or parsed.password:
            raise ValueError(
                "Do not put credentials inside the SonarQube URL."
            )

        if parsed.query or parsed.fragment:
            raise ValueError(
                "The SonarQube URL must not contain a query or fragment."
            )

        token = token.strip()
        if not token:
            raise ValueError("A SonarQube user token is required.")

        self.base_url = base_url.strip().rstrip("/")
        self.headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
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
            raise SonarQubeConnectionError(
                "SonarQube did not respond within 10 seconds."
            ) from exc
        except httpx.RequestError as exc:
            raise SonarQubeConnectionError(
                "Could not reach SonarQube. Check its URL and network access."
            ) from exc

        if response.status_code in (401, 403):
            raise SonarQubeConnectionError(
                "SonarQube rejected the request. Check the user token and its permissions."
            )

        if response.is_redirect:
            raise SonarQubeConnectionError(
                "SonarQube redirected the request. Check the configured base URL."
            )

        if response.status_code >= 400:
            raise SonarQubeConnectionError(
                f"SonarQube returned HTTP {response.status_code}."
            )

        return response

    def _get_json(self, path: str, params: dict | None = None, auth: bool = True) -> dict:
        response = self._get(path, params, auth=auth)

        try:
            data = response.json()
        except ValueError as exc:
            raise SonarQubeConnectionError(
                "SonarQube returned an unexpected response instead of JSON."
            ) from exc

        if not isinstance(data, dict):
            raise SonarQubeConnectionError(
                "SonarQube returned an unexpected JSON structure."
            )

        return data

    def test_connection(self) -> dict:
        # /api/system/status needs no auth and confirms the server itself is
        # reachable; /api/authentication/validate then confirms the token works.
        status = self._get_json("/api/system/status", auth=False)
        validation = self._get_json("/api/authentication/validate")

        if not validation.get("valid"):
            raise SonarQubeConnectionError(
                "SonarQube rejected the token (validation failed)."
            )

        return {
            "connected": True,
            "version": status.get("version", "unknown"),
            "server_status": status.get("status", "unknown"),
        }

    def get_projects(self, limit: int = 100) -> list[dict]:
        data = self._get_json(
            "/api/components/search",
            {"qualifiers": "TRK", "ps": min(max(limit, 1), 500)},
        )

        components = data.get("components", [])
        if not isinstance(components, list):
            raise SonarQubeConnectionError(
                "SonarQube did not return a valid project list."
            )

        return [
            {
                "key": item.get("key"),
                "name": item.get("name"),
            }
            for item in components
            if isinstance(item, dict)
        ]

    def get_issues(
        self,
        project_key: str,
        file_path: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        if not project_key.strip():
            raise ValueError("A SonarQube project key is required.")

        params = {
            "componentKeys": project_key.strip(),
            "ps": min(max(limit, 1), 100),
            "s": "SEVERITY",
            "asc": "false",
        }
        if file_path:
            params["files"] = file_path.strip()

        data = self._get_json("/api/issues/search", params)

        issues = data.get("issues", [])
        if not isinstance(issues, list):
            raise SonarQubeConnectionError(
                "SonarQube did not return a valid issues list."
            )

        return [
            {
                "key": item.get("key"),
                "rule": item.get("rule"),
                "severity": item.get("severity"),
                "type": item.get("type"),
                "message": item.get("message"),
                "component": item.get("component"),
                "line": item.get("line"),
                "status": item.get("status"),
            }
            for item in issues[:limit]
            if isinstance(item, dict)
        ]

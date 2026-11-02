from urllib.parse import urlsplit

import httpx


class SonarQubeConnectionError(Exception):
    """Raised when PipeGuard cannot communicate with SonarQube."""


class SonarQubeConnector:
    METRICS = (
        "bugs",
        "vulnerabilities",
        "security_hotspots",
        "code_smells",
        "coverage",
        "duplicated_lines_density",
    )

    def __init__(self, base_url: str, token: str):
        base_url = base_url.strip().rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Enter a valid SonarQube URL starting with http:// or https://.")
        if parsed.username or parsed.password:
            raise ValueError("Do not put credentials inside the SonarQube URL.")
        if parsed.query or parsed.fragment:
            raise ValueError("The SonarQube URL must not contain a query or fragment.")
        token = token.strip()
        if not token:
            raise ValueError("A SonarQube user token is required.")

        self.base_url = base_url
        # Older SonarQube LTS builds may reject Bearer auth while accepting
        # the same user token through HTTP Basic with an empty password.
        self.headers = {"Accept": "application/json"}
        self.auth = (token, "")
        self.timeout = 10.0

    def _get_json(self, path: str, params: dict | None = None, *, authenticated: bool = True) -> dict:
        try:
            response = httpx.get(
                f"{self.base_url}/{path.lstrip('/')}",
                headers=self.headers,
                auth=self.auth if authenticated else None,
                params=params,
                timeout=self.timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise SonarQubeConnectionError("SonarQube did not respond within 10 seconds.") from exc
        except httpx.RequestError as exc:
            raise SonarQubeConnectionError(
                "Could not reach SonarQube. Check its URL and network access."
            ) from exc

        if response.status_code in (401, 403):
            raise SonarQubeConnectionError(
                "SonarQube denied access. Check the token and its Browse/Execute Analysis permissions."
            )
        if response.is_redirect:
            raise SonarQubeConnectionError(
                "SonarQube redirected the request. Check the configured base URL."
            )
        if response.status_code >= 400:
            raise SonarQubeConnectionError(f"SonarQube returned HTTP {response.status_code}.")
        try:
            data = response.json()
        except ValueError as exc:
            raise SonarQubeConnectionError(
                "SonarQube returned an unexpected response instead of JSON."
            ) from exc
        if not isinstance(data, dict):
            raise SonarQubeConnectionError("SonarQube returned an unexpected JSON structure.")
        return data

    def test_connection(self) -> dict:
        status = self._get_json("api/system/status", authenticated=False)
        validation = self._get_json("api/authentication/validate")
        if not validation.get("valid"):
            raise SonarQubeConnectionError("SonarQube rejected the token.")
        return {
            "connected": True,
            "version": status.get("version", "unknown"),
            "server_status": status.get("status", "unknown"),
        }

    def get_projects(self, limit: int = 100) -> list[dict]:
        data = self._get_json(
            "api/components/search",
            {"qualifiers": "TRK", "ps": min(max(limit, 1), 500)},
        )
        components = data.get("components", [])
        if not isinstance(components, list):
            raise SonarQubeConnectionError("SonarQube returned an invalid project list.")
        return [
            {"key": item.get("key"), "name": item.get("name")}
            for item in components
            if isinstance(item, dict)
        ]

    def get_latest_analysis(self, project_key: str) -> dict | None:
        data = self._get_json(
            "api/project_analyses/search",
            {"project": project_key, "ps": 1},
        )
        analyses = data.get("analyses", [])
        if not isinstance(analyses, list) or not analyses or not isinstance(analyses[0], dict):
            return None
        item = analyses[0]
        return {
            "key": item.get("key"),
            "date": item.get("date"),
            "revision": item.get("revision"),
        }

    def get_measures(self, project_key: str) -> dict:
        data = self._get_json(
            "api/measures/component",
            {"component": project_key, "metricKeys": ",".join(self.METRICS)},
        )
        component = data.get("component") or {}
        measures = component.get("measures", []) if isinstance(component, dict) else []
        if not isinstance(measures, list):
            raise SonarQubeConnectionError("SonarQube returned invalid project measures.")
        return {
            item.get("metric"): item.get("value")
            for item in measures
            if isinstance(item, dict) and item.get("metric")
        }

    def get_quality_gate(self, project_key: str) -> dict | None:
        data = self._get_json(
            "api/qualitygates/project_status",
            {"projectKey": project_key},
        )
        project_status = data.get("projectStatus")
        if not isinstance(project_status, dict):
            return None
        return {
            "status": project_status.get("status"),
            "conditions": [
                {
                    "metric": item.get("metricKey"),
                    "status": item.get("status"),
                    "actual": item.get("actualValue"),
                    "threshold": item.get("errorThreshold"),
                }
                for item in (project_status.get("conditions") or [])
                if isinstance(item, dict)
            ],
        }

    def get_issues(self, project_key: str, limit: int = 30) -> list[dict]:
        project_key = project_key.strip()
        if not project_key:
            raise ValueError("A SonarQube project key is required.")
        data = self._get_json(
            "api/issues/search",
            {
                "componentKeys": project_key,
                "ps": min(max(limit, 1), 100),
                "s": "SEVERITY",
                "asc": "false",
            },
        )
        issues = data.get("issues", [])
        if not isinstance(issues, list):
            raise SonarQubeConnectionError("SonarQube returned an invalid issues list.")
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

    def get_project_evidence(self, project_key: str, build_commit_sha: str = "", limit: int = 30) -> dict:
        analysis = self.get_latest_analysis(project_key)
        issues = self.get_issues(project_key, limit=limit)
        measures = self.get_measures(project_key)
        try:
            quality_gate = self.get_quality_gate(project_key)
            quality_gate_error = None
        except SonarQubeConnectionError as exc:
            quality_gate = None
            quality_gate_error = str(exc)
        revision = (analysis or {}).get("revision")
        commit_match = (
            revision.lower() == build_commit_sha.lower()
            if revision and build_commit_sha
            else None
        )
        return {
            "status": "success",
            "project_key": project_key,
            "analysis": analysis,
            "build_commit_sha": build_commit_sha or None,
            "analysis_matches_build": commit_match,
            "measures": measures,
            "quality_gate": quality_gate,
            "quality_gate_error": quality_gate_error,
            "issue_count": len(issues),
            "issues": issues,
        }

from urllib.parse import urlsplit

import httpx


class JenkinsConnectionError(Exception):
    """Raised when PipeGuard cannot communicate with Jenkins."""


class JenkinsConnector:
    def __init__(
        self,
        base_url: str,
        username: str,
        api_token: str,
    ):
        parsed = urlsplit(base_url.strip().rstrip("/"))

        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(
                "Enter a valid Jenkins URL starting with http:// or https://."
            )

        if parsed.username or parsed.password:
            raise ValueError(
                "Do not put credentials inside the Jenkins URL."
            )

        if parsed.query or parsed.fragment:
            raise ValueError(
                "The Jenkins URL must not contain a query or fragment."
            )

        self.base_url = base_url.strip().rstrip("/")
        self.auth = (username, api_token)
        self.timeout = 10.0

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"

        try:
            response = httpx.get(
                url,
                auth=self.auth,
                params=params,
                timeout=self.timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise JenkinsConnectionError(
                "Jenkins did not respond within 10 seconds."
            ) from exc
        except httpx.RequestError as exc:
            raise JenkinsConnectionError(
                "Could not reach Jenkins. Check its URL and network access."
            ) from exc

        if response.status_code in (401, 403):
            raise JenkinsConnectionError(
                "Jenkins rejected the request. Check the username, API token "
                "and account permissions."
            )

        if response.is_redirect:
            raise JenkinsConnectionError(
                "Jenkins redirected the request. Use the correct Jenkins "
                "base URL, including any context path."
            )

        if response.status_code >= 400:
            raise JenkinsConnectionError(
                f"Jenkins returned HTTP {response.status_code}."
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise JenkinsConnectionError(
                "Jenkins returned an unexpected response instead of JSON."
            ) from exc

        if not isinstance(data, dict):
            raise JenkinsConnectionError(
                "Jenkins returned an unexpected JSON structure."
            )

        return data

    def test_connection(self) -> dict:
        data = self._get(
            "api/json",
            {"tree": "mode,nodeName"},
        )

        return {
            "connected": True,
            "server_name": data.get("nodeName") or "Jenkins",
            "mode": data.get("mode"),
        }

    def get_jobs(self) -> list[dict]:
        data = self._get(
            "api/json",
            {
                "tree": (
                    "jobs[name,url,color,lastBuild[number,result,"
                    "timestamp,duration,url]]"
                )
            },
        )

        jobs = data.get("jobs")

        if not isinstance(jobs, list):
            raise JenkinsConnectionError(
                "Jenkins did not return a valid jobs list."
            )

        results = []

        for job in jobs:
            if not isinstance(job, dict):
                continue

            last_build = job.get("lastBuild")

            if not isinstance(last_build, dict):
                last_build = None

            results.append(
                {
                    "name": job.get("name"),
                    "url": job.get("url"),
                    "color": job.get("color"),
                    "last_build": (
                        {
                            "number": last_build.get("number"),
                            "result": last_build.get("result"),
                            "timestamp": last_build.get("timestamp"),
                            "duration_ms": last_build.get("duration"),
                            "url": last_build.get("url"),
                        }
                        if last_build
                        else None
                    ),
                }
            )

        return results

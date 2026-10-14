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

    @staticmethod
    def _job_path(job_name: str) -> str:
        from urllib.parse import quote

        name = job_name.strip()
        if not name:
            raise ValueError("A Jenkins job name is required.")

        return f"job/{quote(name, safe='')}"

    def _get_text(self, path: str) -> str:
        url = f"{self.base_url}/{path.lstrip('/')}"

        try:
            response = httpx.get(
                url,
                auth=self.auth,
                timeout=self.timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise JenkinsConnectionError(
                f"Jenkins did not respond within {self.timeout:g} seconds."
            ) from exc
        except httpx.RequestError as exc:
            raise JenkinsConnectionError(
                "Could not reach Jenkins while retrieving the build log."
            ) from exc

        if response.status_code in (401, 403):
            raise JenkinsConnectionError(
                "Jenkins rejected the build-log request. Check credentials "
                "and account permissions."
            )

        if response.is_redirect:
            raise JenkinsConnectionError(
                "Jenkins redirected the build-log request. Check the base URL."
            )

        if response.status_code >= 400:
            raise JenkinsConnectionError(
                f"Jenkins returned HTTP {response.status_code} "
                "while retrieving the build log."
            )

        return response.text

    def get_builds(self, job_name: str, limit: int = 30) -> list[dict]:
        limit = min(max(limit, 1), 100)
        job_path = self._job_path(job_name)

        data = self._get(
            f"{job_path}/api/json",
            {
                "tree": (
                    "builds[number,result,timestamp,duration,url,"
                    f"building,displayName]{{0,{limit}}}"
                )
            },
        )

        builds = data.get("builds")
        if not isinstance(builds, list):
            raise JenkinsConnectionError(
                "Jenkins did not return a valid build history."
            )

        return [
            {
                "number": build.get("number"),
                "result": build.get("result"),
                "timestamp": build.get("timestamp"),
                "duration_ms": build.get("duration"),
                "url": build.get("url"),
                "building": build.get("building", False),
                "display_name": build.get("displayName"),
            }
            for build in builds
            if isinstance(build, dict)
        ]

    def get_build_evidence(
        self,
        job_name: str,
        build_number: int,
    ) -> dict:
        if build_number < 1:
            raise ValueError("The build number must be positive.")

        build_path = f"{self._job_path(job_name)}/{build_number}"

        metadata = self._get(
            f"{build_path}/api/json",
            {
                "tree": (
                    "number,result,timestamp,duration,building,url,"
                    "displayName,fullDisplayName,"
                    "changeSets[items[commitId,msg,timestamp,"
                    "author[fullName]]]"
                )
            },
        )

        if metadata.get("number") != build_number:
            raise JenkinsConnectionError(
                "Jenkins returned metadata for an unexpected build."
            )

        # Fetch the console output from this exact build.
        console_log = self._get_text(f"{build_path}/consoleText")

        commits = []
        change_sets = metadata.get("changeSets", [])

        if isinstance(change_sets, list):
            for change_set in change_sets:
                if not isinstance(change_set, dict):
                    continue

                items = change_set.get("items", [])
                if not isinstance(items, list):
                    continue

                for item in items:
                    if not isinstance(item, dict):
                        continue

                    author = item.get("author") or {}
                    commits.append(
                        {
                            "sha": item.get("commitId"),
                            "message": item.get("msg"),
                            "timestamp": item.get("timestamp"),
                            "author": (
                                author.get("fullName")
                                if isinstance(author, dict)
                                else None
                            ),
                        }
                    )

        return {
            "job_name": job_name,
            "build_number": build_number,
            "result": metadata.get("result"),
            "building": metadata.get("building", False),
            "timestamp": metadata.get("timestamp"),
            "duration_ms": metadata.get("duration"),
            "url": metadata.get("url"),
            "display_name": metadata.get("displayName"),
            "full_display_name": metadata.get("fullDisplayName"),
            "commits": commits,
            "console_log": console_log,
        }

    @staticmethod
    def _job_path(job_name: str) -> str:
        from urllib.parse import quote

        name = job_name.strip()
        if not name:
            raise ValueError("A Jenkins job name is required.")

        return f"job/{quote(name, safe='')}"

    def _get_text(self, path: str) -> str:
        url = f"{self.base_url}/{path.lstrip('/')}"

        try:
            response = httpx.get(
                url,
                auth=self.auth,
                timeout=self.timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise JenkinsConnectionError(
                f"Jenkins did not respond within {self.timeout:g} seconds."
            ) from exc
        except httpx.RequestError as exc:
            raise JenkinsConnectionError(
                "Could not reach Jenkins while retrieving the build log."
            ) from exc

        if response.status_code in (401, 403):
            raise JenkinsConnectionError(
                "Jenkins rejected the build-log request. Check credentials "
                "and account permissions."
            )

        if response.is_redirect:
            raise JenkinsConnectionError(
                "Jenkins redirected the build-log request. Check the base URL."
            )

        if response.status_code >= 400:
            raise JenkinsConnectionError(
                f"Jenkins returned HTTP {response.status_code} "
                "while retrieving the build log."
            )

        return response.text

    def get_builds(self, job_name: str, limit: int = 30) -> list[dict]:
        limit = min(max(limit, 1), 100)
        job_path = self._job_path(job_name)

        data = self._get(
            f"{job_path}/api/json",
            {
                "tree": (
                    "builds[number,result,timestamp,duration,url,"
                    f"building,displayName]{{0,{limit}}}"
                )
            },
        )

        builds = data.get("builds")
        if not isinstance(builds, list):
            raise JenkinsConnectionError(
                "Jenkins did not return a valid build history."
            )

        return [
            {
                "number": build.get("number"),
                "result": build.get("result"),
                "timestamp": build.get("timestamp"),
                "duration_ms": build.get("duration"),
                "url": build.get("url"),
                "building": build.get("building", False),
                "display_name": build.get("displayName"),
            }
            for build in builds
            if isinstance(build, dict)
        ]

    def get_build_evidence(
        self,
        job_name: str,
        build_number: int,
    ) -> dict:
        if build_number < 1:
            raise ValueError("The build number must be positive.")

        build_path = f"{self._job_path(job_name)}/{build_number}"

        metadata = self._get(
            f"{build_path}/api/json",
            {
                "tree": (
                    "number,result,timestamp,duration,building,url,"
                    "displayName,fullDisplayName,"
                    "changeSets[items[commitId,msg,timestamp,"
                    "author[fullName]]]"
                )
            },
        )

        if metadata.get("number") != build_number:
            raise JenkinsConnectionError(
                "Jenkins returned metadata for an unexpected build."
            )

        # Fetch the console output from this exact build.
        console_log = self._get_text(f"{build_path}/consoleText")

        commits = []
        change_sets = metadata.get("changeSets", [])

        if isinstance(change_sets, list):
            for change_set in change_sets:
                if not isinstance(change_set, dict):
                    continue

                items = change_set.get("items", [])
                if not isinstance(items, list):
                    continue

                for item in items:
                    if not isinstance(item, dict):
                        continue

                    author = item.get("author") or {}
                    commits.append(
                        {
                            "sha": item.get("commitId"),
                            "message": item.get("msg"),
                            "timestamp": item.get("timestamp"),
                            "author": (
                                author.get("fullName")
                                if isinstance(author, dict)
                                else None
                            ),
                        }
                    )

        return {
            "job_name": job_name,
            "build_number": build_number,
            "result": metadata.get("result"),
            "building": metadata.get("building", False),
            "timestamp": metadata.get("timestamp"),
            "duration_ms": metadata.get("duration"),
            "url": metadata.get("url"),
            "display_name": metadata.get("displayName"),
            "full_display_name": metadata.get("fullDisplayName"),
            "commits": commits,
            "console_log": console_log,
        }

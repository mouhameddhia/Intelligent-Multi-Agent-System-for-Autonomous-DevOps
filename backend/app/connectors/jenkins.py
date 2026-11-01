from urllib.parse import quote, urlsplit

import httpx


class JenkinsConnectionError(Exception):
    """Raised when PipeGuard cannot communicate with Jenkins."""


class JenkinsConnector:
    TRIVY_ARTIFACT_PATH = "reports/trivy-image.json"
    TRIVY_MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
    TRIVY_MAX_FINDINGS = 25

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

    def _get_optional_json_artifact(self, build_path: str, relative_path: str) -> dict | None:
        """Fetch a bounded JSON artifact from one exact Jenkins build."""
        encoded_path = quote(relative_path, safe="/")
        url = f"{self.base_url}/{build_path}/artifact/{encoded_path}"
        try:
            response = httpx.get(
                url,
                auth=self.auth,
                timeout=self.timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise JenkinsConnectionError("Jenkins timed out while retrieving the Trivy report.") from exc
        except httpx.RequestError as exc:
            raise JenkinsConnectionError("Could not reach Jenkins while retrieving the Trivy report.") from exc

        if response.status_code == 404:
            return None
        if response.status_code in (401, 403):
            raise JenkinsConnectionError("Jenkins denied access to the archived Trivy report.")
        if response.is_redirect:
            raise JenkinsConnectionError("Jenkins redirected the Trivy report request.")
        if response.status_code >= 400:
            raise JenkinsConnectionError(
                f"Jenkins returned HTTP {response.status_code} while retrieving the Trivy report."
            )
        if len(response.content) > self.TRIVY_MAX_ARTIFACT_BYTES:
            raise JenkinsConnectionError("The archived Trivy report exceeds the 5 MiB safety limit.")
        try:
            report = response.json()
        except ValueError as exc:
            raise JenkinsConnectionError("The archived Trivy report is not valid JSON.") from exc
        if not isinstance(report, dict):
            raise JenkinsConnectionError("The archived Trivy report has an unexpected structure.")
        return report

    @classmethod
    def _summarize_trivy_report(cls, report: dict | None, error: str = "") -> dict:
        if error:
            return {"status": "unavailable", "message": error}
        if report is None:
            return {
                "status": "not_found",
                "message": "This build has no archived Trivy image report.",
            }

        results = report.get("Results")
        if not isinstance(results, list):
            return {"status": "invalid", "message": "The Trivy report has no valid Results list."}

        counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
        findings = []
        total = 0
        for result in results:
            if not isinstance(result, dict):
                continue
            vulnerabilities = result.get("Vulnerabilities") or []
            if not isinstance(vulnerabilities, list):
                continue
            for item in vulnerabilities:
                if not isinstance(item, dict):
                    continue
                total += 1
                severity = str(item.get("Severity") or "UNKNOWN").upper()
                counts[severity if severity in counts else "UNKNOWN"] += 1
                if len(findings) < cls.TRIVY_MAX_FINDINGS:
                    vulnerability_url = item.get("PrimaryURL")
                    findings.append({
                        "id": item.get("VulnerabilityID"),
                        "package": item.get("PkgName"),
                        "installed_version": item.get("InstalledVersion"),
                        "fixed_version": item.get("FixedVersion"),
                        "severity": severity,
                        "title": str(item.get("Title") or "")[:240],
                        "target": str(result.get("Target") or "")[:240],
                        "url": (
                            vulnerability_url
                            if isinstance(vulnerability_url, str)
                            and vulnerability_url.startswith("https://")
                            else None
                        ),
                    })

        metadata = report.get("Metadata") if isinstance(report.get("Metadata"), dict) else {}
        return {
            "status": "success",
            "artifact_path": cls.TRIVY_ARTIFACT_PATH,
            "artifact_name": str(report.get("ArtifactName") or "")[:240],
            "artifact_type": report.get("ArtifactType"),
            "generated_at": report.get("CreatedAt"),
            "schema_version": report.get("SchemaVersion"),
            "image_id": metadata.get("ImageID"),
            "counts": counts,
            "finding_count": total,
            "findings": findings,
            "findings_truncated": total > len(findings),
        }

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
                    "artifacts[fileName,relativePath],"
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

        artifacts = metadata.get("artifacts") if isinstance(metadata.get("artifacts"), list) else []
        has_trivy_artifact = any(
            isinstance(item, dict)
            and item.get("relativePath") == self.TRIVY_ARTIFACT_PATH
            for item in artifacts
        )
        trivy_report = None
        trivy_error = ""
        if has_trivy_artifact:
            try:
                trivy_report = self._get_optional_json_artifact(build_path, self.TRIVY_ARTIFACT_PATH)
                if trivy_report is None:
                    trivy_error = "Jenkins listed the Trivy report but did not return it for this build."
            except JenkinsConnectionError as exc:
                trivy_error = str(exc)

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
            "trivy": self._summarize_trivy_report(trivy_report, trivy_error),
        }

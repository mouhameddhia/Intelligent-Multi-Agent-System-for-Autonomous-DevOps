"""Read-only client for a namespace-scoped Kubernetes API connection."""

import ssl
from urllib.parse import quote, urlsplit

import httpx


class KubernetesConnectionError(Exception):
    """Raised when PipeGuard cannot read the configured Kubernetes API."""


class KubernetesConnector:
    TIMEOUT = 10.0
    RESOURCE_LIMIT = 100
    EVENT_LIMIT = 20

    def __init__(self, base_url: str, namespace: str, token: str, ca_certificate: str):
        base_url = base_url.strip().rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("Enter a Kubernetes API URL starting with https:// or http://.")
        if parsed.username or parsed.password:
            raise ValueError("Do not put credentials inside the Kubernetes API URL.")
        if parsed.query or parsed.fragment:
            raise ValueError("The Kubernetes API URL must not contain a query or fragment.")
        if not namespace or len(namespace) > 63 or not namespace[0].isalnum() or not namespace[-1].isalnum():
            raise ValueError("Enter a valid Kubernetes namespace name.")
        if any(not (char.islower() or char.isdigit() or char == "-") for char in namespace):
            raise ValueError("Namespace names must use lowercase letters, numbers, and hyphens.")
        token = token.strip()
        if not token or "\n" in token or "\r" in token:
            raise ValueError("A Kubernetes bearer token is required.")
        ca_certificate = ca_certificate.strip()
        if parsed.scheme == "https" and not ca_certificate:
            raise ValueError("Paste the cluster CA certificate to verify the Kubernetes API connection.")

        self.base_url = base_url
        self.namespace = namespace
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self.verify: ssl.SSLContext | bool = True
        if parsed.scheme == "https":
            try:
                self.verify = ssl.create_default_context(cadata=ca_certificate)
            except (ssl.SSLError, ValueError) as exc:
                raise ValueError("The Kubernetes CA certificate is not valid PEM data.") from exc

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            response = httpx.get(
                f"{self.base_url}/{path.lstrip('/')}",
                headers=self.headers,
                params=params,
                verify=self.verify,
                timeout=self.TIMEOUT,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise KubernetesConnectionError("The Kubernetes API did not respond within 10 seconds.") from exc
        except httpx.RequestError as exc:
            raise KubernetesConnectionError(
                "Could not reach the Kubernetes API. Check the API URL, network access, and CA certificate."
            ) from exc

        if response.status_code in (401, 403):
            raise KubernetesConnectionError(
                "Kubernetes denied access. Check the token and grant read access to the selected namespace."
            )
        if response.is_redirect:
            raise KubernetesConnectionError("The Kubernetes API redirected the request; check the API URL.")
        if response.status_code >= 400:
            raise KubernetesConnectionError(f"Kubernetes returned HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise KubernetesConnectionError("Kubernetes returned an unexpected non-JSON response.") from exc
        if not isinstance(payload, dict):
            raise KubernetesConnectionError("Kubernetes returned an unexpected response structure.")
        return payload

    def get_overview(self) -> dict:
        namespace_path = quote(self.namespace, safe="")
        prefix = f"api/v1/namespaces/{namespace_path}"
        version = self._get("version")
        deployments = self._get(f"apis/apps/v1/namespaces/{namespace_path}/deployments")
        pods = self._get(f"{prefix}/pods")
        services = self._get(f"{prefix}/services")
        events = self._get(f"{prefix}/events")

        deployment_items = [self._deployment(item) for item in deployments.get("items", []) if isinstance(item, dict)]
        pod_items = [self._pod(item) for item in pods.get("items", []) if isinstance(item, dict)]
        service_items = [self._service(item) for item in services.get("items", []) if isinstance(item, dict)]
        event_items = [self._event(item) for item in events.get("items", []) if isinstance(item, dict)]
        event_items.sort(key=lambda item: item.get("last_seen") or "", reverse=True)
        warning_events = [item for item in event_items if item.get("type") == "Warning"]

        return {
            "status": "connected",
            "cluster_version": version.get("gitVersion", "unknown"),
            "namespace": self.namespace,
            "namespace_status": "Active",
            "counts": {
                "deployments": len(deployment_items),
                "ready_deployments": sum(
                    1 for item in deployment_items
                    if item["ready_replicas"] >= item["desired_replicas"]
                ),
                "pods": len(pod_items),
                "ready_pods": sum(1 for item in pod_items if item["ready"]),
                "services": len(service_items),
                "warning_events": len(warning_events),
            },
            "deployments": deployment_items[: self.RESOURCE_LIMIT],
            "pods": pod_items[: self.RESOURCE_LIMIT],
            "services": service_items[: self.RESOURCE_LIMIT],
            "events": warning_events[: self.EVENT_LIMIT],
        }

    @staticmethod
    def _deployment(item: dict) -> dict:
        metadata = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        template = spec.get("template") or {}
        pod_spec = template.get("spec") or {}
        containers = pod_spec.get("containers") or []
        return {
            "name": metadata.get("name"),
            "desired_replicas": spec.get("replicas", 1),
            "ready_replicas": status.get("readyReplicas", 0),
            "available_replicas": status.get("availableReplicas", 0),
            "images": [container.get("image") for container in containers if isinstance(container, dict)],
            "labels": metadata.get("labels") or {},
        }

    @staticmethod
    def _pod(item: dict) -> dict:
        metadata = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        container_statuses = status.get("containerStatuses") or []
        ready_count = sum(1 for entry in container_statuses if isinstance(entry, dict) and entry.get("ready"))
        total_count = len(container_statuses)
        return {
            "name": metadata.get("name"),
            "phase": status.get("phase", "Unknown"),
            "ready": total_count > 0 and ready_count == total_count,
            "ready_containers": ready_count,
            "total_containers": total_count,
            "restarts": sum(
                entry.get("restartCount", 0)
                for entry in container_statuses if isinstance(entry, dict)
            ),
            "node": spec.get("nodeName"),
            "created_at": metadata.get("creationTimestamp"),
            "labels": metadata.get("labels") or {},
        }

    @staticmethod
    def _service(item: dict) -> dict:
        metadata = item.get("metadata") or {}
        spec = item.get("spec") or {}
        return {
            "name": metadata.get("name"),
            "type": spec.get("type", "ClusterIP"),
            "cluster_ip": spec.get("clusterIP"),
            "ports": [
                {"port": port.get("port"), "target_port": port.get("targetPort"), "protocol": port.get("protocol", "TCP")}
                for port in spec.get("ports", []) if isinstance(port, dict)
            ],
            "labels": metadata.get("labels") or {},
        }

    @staticmethod
    def _event(item: dict) -> dict:
        metadata = item.get("metadata") or {}
        involved = item.get("regarding") or item.get("involvedObject") or {}
        return {
            "type": item.get("type", "Normal"),
            "reason": item.get("reason"),
            "message": str(item.get("message") or "")[:300],
            "object": involved.get("name"),
            "object_kind": involved.get("kind"),
            "count": item.get("count", item.get("series", {}).get("count", 1)),
            "last_seen": item.get("eventTime") or item.get("lastTimestamp") or metadata.get("creationTimestamp"),
        }

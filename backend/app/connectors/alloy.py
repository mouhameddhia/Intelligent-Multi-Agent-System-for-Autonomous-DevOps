"""Provision the PipeGuard-managed Alloy collector in the configured Vagrant VM."""

import base64
import json
import subprocess
from pathlib import Path


class AlloyProvisioningError(Exception):
    """Raised when Alloy cannot be provisioned in the Docker host."""


def _build_config(
    *,
    project_name: str,
    loki_url: str,
    username: str,
    has_token: bool,
) -> str:
    project_value = json.dumps(project_name)
    push_url = json.dumps(f"{loki_url.rstrip('/')}/loki/api/v1/push")
    auth_block = ""
    if has_token and username:
        auth_block = f"""
    basic_auth {{
      username = {json.dumps(username)}
      password_file = "/etc/alloy-secrets/loki-token"
    }}
"""
    elif has_token:
        auth_block = """
    authorization {
      type = "Bearer"
      credentials_file = "/etc/alloy-secrets/loki-token"
    }
"""

    return f"""discovery.docker "pipeguard" {{
  host = "unix:///var/run/docker.sock"
  refresh_interval = "1s"
}}

discovery.relabel "pipeguard_targets" {{
  targets = discovery.docker.pipeguard.targets

  rule {{
    source_labels = ["__meta_docker_container_label_pipeguard_project"]
    regex = {project_value}
    action = "keep"
  }}

  rule {{
    source_labels = ["__meta_docker_container_name"]
    regex = "/(.*)"
    target_label = "container"
  }}

  rule {{
    source_labels = ["__meta_docker_container_label_pipeguard_build"]
    target_label = "build_number"
  }}

  rule {{
    source_labels = ["__meta_docker_container_label_pipeguard_project"]
    target_label = "project"
  }}
}}

loki.source.docker "pipeguard_targets" {{
  host = "unix:///var/run/docker.sock"
  targets = discovery.relabel.pipeguard_targets.output
  labels = {{"job" = "pipeguard"}}
  forward_to = [loki.write.pipeguard.receiver]
}}

loki.write "pipeguard" {{
  endpoint {{
    url = {push_url}
{auth_block.rstrip()}
  }}
}}
"""


def ensure_alloy_collector(
    *,
    vagrant_dir: str,
    project_name: str,
    loki_url: str,
    username: str = "",
    token: str = "",
) -> dict:
    """Run Alloy and watch containers labeled for one PipeGuard project.

    Containers can be created and removed by CI builds after this collector is
    connected. Vagrant SSH avoids exposing the remote Docker API. The only
    Docker resource replaced is this routine's own managed Alloy container.
    """
    project_dir = Path(vagrant_dir).expanduser()
    if not project_dir.is_dir() or not (project_dir / "Vagrantfile").is_file():
        raise AlloyProvisioningError(
            f"Vagrant project directory is unavailable: {project_dir}. "
            "Set LOKI_VAGRANT_DIR to the folder containing the Vagrantfile."
        )
    if "\n" in token or "\r" in token:
        raise AlloyProvisioningError("The Loki token cannot contain line breaks.")

    config = _build_config(
        project_name=project_name,
        loki_url=loki_url,
        username=username,
        has_token=bool(token),
    )
    config_b64 = base64.b64encode(config.encode("utf-8")).decode("ascii")
    token_b64 = base64.b64encode(token.encode("utf-8")).decode("ascii")
    secret_mount = '  -v "$secret_path":/etc/alloy-secrets/loki-token:ro \\\n' if token else ""

    # The token is sent over SSH stdin and stored in a mode-600 file, never in
    # Alloy's config or the container's command line.
    remote_command = f"""set -eu
config_dir="$HOME/.pipeguard/loki"
mkdir -p "$config_dir"
chmod 700 "$HOME/.pipeguard" "$config_dir"
config_path="$config_dir/alloy.config"
secret_path="$config_dir/loki-token"
printf '%s' {json.dumps(config_b64)} | base64 -d > "$config_path"
chmod 644 "$config_path"
""" + (
        "cat > \"$secret_path.b64\"\n"
        "base64 -d \"$secret_path.b64\" > \"$secret_path\"\n"
        "rm -f \"$secret_path.b64\"\n"
        "chmod 600 \"$secret_path\"\n"
        if token
        else "rm -f \"$secret_path\"\n"
    ) + f"""

if docker container inspect pipeguard-alloy >/dev/null 2>&1; then
  managed=$(docker inspect --format '{{{{ index .Config.Labels "io.pipeguard.managed-by" }}}}' pipeguard-alloy)
  [ "$managed" = "pipeguard" ] || {{ echo "Refusing to replace an unmanaged container named pipeguard-alloy." >&2; exit 23; }}
  docker rm -f pipeguard-alloy >/dev/null
fi

docker pull grafana/alloy:latest-distroless >/dev/null
docker run -d \\
  --name pipeguard-alloy \\
  --label io.pipeguard.managed-by=pipeguard \\
  --label io.pipeguard.loki.project={json.dumps(project_name)} \\
  --restart unless-stopped \\
  --user 0:0 \\
  --network host \\
  -v /var/run/docker.sock:/var/run/docker.sock \\
  -v "$config_path":/etc/alloy/config.alloy:ro \\
  -v pipeguard-alloy-data:/var/lib/alloy/data \\
{secret_mount}  grafana/alloy:latest-distroless \\
  run --storage.path=/var/lib/alloy/data /etc/alloy/config.alloy >/dev/null

for attempt in $(seq 1 20); do
  status=$(docker inspect --format '{{{{.State.Status}}}}' pipeguard-alloy 2>/dev/null || true)
  if [ "$status" = "running" ]; then
    sleep 2
    status=$(docker inspect --format '{{{{.State.Status}}}}' pipeguard-alloy 2>/dev/null || true)
    [ "$status" = "running" ] && {{ echo "PIPEGUARD_ALLOY_RUNNING"; exit 0; }}
  fi
  if [ "$status" = "exited" ]; then
    docker logs --tail 40 pipeguard-alloy >&2 || true
    exit 24
  fi
  sleep 1
done
docker logs --tail 40 pipeguard-alloy >&2 || true
exit 25
"""

    try:
        result = subprocess.run(
            ["vagrant", "ssh", "-c", remote_command],
            cwd=project_dir,
            input=(token_b64 + "\n") if token else "",
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AlloyProvisioningError("Vagrant is not installed or is not on the backend PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise AlloyProvisioningError("Timed out while provisioning Alloy in the Vagrant VM.") from exc
    except OSError as exc:
        raise AlloyProvisioningError(f"Could not start the Vagrant SSH command: {exc}") from exc

    # The marker is printed only after Docker reports the collector running.
    # In this Vagrant setup, the remote command can emit that marker while the
    # local Vagrant process still exits nonzero; treat the verified marker as
    # the authoritative provisioning result.
    if "PIPEGUARD_ALLOY_RUNNING" not in result.stdout:
        detail = (result.stderr or result.stdout or "No diagnostic output was returned.").strip()
        if token:
            detail = detail.replace(token, "[redacted]")
        raise AlloyProvisioningError(
            f"Vagrant SSH failed with exit code {result.returncode}: {detail[-2500:]}"
        )

    return {"status": "running", "container": "pipeguard-alloy", "project": project_name}

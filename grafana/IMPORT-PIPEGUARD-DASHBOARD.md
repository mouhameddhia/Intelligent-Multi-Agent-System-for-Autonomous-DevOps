# Import the PipeGuard observability dashboard

I prepared `PipeGuard-Observability-Dashboard.json` with Jenkins health, latest build status and duration by job, Jenkins executor load, Vagrant CPU/memory/disk usage, and an Alloy/Loki log panel.

## Why I could not save it directly

Grafana and its health endpoint are reachable, but Grafana’s datasource and dashboard APIs return `401 Unauthorized`. I did not guess an administrator password or bypass that account boundary. The Vagrant VM and the Prometheus/Loki endpoints are reachable from the Grafana container, so the remaining work is to sign in to Grafana and apply the datasource/dashboard configuration.

## Add the datasources

Open Grafana at [http://192.168.56.10:3000](http://192.168.56.10:3000), then go to **Connections → Data sources → Add data source**.

1. Add **Prometheus** with URL `http://prometheus:9090`, then select **Save & test**. Grafana shares a Docker network with Prometheus, and that URL is reachable from its container.
2. Add **Loki** with URL `http://192.168.56.10:3100`, then select **Save & test**. Grafana and Loki are on separate Docker networks, so use the VM’s published port instead of the Docker service name `loki`.

Loki is reachable and ready, but it currently reports no stream labels. The dashboard’s log panel will remain empty until Alloy sends labeled container logs to Loki. It filters on PipeGuard’s `project` label; the collector expects containers labeled `pipeguard_project` and `pipeguard_build`.

## Import the dashboard

1. Go to **Dashboards → New → Import**.
2. Upload `PipeGuard-Observability-Dashboard.json`.
3. Map the Prometheus and Loki inputs to the datasources created above, then select **Import**.

The dashboard has UID `pipeguard-observability`. After import, its URL should be:

`http://192.168.56.10:3000/d/pipeguard-observability/pipeguard-ci-cd-observability`

If Grafana changes the slug during import, open the dashboard and copy its URL; the UID remains the stable part of the link. The URL can then be entered as PipeGuard’s optional **Build dashboard URL**.

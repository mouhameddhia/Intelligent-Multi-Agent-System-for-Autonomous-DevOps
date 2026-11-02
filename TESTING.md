# Setting up a real failure scenario for PipeGuard

This documents how the `test_devops` repo and the `pipepline` Jenkins job were
wired together to produce a real, reproducible build failure for testing
PipeGuard's investigation pipeline — plus the problems hit along the way and
how each was fixed. No tokens or secrets are included here; they live in
`.env` (already git-ignored).

## 1. The goal

Jenkins is the only trigger in this system — nothing else starts an
investigation. To test PipeGuard properly we needed a Jenkins build that:

- actually fails, for a real reason (not a typo in the pipeline script)
- is tied to a real GitHub commit, so the GitHub evidence panel has
  something real to show (not `not_configured`)

## 2. Seeding the GitHub repo

`https://github.com/mouhameddhia/test_devops` was empty. Three files were
added directly via the GitHub Contents API:

- **`refund.py`** — a small function with a deliberate bug:
  ```python
  def calculate_refund(amount, fee_percent=15):
      fee = amount * fee_percent        # bug: should be / 100
      return amount - fee
  ```
- **`test_refund.py`** — a unit test that expects the correct behavior
  (`calculate_refund(100) == 85.0`), which the bug breaks.
- **`Jenkinsfile`** — runs that test as part of a Jenkins pipeline.

**Problem hit:** the first push attempt failed with `403 Resource not
accessible by personal access token` — the GitHub token only had *read*
access. Once its permissions were updated to include **Contents: Read and
write**, the push succeeded. (This same permission is required later for
PipeGuard's own auto-fix feature to push a corrected file.)

## 3. Pointing Jenkins at the repo

In the Jenkins job (`pipepline` → Configure → Pipeline section), the
definition was changed from a script typed directly into the job to
**"Pipeline script from SCM"**, pointing at the repo above, branch `main`,
script path `Jenkinsfile`. No credentials were needed — the repo is public.

## 4. Two environment problems discovered by actually running it

The first triggered build failed immediately, **before even reaching the
test** — which is how both of these were discovered:

**Problem A — wrong OS assumption.** The Jenkinsfile used a `bat` (Windows
batch) step. It turned out Jenkins itself isn't running natively on Windows
at all — it's running inside a **Linux** Docker container
(`jenkins/jenkins:lts`), confirmed with:
```
docker ps | grep jenkins
docker exec jenkins python3 --version   # (checking for an interpreter)
```
Fix: changed the Jenkinsfile's `bat 'python ...'` step to `sh 'python3 ...'`.

**Problem B — no Python in the container.** The official Jenkins image is a
minimal Debian JVM image with no Python preinstalled. Fixed by installing it
directly in the running container:
```
docker exec -u root jenkins apt-get update
docker exec -u root jenkins apt-get install -y python3
```
This survives container restarts, but not a full container recreation —
worth remembering if the Jenkins container is ever rebuilt from scratch.

**Problem C — a transient network blip.** One early build also failed with
`Could not connect to server` while fetching from GitHub. Retried moments
later from inside the container (`docker exec jenkins curl https://github.com`)
and it worked fine — this looks like it was a one-off hiccup, not a
persistent issue.

## 5. The result

With both fixes applied, the build now fails for the *right* reason:

```
FAIL: test_standard_refund (test_refund.TestRefund.test_standard_refund)
AssertionError: -1400 != 85.0
```

tied to a real commit SHA, with a real Git changeset Jenkins can report. This
is what PipeGuard's Monitoring/Diagnosis agents now have to work with when
you click **Investigate** on that build.

## 6. Giving Loki, Prometheus and Grafana something real to correlate

Jenkins is the only *trigger*, but Loki/Prometheus/Grafana are only ever
correlated to a build by **time window** (±5 minutes around the build's
start/end) — nothing links them to the Jenkins job structurally. So to make
their evidence panels show something real (not `not_configured`), real data
had to exist in each of them at the same real-world moment a build ran.

**Loki — stood up for real.** It had never actually been run before (only a
mock HTTP server was used earlier to test the connector code in isolation).
Started a real instance:
```
docker run -d --name pipeguard-loki --network pipeguard-net -p 3100:3100 \
  grafana/loki -config.file=/etc/loki/local-config.yaml
```
**Problem hit:** on Windows, Git Bash auto-converts any argument that looks
like a Unix path, so `/etc/loki/local-config.yaml` was silently rewritten to
a Windows path (`C:/Program Files/Git/etc/...`) before Docker ever saw it,
and the container failed immediately with "config file does not exist."
Fixed by prefixing the command with `MSYS_NO_PATHCONV=1` to disable that
conversion. `LOKI_URL=http://127.0.0.1:3100` was then added to `.env` so the
frontend auto-connects it on load, same as the other tools.

With Loki actually running, build #6 was triggered via the Jenkins API, and
right after it finished, a real log line was pushed directly to Loki's push
API (`POST /loki/api/v1/push`), timestamped to that exact build:
```
ERROR payment-service: refund calculation returned negative amount (-1400.0)
for a $100 order — billing defect, build pipepline #6
```
Verified by querying it back through PipeGuard's own `/api/loki/query-range`
endpoint (not just raw Loki) — it came back with the correct timestamp and
labels. One cosmetic issue: the em dash in the message got corrupted into a
UTF-8 replacement character while passing through the shell's JSON encoding
— harmless, just not pretty in the raw log text.

**Prometheus — correlated in real time, not backdated.** Prometheus has no
API for inserting a metric at a past timestamp (unlike Loki/Grafana, which
both accept an explicit time in their push/annotation APIs). So instead, a
~45-second CPU-stress loop was started on the host at the same moment build
#6 was triggered, so `node_exporter`'s normal continuous scraping would
capture a real spike aligned with that build's time window.

**Grafana — annotation blocked by role, then fixed.** Posting an annotation
via `POST /api/annotations` with the existing service-account token first
failed with `403 … Permissions needed: annotations:create` — the service
account was created with the **Viewer** role, which can read
dashboards/annotations but can't create one. Fixed by upgrading that service
account to **Editor** (Administration → Service accounts → `pipeguard` →
role → Editor). Retried the exact same request and it succeeded:
```
{"id":1,"message":"Annotation added"}
```
tagged `incident`, `payment-service`, `jenkins`, timestamped to build #6.
Verified it through PipeGuard's own `/api/grafana/annotations` endpoint (not
just raw Grafana) — it came back with the correct time, text and tags.

## 7. Current state

All three are now real and time-aligned with build #6:

| Tool | State |
|---|---|
| GitHub | real commit (`ac03fa1`), real diff |
| Loki | real error log line, timestamp-matched |
| Prometheus | real `node_exporter` data, with a real CPU spike during the build |
| Grafana | real annotation, timestamp-matched |

Running **Investigate** on build #6 (or any later one, since the bug is
still in `main`) should now show real, non-placeholder evidence in every
panel.

## 8. A full real run, end to end — including the Approve & fix path

Since no browser automation is available in this environment, the real
backend was driven directly through the exact same sequence the frontend
performs, to prove the whole pipeline end to end rather than just each
connector in isolation.

**Build #7 triggered.** Its `commits` array came back empty — correct
Jenkins behavior, not a bug: nothing had changed in the repo since build #6,
so there was no new changeset to report. The frontend's own fallback for
this exact case — extracting a commit SHA from the console log's
`"Checking out Revision ..."` line via regex — was used instead, and it
found `ac03fa1...`.

**A real, honest complication:** that commit turned out to be the earlier
*"Fix Jenkinsfile"* commit, not the one that actually introduced the bug in
`refund.py` — those were separate, earlier commits. So the GitHub evidence
handed to the agents was technically real, but pointed at the wrong file.
This is a genuine property of how GitHub evidence is linked (whatever
commit Jenkins reports building, not necessarily the one that caused the
regression), not something staged for the test.

**Diagnosis handled it correctly.** Given that misleading evidence, it did
not get fooled — it explicitly said *"the change is unrelated to the refund
logic"* and *"the buggy code was introduced in a prior change that is not
captured in the current payload,"* then still correctly diagnosed the real
root cause from the test failure itself (confidence 85%, lower than a clean
case, appropriately).

**Approved for real.** The Fix agent correctly targeted `refund.py` — not
the `Jenkinsfile` the (misleading) commit evidence pointed at — fetched it
and its test file, and pushed a real commit
(`490aa991...`, `fix: PipeGuard automated remediation for pipepline build #7`)
with the correct fix: `fee = amount * fee_percent / 100`.

**A second real problem, found by actually verifying:** rebuilding
immediately after the push still failed, because Jenkins' `git fetch`
resolved `refs/remotes/origin/main` to the *old* commit — a genuine
propagation delay between GitHub's Contents API write and its smart-HTTP
git read path. Confirmed independently via the GitHub API that `main` was
already correctly at the new commit by this point; it was specifically
Jenkins' fetch that hadn't caught up yet. Waiting ~10 seconds and rebuilding
again resolved it.

**Build #9: `SUCCESS`.** Checked out the fix commit, both tests passed. This
is the one case in this whole exercise where a commit succeeding was
actually confirmed by a verified green rebuild — not just assumed.

The takeaway: if PipeGuard (or a human) ever rebuilds immediately after an
auto-fix commit and sees the old failure again, the first thing to check is
whether the SCM fetch actually caught up — not whether the fix itself was
wrong.

## 9. Measured metrics, from two real trials

Real timings and outcomes from two independent, real end-to-end runs: the
first (section 8, incident `8178912c...`, build #7→#9) and a second one run
specifically to measure this (incident `bdb040ee...`, build #12→#13). Nothing
below is estimated or invented — each number comes from an actual API call,
an actual Jenkins build, or an actual Groq response.

| Metric | Trial 1 (build #7) | Trial 2 (build #12) |
|---|---|---|
| Evidence collection | not separately timed | 1.05 s |
| Investigation (Monitoring+Diagnosis+Remediation) | not separately timed | 8.83 s |
| Fix application (fetch, rewrite, commit) | not separately timed | 12.04 s |
| Overall confidence | 92% (Monitoring 100, Diagnosis 85, Remediation 92) | 97% (Monitoring 100, Diagnosis 95, Remediation 96) |
| Diagnosis correct? | Yes, despite a misleading linked commit | Yes |
| Fix correct? | Yes | Yes |
| Verified by rebuild? | Yes (after a retry — see section 8) | Yes (first attempt, after a deliberate 20 s wait) |
| Regression check | not separately checked | `test_zero_fee` still passes — no regression |
| Approval compliance | 1/1 respected | 1/1 respected |
| Unsafe actions | 0 | 0 |
| Historical match found? | No (first incident ever recorded) | **Yes** — correctly matched incident from build #7, and Remediation's plan explicitly said so |

**On MTTR specifically:** a naive wall-clock measurement for trial 2 (build
#12's failure timestamp to build #13's verified success) came out to roughly
535 minutes — because build #12 had failed the previous day and sat
uninvestigated until this trial picked it back up. That gap is a real
artifact of this session's own timing, not a property of the system, and
reporting it as "MTTR" without that caveat would be misleading. The
figure that actually reflects the system's own speed is the sum of its
active steps: **≈ 46 seconds** (1.05 s evidence + 8.83 s investigation +
12.04 s fix + a deliberate 20 s propagation wait, learned from section 7's
real problem + 4.12 s rebuild), with the human approval step itself
unmeasured since it is, by design, not something the system should be timed
on.

**On token cost:** Groq's own response does carry real, usable token-usage
data (confirmed directly: a one-line test call returned
`prompt_tokens`/`completion_tokens`/`total_tokens`), but the current
agent code does not capture or log it anywhere. So per-investigation token
cost is *measurable* but not *currently measured* — a small, concrete gap
worth adding to the report's limitations list rather than estimating a
number that was never actually recorded.

# Frontend Patch Instructions

This file describes the **only two changes** needed in the existing frontend (App.jsx).
Nothing else needs to be touched.

---

## Change 1 — Add the Agent API URL constant

At the top of `frontend/src/App.jsx`, alongside the existing:

```js
const API_URL = 'http://127.0.0.1:8000'
```

Add:

```js
const AGENT_API_URL = 'http://127.0.0.1:8001'
```

---

## Change 2 — Add the Investigate button to the JobsTable component

Inside the `JobsTable` function (around line 859), find the last `<td>` in each job row:

```jsx
<td>
  <a className="job-link" href={job.url} target="_blank" rel="noreferrer">
    Open ↗
  </a>
</td>
```

Replace it with:

```jsx
<td style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
  <a className="job-link" href={job.url} target="_blank" rel="noreferrer">
    Open ↗
  </a>
  {status === 'FAILURE' && (
    <button
      className="primary-button"
      style={{ fontSize: '0.75rem', padding: '4px 10px' }}
      onClick={() => onInvestigate(job)}
    >
      Investigate
    </button>
  )}
</td>
```

Then update the `JobsTable` call signature to accept `onInvestigate`:

```jsx
function JobsTable({ jobs, onInvestigate }) {
```

And wherever `JobsTable` is rendered (two places in App), pass the handler:

```jsx
<JobsTable jobs={jenkinsJobs} onInvestigate={handleInvestigate} />
```

Where `handleInvestigate` is defined in the `App` component:

```jsx
async function handleInvestigate(job) {
  const response = await fetch(`${AGENT_API_URL}/api/investigate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      job_name: job.name,
      build_number: job.last_build?.number ?? 0,
    }),
  })
  const data = await response.json()
  // Display the result however you like — alert for now
  alert(
    `MONITORING:\n${data.monitoring_report}\n\n` +
    `DIAGNOSIS:\n${data.diagnosis_report}\n\n` +
    `REMEDIATION:\n${data.remediation_report}`
  )
}
```

---

## To run the agent backend

```bash
cd agent_system
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY
pip install -r requirements.txt
python main.py
```

The agent API will be available at http://localhost:8001

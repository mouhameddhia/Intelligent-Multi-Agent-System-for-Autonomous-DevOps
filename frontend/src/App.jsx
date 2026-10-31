import { useCallback, useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { extractSection } from './reportSections'
import './App.css'

const API_URL = 'http://127.0.0.1:8000';
const AGENT_API_URL = API_URL;

const initialIntegrations = [
  {
    id: 'jenkins',
    name: 'Jenkins',
    description: 'Monitor CI/CD jobs, builds, stages and console logs.',
    category: 'CI/CD',
    icon: 'J',
    color: 'jenkins',
    fields: [
      { name: 'url', label: 'Jenkins URL', placeholder: 'http://192.168.56.10:8080' },
      { name: 'username', label: 'Username', placeholder: 'Your Jenkins username' },
      { name: 'token', label: 'API token', placeholder: 'Enter API token', secret: true },
    ],
  },
  {
    id: 'github',
    name: 'GitHub',
    description: 'Access repositories, commits and workflow information.',
    category: 'Source control',
    icon: '⌘',
    color: 'github',
    fields: [
      { name: 'token', label: 'Personal access token', placeholder: 'Enter GitHub token', secret: true },
    ],
  },
  {
    id: 'prometheus',
    name: 'Prometheus',
    description: 'Collect metrics and identify abnormal system behavior.',
    category: 'Monitoring',
    icon: 'P',
    color: 'prometheus',
    fields: [
      { name: 'url', label: 'Prometheus URL', placeholder: 'http://localhost:9090' },
    ],
  },
]

const navigation = [
  { id: 'dashboard', icon: '▦', label: 'Dashboard' },
  { id: 'pipelines', icon: '⑂', label: 'Pipelines' },
  { id: 'incidents', icon: '◈', label: 'Incidents' },
]

function formatDuration(milliseconds) {
  if (milliseconds == null) return '—'
  if (milliseconds < 1000) return `${milliseconds} ms`
  return `${(milliseconds / 1000).toFixed(2)} s`
}

function formatTimestamp(timestamp) {
  if (!timestamp) return 'Not available'
  return new Date(timestamp).toLocaleString()
}

function getBuildStatus(job) {
  if (!job.last_build) return 'NO BUILDS'
  if (!job.last_build.result) return 'RUNNING'

  return job.last_build.result
}

function statusClass(status) {
  if (status === 'SUCCESS' || status === 'blue') return 'success'
  if (status === 'FAILURE' || status === 'red') return 'failure'
  if (status === 'UNSTABLE' || status === 'yellow') return 'unstable'
  if (status === 'RUNNING' || status.endsWith('_anime')) return 'running'
  return 'neutral'
}

function formatJudgeAuditForReport(reports) {
  const scores = reports.judgeScores || {}
  const issues = reports.judgeIssues || []
  const hallucination = reports.judgeHallucinationDetected
  const lines = []

  if (typeof reports.judgeOverallScore === 'number') {
    lines.push(`**Judge score: ${reports.judgeOverallScore}%**`)
  }
  const scoreParts = Object.entries(scores)
    .filter(([, score]) => score !== null && score !== undefined)
    .map(([name, score]) => `${name[0].toUpperCase()}${name.slice(1)} (${score}%)`)
  if (scoreParts.length) lines.push(`**Per-agent scores:** ${scoreParts.join(', ')}`)
  if (typeof reports.judgeHistoricalMatchQuality === 'number') {
    lines.push(`**Historical match quality:** ${reports.judgeHistoricalMatchQuality}%`)
  }

  if (hallucination === false) {
    lines.push('**✅ No hallucinations detected in the audited claims.**')
    if (issues.length) {
      lines.push('', '**Review notes:**', ...issues.map((issue) => `- ${issue}`))
    }
  } else if (hallucination === true) {
    lines.push('**⚠️ The judge detected possible hallucinations or unsupported claims.**')
    if (issues.length) lines.push('', '**Evidence concerns:**', ...issues.map((issue) => `- ${issue}`))
  } else if (issues.length) {
    lines.push('**Review notes:**', ...issues.map((issue) => `- ${issue}`))
  }

  const verdict = (reports.judgeVerdict || '').trim()
  if (verdict) lines.push('', verdict)
  return lines.join('\n') || '_Judge verdict unavailable for this incident._'
}

function getUnifiedReportForDisplay(reports) {
  const unified = reports.unified || ''
  const auditHeading = '## Independent audit (LLM judge)'
  const auditStart = unified.indexOf(auditHeading)
  if (auditStart < 0) return unified

  const nextSection = unified.indexOf('\n---\n\n## Overall agent confidence estimate', auditStart)
  if (nextSection < 0) return unified

  return `${unified.slice(0, auditStart + auditHeading.length)}\n\n${formatJudgeAuditForReport(reports)}${unified.slice(nextSection)}`
}

function App() {
  const [activePage, setActivePage] = useState('dashboard')
  const [integrations, setIntegrations] = useState(initialIntegrations)
  const [selectedIntegration, setSelectedIntegration] = useState(null)
  const [formValues, setFormValues] = useState({})
  const [jenkinsCredentials, setJenkinsCredentials] = useState(null)
  const [jenkinsJobs, setJenkinsJobs] = useState([])
  const [connectionStatus, setConnectionStatus] = useState('disconnected')
  const [connectionMessage, setConnectionMessage] = useState('')
  const [isConnecting, setIsConnecting] = useState(false)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [lastRefresh, setLastRefresh] = useState(null)
  const [pageError, setPageError] = useState('')
  const [githubToken, setGithubToken] = useState('')
  const [githubRepositories, setGithubRepositories] = useState([])
  const [selectedGithubRepo, setSelectedGithubRepo] = useState('')
  const [githubCommits, setGithubCommits] = useState([])
  const [githubDataError, setGithubDataError] = useState('')
  const [isLoadingGithubData, setIsLoadingGithubData] = useState(false)
  const [githubConnectionStatus, setGithubConnectionStatus] = useState('disconnected')
  const [githubConnectionMessage, setGithubConnectionMessage] = useState('')
  const [prometheusUrl, setPrometheusUrl] = useState('')
  const [prometheusTargets, setPrometheusTargets] = useState([])
  const [prometheusSummary, setPrometheusSummary] = useState(null)
  const [prometheusConnectionStatus, setPrometheusConnectionStatus] = useState('disconnected')
  const [prometheusConnectionMessage, setPrometheusConnectionMessage] = useState('')
  const [investigation, setInvestigation] = useState(null)
  const [isInvestigating, setIsInvestigating] = useState(false)
  const [isInvestigationModalOpen, setIsInvestigationModalOpen] = useState(false)
  const [activeInvestigationStage, setActiveInvestigationStage] = useState(0)

  async function investigateBuild(job) {
    if (isInvestigating) return
  
    const buildNumber = job.last_build?.number
    if (!jenkinsCredentials || !buildNumber) return
  
    const traceId =
      typeof crypto !== 'undefined' && crypto.randomUUID
        ? crypto.randomUUID()
        : `investigate-${Date.now()}`
  
    const traceStartedAt = performance.now()
  
    const trace = (step, details = {}) => {
      console.log('[INVESTIGATION_TRACE]', {
        traceId,
        step,
        timestamp: new Date().toISOString(),
        elapsedMs: Math.round(performance.now() - traceStartedAt),
        ...details,
      })
    }
  
    trace('investigation_started', { jobName: job.name, buildNumber })
  
    setIsInvestigating(true)
    setActiveInvestigationStage(0)
    setIsInvestigationModalOpen(true)
    setInvestigation({
      jobName: job.name,
      buildNumber,
      evidence: null,
      github: null,
      metrics: [],
      agentStatus: 'waiting',
      agentReports: null,
      agentError: '',
      nodeProgress: [],
      currentNode: null,
    })
  
    async function postJson(path, body) {
      const response = await fetch(`${API_URL}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.detail || `${path} failed (HTTP ${response.status})`)
      }
      return data
    }
  
    try {
      // First retrieve the selected build itself; never substitute another build.
      const evidenceData = await postJson('/api/jenkins/build-evidence', {
        ...jenkinsCredentials,
        job_name: job.name,
        build_number: buildNumber,
      })
      const evidence = evidenceData
      const shaFromJenkins = evidence.commits?.find((item) => item.sha)?.sha
      const shaFromLog = evidence.console_log?.match(/\b[0-9a-f]{40}\b/i)?.[0]
      const sha = shaFromJenkins || shaFromLog || null
  
      setInvestigation({
        jobName: job.name,
        buildNumber,
        evidence,
        github: { status: 'loading', sha },
        metrics: [],
      })
  
      // GitHub lookup is tied to the exact commit SHA found in this build.
      let github = {
        status: 'not_configured',
        message: 'Connect GitHub and select the repository containing this commit.',
        sha,
      }
  
      if (!githubToken) {
        github = { ...github, status: 'not_configured' }
      } else if (!selectedGithubRepo) {
        github = { ...github, status: 'not_configured' }
      } else if (!sha) {
        github = {
          status: 'unavailable',
          message: 'Jenkins did not expose a full commit SHA in its metadata or console log.',
        }
      } else {
        const [owner, repository] = selectedGithubRepo.split('/')
        try {
          const result = await postJson('/api/github/commit', {
            token: githubToken,
            owner,
            repository,
            sha,
          })
          github = { status: 'success', ...result.commit }
        } catch (error) {
          github = { status: 'error', sha, message: error.message }
        }
      }
  
      // Query real Prometheus samples around this build's start/end timestamps.
      let metrics = []
      if (!prometheusUrl) {
        metrics = [{
          query: 'Prometheus configuration',
          status: 'not_configured',
          message: 'Connect Prometheus in Settings to retrieve time-aligned metrics.',
        }]
      } else if (!evidence.timestamp) {
        metrics = [{
          query: 'Prometheus range',
          status: 'unavailable',
          message: 'The selected Jenkins build has no timestamp for alignment.',
        }]
      } else {
        const start = Math.floor(evidence.timestamp / 1000) - 300
        const end = Math.ceil((evidence.timestamp + (evidence.duration_ms || 0)) / 1000) + 300
        const expressions = [
          'up',
          'node_load1',
          'node_memory_MemAvailable_bytes',
          'rate(node_cpu_seconds_total{mode!="idle"}[5m])',
        ]
  
        metrics = await Promise.all(expressions.map(async (query) => {
          try {
            const result = await postJson('/api/prometheus/query-range', {
              url: prometheusUrl,
              query,
              start,
              end,
              step: '30s',
            })
            return {
              query,
              status: 'success',
              count: result.count,
              results: result.results || [],
              start,
              end,
            }
          } catch (error) {
            return { query, status: 'error', message: error.message }
          }
        }))
      }
  
      // Live provider evidence is complete; now request the real agent workflow.
      setActiveInvestigationStage(1)
  
      // Preserve the provider evidence in the UI and pass a bounded evidence
      // bundle to the agents. Provider credentials are deliberately not sent.
      setInvestigation({
        jobName: job.name,
        buildNumber,
        evidence,
        github,
        metrics,
        agentStatus: 'running',
        agentReports: null,
        agentError: '',
        nodeProgress: [],
        currentNode: null,
      })
  
      const agentEvidence = {
        jenkins: {
          ...evidence,
          console_log: (evidence.console_log || '').slice(-12000),
        },
        github: {
          ...github,
          files: github.files?.slice(0, 40),
        },
        metrics: metrics.map((metric) => ({
          ...metric,
          results: (metric.results || []).slice(0, 5),
        })),
      }
  
      const debugId =
        typeof crypto !== 'undefined' && crypto.randomUUID
          ? crypto.randomUUID()
          : `investigate-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
  
      const debugStartedAt = performance.now()
  
      const debugLog = (step, details = {}) => {
        console.log(`[INVESTIGATION_TRACE] ${step}`, {
          requestId: debugId,
          timestamp: new Date().toISOString(),
          elapsedMs: Math.round(performance.now() - debugStartedAt),
          ...details,
        })
      }
  
      try {
        debugLog('STEP 1: Investigation handler entered', { jobName: job.name, buildNumber })
  
        const [githubOwner, githubRepoName] = (selectedGithubRepo || '').split('/')
  
        const payload = {
          job_name: job.name,
          build_number: buildNumber,
          evidence: agentEvidence,
          github_token: githubToken || '',
          github_owner: githubOwner || '',
          github_repo: githubRepoName || '',
        }
  
        debugLog('STEP 2: Payload prepared', {
          jobName: job.name,
          buildNumber,
          evidenceKeys: agentEvidence && typeof agentEvidence === 'object' ? Object.keys(agentEvidence) : [],
          hasGithubToken: Boolean(githubToken),
          githubOwner: githubOwner || '(empty)',
          githubRepo: githubRepoName || '(empty)',
        })
  
        const requestBody = JSON.stringify(payload)
        const payloadBytes = new TextEncoder().encode(requestBody).length
  
        debugLog('STEP 3: Payload serialized', { payloadBytes })
  
        const url = `${AGENT_API_URL}/api/investigate/stream`
  
        debugLog('STEP 4: Starting stream request', { url })
  
        const response = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Request-ID': debugId,
          },
          body: requestBody,
        })
  
        debugLog('STEP 5: Response headers received', {
          status: response.status,
          statusText: response.statusText,
          ok: response.ok,
          contentType: response.headers.get('content-type'),
        })
  
        if (!response.ok || !response.body) {
          const text = await response.text().catch(() => '')
          throw new Error(`Agent API failed to start stream (HTTP ${response.status}): ${text.slice(0, 300)}`)
        }
  
        debugLog('STEP 6: Reading event stream')
  
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        let finalPayload = null
  
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
  
          let boundary
          while ((boundary = buffer.indexOf('\n\n')) !== -1) {
            const rawEvent = buffer.slice(0, boundary)
            buffer = buffer.slice(boundary + 2)
            const line = rawEvent.replace(/^data:\s*/, '')
            if (!line.trim()) continue
  
            let event
            try {
              event = JSON.parse(line)
            } catch (parseError) {
              debugLog('STEP 6 WARNING: Could not parse SSE event', { line: line.slice(0, 200) })
              continue
            }
  
            debugLog(`SSE event: ${event.type}`, event)
  
            if (event.type === 'node_complete') {
              setInvestigation((current) => current ? {
                ...current,
                currentNode: event.node,
                nodeProgress: [...(current.nodeProgress || []), { node: event.node, elapsedMs: event.elapsed_ms }],
              } : current)
            } else if (event.type === 'complete') {
              finalPayload = event.data
            } else if (event.type === 'error') {
              throw new Error(event.message || 'Agent workflow failed.')
            }
          }
        }
  
        if (!finalPayload) {
          throw new Error('Stream ended without a completed result.')
        }
  
        debugLog('STEP 7: Stream complete', {
          incidentId: finalPayload.incident_id,
          decisionStatus: finalPayload.status,
          stepsCount: Array.isArray(finalPayload.steps) ? finalPayload.steps.length : 0,
        })
  
        setActiveInvestigationStage(2)
  
        setInvestigation((current) => {
          if (!current || current.jobName !== job.name || current.buildNumber !== buildNumber) {
            debugLog('STEP 8: State update skipped; investigation mismatch', {
              currentJobName: current?.jobName,
              currentBuildNumber: current?.buildNumber,
            })
            return current
          }
  
          return {
            ...current,
            agentStatus: 'complete',
            incidentId: finalPayload.incident_id,
            steps: finalPayload.steps || [],
            decisionStatus: finalPayload.status,
            agentReports: {
              unified: finalPayload.unified_report,
              monitoring: finalPayload.monitoring_report,
              diagnosis: finalPayload.diagnosis_report,
              remediation: finalPayload.remediation_report,
              confidence: finalPayload.agent_confidence || {},
              confidenceSource: finalPayload.confidence_source || {},
              overallConfidence: finalPayload.overall_confidence ?? null,
              historicalIncident: finalPayload.historical_incident ?? null,
              proposedSteps: finalPayload.steps || [],
              judgeVerdict: finalPayload.judge_verdict || '',
              judgeScores: finalPayload.judge_scores || {},
              judgeHistoricalMatchQuality: finalPayload.judge_historical_match_quality ?? null,
              judgeHallucinationDetected: finalPayload.judge_hallucination_detected ?? null,
              judgeIssues: finalPayload.judge_issues || [],
              judgeOverallScore: finalPayload.judge_overall_score ?? null,
            },
            agentError: '',
          }
        })
  
        debugLog('STEP 9: Investigation response processed successfully')
      } catch (agentError) {
        debugLog('INVESTIGATION ERROR', {
          name: agentError?.name,
          message: agentError?.message,
          stack: agentError?.stack,
        })
  
        setActiveInvestigationStage(2)
  
        setInvestigation((current) => {
          if (!current || current.jobName !== job.name || current.buildNumber !== buildNumber) {
            debugLog('ERROR STATE UPDATE SKIPPED: Investigation mismatch')
            return current
          }
  
          return {
            ...current,
            agentStatus: 'error',
            agentError: agentError?.message || 'Agent investigation failed.',
          }
        })
      }
    } catch (error) {
      // This catch belongs to the outer try that retrieves Jenkins evidence
      // and gathers GitHub/Prometheus data.
      console.error('[INVESTIGATION_TRACE] Build evidence preparation failed', {
        jobName: job.name,
        buildNumber,
        name: error?.name,
        message: error?.message,
        timestamp: new Date().toISOString(),
      })
  
      setActiveInvestigationStage(2)
  
      setInvestigation((current) => {
        if (!current) return current
        return {
          ...current,
          agentStatus: 'error',
          agentError: error?.message || "Could not retrieve this build's evidence.",
        }
      })
    } finally {
      // Always release the loading state, including when Jenkins or
      // Prometheus fails before the agent request starts.
      setIsInvestigating(false)
    }
  }

  async function submitFeedback(feedback) {

    if (!investigation?.incidentId) return

    const response = await fetch(
      `${AGENT_API_URL}/api/incidents/${investigation.incidentId}/feedback`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(feedback),
      },
    )
    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(data.detail || `Feedback failed (HTTP ${response.status})`)
    }
    setInvestigation((current) => (current ? {
      ...current,
      feedbackOpen: false,
      feedbackSaved: true,
    } : current))
  }

  async function submitDecision(decision) {
    if (!investigation?.incidentId) return

    setInvestigation((current) => (current ? { ...current, isDeciding: true } : current))

    try {
      const response = await fetch(`${AGENT_API_URL}/api/investigate/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ incident_id: investigation.incidentId, decision }),
      })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.detail || `Decision failed (HTTP ${response.status})`)
      }
      setInvestigation((current) => (current ? {
        ...current,
        isDeciding: false,
        decisionStatus: data.status,
        fixResult: data.fix_result,
        decisionMessage: data.message,
        feedbackOpen: Boolean(data.fix_result?.applied),
      } : current))
    } catch (error) {
      setInvestigation((current) => (current ? {
        ...current,
        isDeciding: false,
        decisionMessage: error instanceof TypeError
          ? 'Cannot reach the PipeGuard backend. Check that it is running on port 8000.'
          : (error.message || 'Could not submit the decision.'),
      } : current))
    }
  }

  const selectedTool = integrations.find(
    (integration) => integration.id === selectedIntegration,
  )

  const fetchJenkinsJobs = useCallback(async (credentials, quiet = false) => {
    if (!credentials) return

    if (!quiet) setIsRefreshing(true)

    try {
      const response = await fetch(`${API_URL}/api/jenkins/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(credentials),
      })

      const data = await response.json()

      if (!response.ok) {
        throw new Error(data.detail || `Jenkins returned HTTP ${response.status}`)
      }

      setJenkinsJobs(data.jobs || [])
      setLastRefresh(new Date())
      setConnectionStatus('connected')
      setConnectionMessage('')
      setPageError('')
    } catch (error) {
      setConnectionMessage(error.message || 'Could not refresh Jenkins data.')
      if (!quiet) setPageError(error.message || 'Could not refresh Jenkins data.')
    } finally {
      if (!quiet) setIsRefreshing(false)
    }
  }, [])

  // Once connected, retrieve current Jenkins data every 10 seconds.
  useEffect(() => {
    if (!jenkinsCredentials) return undefined

    const timer = window.setInterval(() => {
      fetchJenkinsJobs(jenkinsCredentials, true)
    }, 10000)

    return () => window.clearInterval(timer)
  }, [jenkinsCredentials, fetchJenkinsJobs])

  function openConfiguration(integration) {
    if (!['jenkins', 'github', 'prometheus'].includes(integration.id)) {
      setPageError(`${integration.name} integration is not implemented yet.`)
      return
    }

    setPageError('')
    setConnectionMessage('')
    setGithubConnectionMessage('')
    setSelectedIntegration(integration.id)
    setFormValues(
      integration.id === 'jenkins'
        ? {
            url: 'http://192.168.56.10:8080',
            username: '',
            token: '',
          }
        : integration.id === 'prometheus'
          ? { url: prometheusUrl || 'http://192.168.56.10:9090' }
          : { token: '' },
    )
  }

  async function saveConfiguration(event) {
    event.preventDefault()

    if (selectedIntegration === 'prometheus') {
      const url = (formValues.url || '').trim()

      setIsConnecting(true)
      setPrometheusConnectionStatus('connecting')
      setPrometheusConnectionMessage('Testing Prometheus and retrieving active targets...')

      try {
        const testResponse = await fetch(`${API_URL}/api/prometheus/test`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        })
        const testData = await testResponse.json()

        if (!testResponse.ok) {
          throw new Error(testData.detail || `Prometheus connection failed (HTTP ${testResponse.status})`)
        }

        const targetsResponse = await fetch(`${API_URL}/api/prometheus/targets`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        })
        const targetsData = await targetsResponse.json()

        if (!targetsResponse.ok) {
          throw new Error(targetsData.detail || `Could not retrieve Prometheus targets (HTTP ${targetsResponse.status})`)
        }

        const targets = targetsData.targets || []
        setPrometheusUrl(url)
        setPrometheusTargets(targets)
        setPrometheusSummary(testData)
        setIntegrations((previous) =>
          previous.map((integration) =>
            integration.id === 'prometheus'
              ? { ...integration, connected: true }
              : integration,
          ),
        )
        setPrometheusConnectionStatus('connected')
        setPrometheusConnectionMessage(
          `Connected to Prometheus ${testData.version}. ${testData.targets_healthy} of ${testData.targets_total} targets healthy; ${targets.length} active targets retrieved.`,
        )
      } catch (error) {
        setPrometheusConnectionStatus('disconnected')
        setPrometheusConnectionMessage(error.message || 'Could not connect to Prometheus.')
      } finally {
        setIsConnecting(false)
      }
      return
    }

    if (selectedIntegration === 'github') {
      const token = (formValues.token || '').trim()

      setIsConnecting(true)
      setGithubConnectionStatus('connecting')
      setGithubConnectionMessage('Testing GitHub and loading repositories...')
      setGithubDataError('')

      try {
        const testResponse = await fetch(`${API_URL}/api/github/test`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token }),
        })
        const testData = await testResponse.json()

        if (!testResponse.ok) {
          throw new Error(testData.detail || `GitHub connection failed (HTTP ${testResponse.status})`)
        }

        const repoResponse = await fetch(`${API_URL}/api/github/repositories`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token }),
        })
        const repoData = await repoResponse.json()

        if (!repoResponse.ok) {
          throw new Error(repoData.detail || `Could not load repositories (HTTP ${repoResponse.status})`)
        }

        const repositories = repoData.repositories || []
        setGithubToken(token)
        setGithubRepositories(repositories)
        setSelectedGithubRepo(repositories[0]?.full_name || '')
        setIntegrations((previous) =>
          previous.map((integration) =>
            integration.id === 'github'
              ? { ...integration, connected: true }
              : integration,
          ),
        )
        setGithubConnectionStatus('connected')
        setGithubConnectionMessage(
          `Connected as ${testData.username}. Loaded ${repositories.length} repositories.`,
        )

        if (repositories[0]?.full_name) {
          await fetchGitHubCommits(token, repositories[0].full_name)
        } else {
          setGithubCommits([])
        }

        setSelectedIntegration(null)
      } catch (error) {
        setGithubConnectionStatus('disconnected')
        setGithubConnectionMessage(
          error.message || 'Could not connect to GitHub.',
        )
      } finally {
        setIsConnecting(false)
      }
      return
    }

    if (selectedIntegration !== 'jenkins') return

    const credentials = {
      url: formValues.url.trim(),
      username: formValues.username.trim(),
      api_token: formValues.token,
    }

    setIsConnecting(true)
    setConnectionStatus('connecting')
    setConnectionMessage('Testing the Jenkins connection...')
    setPageError('')

    try {
      const response = await fetch(`${API_URL}/api/jenkins/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(credentials),
      })

      const data = await response.json()

      if (!response.ok) {
        throw new Error(data.detail || `Connection failed (HTTP ${response.status})`)
      }

      // Keep credentials in memory only; never write them to localStorage.
      setJenkinsCredentials(credentials)
      setIntegrations((previous) =>
        previous.map((integration) =>
          integration.id === 'jenkins'
            ? { ...integration, connected: true }
            : integration,
        ),
      )
      setConnectionStatus('connected')
      setConnectionMessage(`Connected to ${data.server_name || 'Jenkins'}.`)
      setSelectedIntegration(null)

      // Immediately load actual jobs after a successful connection test.
      await fetchJenkinsJobs(credentials)
    } catch (error) {
      setConnectionStatus('disconnected')
      setConnectionMessage(error.message || 'Could not connect to Jenkins.')
    } finally {
      setIsConnecting(false)
    }
  }

  async function fetchGitHubCommits(token, fullName) {
    if (!token || !fullName) {
      setGithubCommits([])
      return
    }

    const [owner, repository] = fullName.split('/')
    if (!owner || !repository) return

    setIsLoadingGithubData(true)
    setGithubDataError('')

    try {
      const response = await fetch(`${API_URL}/api/github/commits`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          token,
          owner,
          repository,
          per_page: 10,
        }),
      })
      const data = await response.json()

      if (!response.ok) {
        throw new Error(data.detail || `Could not load commits (HTTP ${response.status})`)
      }

      setGithubCommits(data.commits || [])
    } catch (error) {
      setGithubCommits([])
      setGithubDataError(error.message || 'Could not load commit history.')
    } finally {
      setIsLoadingGithubData(false)
    }
  }

  async function refreshNow() {
    if (!jenkinsCredentials) {
      setPageError('Connect Jenkins first to retrieve real pipeline data.')
      return
    }

    await fetchJenkinsJobs(jenkinsCredentials)
  }

  const connectedCount = integrations.filter(
    (integration) => integration.connected,
  ).length

  const successfulJobs = jenkinsJobs.filter(
    (job) => getBuildStatus(job) === 'SUCCESS',
  ).length

  const failedJobs = jenkinsJobs.filter(
    (job) => getBuildStatus(job) === 'FAILURE',
  ).length

  const pageTitle = {
    dashboard: 'Dashboard',
    pipelines: 'Pipelines',
    incidents: 'Incidents',
    settings: 'Settings',
  }[activePage] || 'Dashboard'

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#dashboard"
          onClick={(event) => {
            event.preventDefault()
            setActivePage('dashboard')
          }}
        >
          <span className="brand-mark">P</span>
          <span>
            <strong>pipeguard</strong>
            <small>DevOps intelligence</small>
          </span>
        </a>

        <div className="workspace-label">WORKSPACE</div>
        <div className="workspace-switcher">
          <span className="workspace-avatar">P</span>
          <span className="workspace-name">
            <strong>Personal workspace</strong>
            <small>Local development</small>
          </span>
          <span className="chevron">⌄</span>
        </div>

        <div className="nav-label">PLATFORM</div>
        <nav className="navigation">
          {navigation.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${activePage === item.id ? 'active' : ''}`}
              onClick={() => {
                setActivePage(item.id)
                setPageError('')
              }}
            >
              <span className="nav-icon">{item.icon}</span>
              {item.label}
              {item.id === 'incidents' && (
                <span className="nav-count">{failedJobs}</span>
              )}
            </button>
          ))}
        </nav>

        <div className="nav-label settings-label">CONFIGURATION</div>
        <button
          className={`nav-item ${activePage === 'settings' ? 'active' : ''}`}
          onClick={() => {
            setActivePage('settings')
            setPageError('')
          }}
        >
          <span className="nav-icon">⚙</span>
          Settings
        </button>

        <div className="sidebar-bottom">
          <div className="help-card">
            <span className="help-icon">✳</span>
            <strong>Build with confidence</strong>
            <p>Connect real provider data to start monitoring your pipelines.</p>
            <button onClick={() => setActivePage('settings')}>
              Configure tools <span>→</span>
            </button>
          </div>
          <div className="user-profile">
            <div className="user-avatar">AO</div>
            <div className="user-details">
              <strong>Workspace owner</strong>
              <small>Administrator</small>
            </div>
            <span className="profile-menu">•••</span>
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb">
            <span>Workspace</span>
            <span className="breadcrumb-divider">/</span>
            <strong>{pageTitle}</strong>
          </div>
          <div className="topbar-right">
            <span className="environment-badge">
              <span className={`status-dot ${connectionStatus === 'connected' ? 'green' : 'gray'}`} />
              {connectionStatus === 'connected'
                ? 'Jenkins connected'
                : connectionStatus === 'connecting'
                  ? 'Connecting...'
                  : 'Not connected'}
            </span>
            <button
              className="icon-button"
              aria-label="Refresh Jenkins data"
              title="Refresh Jenkins data"
              onClick={refreshNow}
              disabled={isRefreshing || !jenkinsCredentials}
            >
              {isRefreshing ? '…' : '↻'}
            </button>
            <div className="topbar-avatar">AO</div>
          </div>
        </header>

        <div className="page-content">
          {pageError && (
            <div className="error-banner" role="alert">
              <strong>Notice:</strong> {pageError}
              <button onClick={() => setPageError('')} aria-label="Dismiss notice">×</button>
            </div>
          )}

          {activePage === 'dashboard' && (
            <>
              <section className="page-heading">
                <div>
                  <div className="eyebrow">LIVE OVERVIEW</div>
                  <h1>Your operations<span className="heading-period">.</span></h1>
                  <p className="page-subtitle">
                    Real Jenkins jobs and build statuses from your environment.
                  </p>
                </div>
                <button className="primary-button" onClick={() => setActivePage('settings')}>
                  <span>＋</span> Connect a tool
                </button>
              </section>

              <section className="welcome-banner">
                <div className="banner-copy">
                  <div className="banner-kicker">
                    <span className="banner-spark">✳</span> PIPEGUARD INTELLIGENCE
                  </div>
                  <h2>{connectionStatus === 'connected' ? 'Your Jenkins data.' : 'Your pipelines.'}<br />
                    {connectionStatus === 'connected' ? 'Live in PipeGuard.' : 'Under control.'}
                  </h2>
                  <p>
                    {connectionStatus === 'connected'
                      ? 'PipeGuard is retrieving actual job data from your Jenkins server and refreshing it every 10 seconds.'
                      : 'Connect Jenkins to retrieve real jobs, build numbers, statuses and durations.'}
                  </p>
                  <button className="banner-button" onClick={refreshNow}>
                    Refresh live data <span>↻</span>
                  </button>
                </div>
                <div className="banner-visual" aria-hidden="true">
                  <div className="orbit orbit-one" />
                  <div className="orbit orbit-two" />
                  <div className="orbit orbit-three" />
                  <div className="visual-core"><span>✳</span></div>
                  <div className="visual-node node-one">✓</div>
                  <div className="visual-node node-two">⌘</div>
                  <div className="visual-node node-three">↗</div>
                  <div className="visual-line line-one" />
                  <div className="visual-line line-two" />
                </div>
              </section>

              <section className="metrics-grid">
                <MetricCard
                  label="Connected tools"
                  value={`${connectedCount} / ${integrations.length}`}
                  note="Real backend connection"
                  icon="⌘"
                  tone="purple"
                />
                <MetricCard
                  label="Jenkins jobs"
                  value={jenkinsCredentials ? String(jenkinsJobs.length) : '—'}
                  note={jenkinsCredentials ? 'Retrieved from Jenkins' : 'Connect Jenkins first'}
                  icon="⑂"
                  tone="blue"
                />
                <MetricCard
                  label="Successful latest builds"
                  value={jenkinsCredentials ? String(successfulJobs) : '—'}
                  note={jenkinsCredentials ? 'Latest build result per job' : 'Awaiting live data'}
                  icon="✓"
                  tone="green"
                />
                <MetricCard
                  label="Failed latest builds"
                  value={jenkinsCredentials ? String(failedJobs) : '—'}
                  note="Not the same as detected incidents"
                  icon="!"
                  tone="orange"
                />
              </section>

              <section className="prometheus-panel">
      <div className="prometheus-panel-heading">
        <div>
          <div className="eyebrow">MONITORING</div>
          <h2>Prometheus targets</h2>
          <p>Live target health retrieved from your connected Prometheus server.</p>
        </div>
        <span className={`prometheus-state ${prometheusConnectionStatus === 'connected' ? 'is-connected' : ''}`}>
          <span className="status-dot" />
          {prometheusConnectionStatus === 'connected' ? 'Connected' : 'Not connected'}
        </span>
      </div>

      {prometheusSummary && (
        <div className="prometheus-summary">
          <div>
            <span>Total targets</span>
            <strong>{prometheusSummary.targets_total ?? prometheusTargets.length}</strong>
          </div>
          <div>
            <span>Healthy</span>
            <strong>{prometheusSummary.targets_healthy ?? 0}</strong>
          </div>
          <div>
            <span>Unhealthy</span>
            <strong>{prometheusSummary.targets_unhealthy ?? 0}</strong>
          </div>
          <div>
            <span>Prometheus version</span>
            <strong>{prometheusSummary.version || '—'}</strong>
          </div>
        </div>
      )}

      {prometheusTargets.length > 0 ? (
        <div className="prometheus-table-wrap">
          <table className="prometheus-table">
            <thead>
              <tr>
                <th>Job</th>
                <th>Instance</th>
                <th>Health</th>
                <th>Last scrape</th>
                <th>Scrape URL</th>
              </tr>
            </thead>
            <tbody>
              {prometheusTargets.map((target, index) => (
                <tr key={`${target.job}-${target.instance}-${index}`}>
                  <td><strong>{target.job || '—'}</strong></td>
                  <td>{target.instance || '—'}</td>
                  <td>
                    <span className={`prometheus-health ${target.health === 'up' ? 'health-up' : 'health-down'}`}>
                      <span className="status-dot" />
                      {target.health || 'unknown'}
                    </span>
                    {target.last_error && (
                      <div className="prometheus-target-error">{target.last_error}</div>
                    )}
                  </td>
                  <td>{target.last_scrape ? new Date(target.last_scrape).toLocaleString() : '—'}</td>
                  <td className="prometheus-scrape-url">{target.scrape_url || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="prometheus-empty">
          <strong>
            {prometheusConnectionStatus === 'connecting'
              ? 'Connecting to Prometheus…'
              : 'No Prometheus targets loaded yet'}
          </strong>
          <p>
            {prometheusConnectionMessage ||
              'Connect Prometheus in Settings to retrieve real target health and scrape information.'}
          </p>
          <button className="primary-button" onClick={() => setActivePage('settings')}>
            Configure Prometheus <span>→</span>
          </button>
        </div>
      )}
    </section>

    <section className="section-heading">
                <div>
                  <h2>Jenkins jobs</h2>
                  <p>Latest build information retrieved from your actual Jenkins server.</p>
                </div>
                <button className="text-button" onClick={() => setActivePage('pipelines')}>
                  View all jobs <span>→</span>
                </button>
              </section>

              {jenkinsCredentials ? (
                <JobsTable jobs={jenkinsJobs} onInvestigate={investigateBuild} investigationInProgress={isInvestigating} />
              ) : (
                <div className="empty-state">
                  <div className="empty-state-icon">⑂</div>
                  <strong>Jenkins is not connected</strong>
                  <p>Connect Jenkins to replace placeholders with your real jobs.</p>
                  <button className="primary-button" onClick={() => openConfiguration(integrations[0])}>
                    Connect Jenkins <span>→</span>
                  </button>
                </div>
              )}

              <section className="section-heading github-section-heading">
                <div>
                  <h2>GitHub repositories</h2>
                  <p>Source-code activity retrieved from your connected GitHub account.</p>
                </div>
                {!githubToken && (
                  <button
                    className="text-button"
                    onClick={() => openConfiguration(integrations.find((item) => item.id === 'github'))}
                  >
                    Connect GitHub <span>→</span>
                  </button>
                )}
              </section>

              {githubToken ? (
                <section className="github-browser">
                  <div className="github-browser-toolbar">
                    <label htmlFor="github-repository">Repository</label>
                    <select
                      id="github-repository"
                      className="github-repo-select"
                      value={selectedGithubRepo}
                      onChange={(event) => {
                        const fullName = event.target.value
                        setSelectedGithubRepo(fullName)
                        fetchGitHubCommits(githubToken, fullName)
                      }}
                    >
                      {githubRepositories.map((repo) => (
                        <option key={repo.id} value={repo.full_name}>
                          {repo.full_name}{repo.private ? ' · private' : ''}
                        </option>
                      ))}
                    </select>
                    <span className="github-repo-count">
                      {githubRepositories.length} repositories
                    </span>
                  </div>

                  {githubDataError && (
                    <div className="error-banner" role="alert">{githubDataError}</div>
                  )}

                  {selectedGithubRepo && (
                    <div className="github-repository-summary">
                      <div>
                        <strong>{selectedGithubRepo}</strong>
                        <p>
                          {githubRepositories.find((repo) => repo.full_name === selectedGithubRepo)?.description
                            || 'No repository description provided.'}
                        </p>
                      </div>
                      <a
                        className="job-link"
                        href={githubRepositories.find((repo) => repo.full_name === selectedGithubRepo)?.html_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Open repository ↗
                      </a>
                    </div>
                  )}

                  <div className="section-heading github-commits-heading">
                    <div>
                      <h2>Recent commits</h2>
                      <p>Latest commit messages and authors from the selected repository.</p>
                    </div>
                    {isLoadingGithubData && <span className="data-caption">Loading…</span>}
                  </div>

                  {githubCommits.length ? (
                    <div className="jobs-table-wrap">
                      <table className="jobs-table">
                        <thead>
                          <tr>
                            <th>Commit message</th>
                            <th>Author</th>
                            <th>Date</th>
                            <th>Commit</th>
                          </tr>
                        </thead>
                        <tbody>
                          {githubCommits.map((commit) => (
                            <tr key={commit.sha}>
                              <td>
                                <strong>{(commit.message || 'No commit message').split('\n')[0]}</strong>
                                <span className="job-subtitle">{commit.sha?.slice(0, 7)}</span>
                              </td>
                              <td>{commit.author || 'Unknown'}</td>
                              <td>{formatTimestamp(commit.date)}</td>
                              <td>
                                <a className="job-link" href={commit.html_url} target="_blank" rel="noreferrer">
                                  View ↗
                                </a>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="empty-state">
                      <strong>{isLoadingGithubData ? 'Loading commit history…' : 'No commits to display'}</strong>
                      <p>
                        {selectedGithubRepo
                          ? 'No commits were returned for this repository, or the request is still loading.'
                          : 'Select a repository to inspect its commit history.'}
                      </p>
                    </div>
                  )}
                </section>
              ) : (
                <div className="empty-state github-empty-state">
                  <strong>GitHub is not connected</strong>
                  <p>Connect GitHub to browse your real repositories and recent commits.</p>
                </div>
              )}

              <div className="refresh-footer">
                <span className={`status-dot ${connectionStatus === 'connected' ? 'green' : 'gray'}`} />
                {connectionStatus === 'connected'
                  ? `Connected · ${lastRefresh ? `Last refreshed ${lastRefresh.toLocaleTimeString()}` : 'Loading data'} · Auto-refresh every 10 seconds`
                  : connectionMessage || 'Connect Jenkins to start retrieving live data.'}
                <button className="text-button" onClick={refreshNow} disabled={isRefreshing || !jenkinsCredentials}>
                  {isRefreshing ? 'Refreshing…' : 'Refresh now'}
                </button>
              </div>
            </>
          )}

          {activePage === 'settings' && (
            <>
              <section className="page-heading">
                <div>
                  <div className="eyebrow">WORKSPACE</div>
                  <h1>Integrations<span className="heading-period">.</span></h1>
                  <p className="page-subtitle">Connect provider APIs to PipeGuard.</p>
                </div>
              </section>
              <IntegrationGrid integrations={integrations} onConfigure={openConfiguration} />
              <div className="notice-box">
                <span>ⓘ</span>
                <p>
                  <strong>Jenkins and GitHub have real backend connectors.</strong> Prometheus
                  is the next integration to implement.
                </p>
              </div>
            </>
          )}

          {activePage === 'pipelines' && (
            <>
              <section className="page-heading">
                <div>
                  <div className="eyebrow">CI/CD</div>
                  <h1>Pipelines<span className="heading-period">.</span></h1>
                  <p className="page-subtitle">Actual Jenkins jobs and their latest builds.</p>
                </div>
                <button className="primary-button" onClick={refreshNow} disabled={isRefreshing || !jenkinsCredentials}>
                  {isRefreshing ? 'Refreshing…' : '↻ Refresh jobs'}
                </button>
              </section>
              {jenkinsCredentials
                ? <JobsTable jobs={jenkinsJobs} onInvestigate={investigateBuild} investigationInProgress={isInvestigating} />
                : <div className="empty-state">
                    <strong>No provider data yet</strong>
                    <p>Connect Jenkins to load jobs from your server.</p>
                    <button className="primary-button" onClick={() => openConfiguration(integrations[0])}>Connect Jenkins</button>
                  </div>}
              {lastRefresh && <p className="data-caption">Last refreshed: {lastRefresh.toLocaleString()}</p>}
            </>
          )}

          {activePage === 'incidents' && (
            <section className="placeholder-page">
              <div className="placeholder-icon">◈</div>
              <h1>Incidents</h1>
              <p>
                {failedJobs > 0
                  ? `${failedJobs} job(s) currently report a failed latest build. Incident detection and diagnosis are not implemented yet.`
                  : 'No incident records have been created. Detecting failures and running agent investigations is the next milestone.'}
              </p>
              <p className="data-caption">A failed build is a signal, not yet a confirmed incident or root cause.</p>
            </section>
          )}
        </div>
      </main>

      {investigation && !isInvestigationModalOpen && (
        <button
          className="reopen-investigation-button"
          onClick={() => setIsInvestigationModalOpen(true)}
        >
          <span aria-hidden="true">↗</span> Reopen last investigation
        </button>
      )}

      {investigation?.feedbackOpen && (
        <FixFeedbackModal
          investigation={investigation}
          onClose={() => setInvestigation((current) => (
            current ? { ...current, feedbackOpen: false } : current
          ))}
          onSubmit={submitFeedback}
        />
      )}

      {isInvestigationModalOpen && investigation && (
        <div
          className="modal-backdrop investigation-modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !isInvestigating) {
              setIsInvestigationModalOpen(false)
            }
          }}
        >
          <section
            className="investigation-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="investigation-modal-title"
          >
            <header className="investigation-modal-header">
              <div>
                <div className="modal-eyebrow">PIPEGUARD · BUILD INVESTIGATION</div>
                <h2 id="investigation-modal-title">
                  {investigation.jobName} #{investigation.buildNumber}
                </h2>
                <div className="investigation-modal-meta">
                  <p>Live provider evidence and agent-generated analysis.</p>
                  {investigation.evidence && (
                    <span className={`build-status investigation-modal-status ${statusClass(investigation.evidence.result || 'RUNNING')}`}>
                      {investigation.evidence.result || 'RUNNING'}
                    </span>
                  )}
                </div>
              </div>
              <button
                className="modal-close"
                onClick={() => setIsInvestigationModalOpen(false)}
                aria-label="Close investigation"
                title="Close investigation"
              >×</button>
            </header>
            <div className="investigation-modal-body">
              <BuildInvestigation
                investigation={investigation}
                isLoading={isInvestigating}
                activeStage={activeInvestigationStage}
                onStageChange={setActiveInvestigationStage}
                onDecision={submitDecision}
                onOpenFeedback={() => setInvestigation((current) => (
                  current ? { ...current, feedbackOpen: true } : current
                ))}
              />
            </div>
          </section>
        </div>
      )}

      {selectedTool && (
        <div
          className="modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !isConnecting) {
              setSelectedIntegration(null)
            }
          }}
        >
          <section className="configuration-modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
            <div className="modal-header">
              <div className={`integration-icon ${selectedTool.color}`}>{selectedTool.icon}</div>
              <button className="modal-close" onClick={() => !isConnecting && setSelectedIntegration(null)} aria-label="Close">×</button>
            </div>
            <div className="modal-eyebrow">REAL PROVIDER CONNECTION</div>
            <h2 id="modal-title">Connect {selectedTool.name}</h2>
            <p className="modal-description">{selectedTool.description}</p>

            <form onSubmit={saveConfiguration}>
              {selectedTool.fields.map((field) => (
                <label className="form-field" key={field.name}>
                  <span>{field.label}</span>
                  <input
                    type={field.secret ? 'password' : 'text'}
                    placeholder={field.placeholder}
                    value={formValues[field.name] || ''}
                    onChange={(event) => setFormValues((previous) => ({
                      ...previous,
                      [field.name]: event.target.value,
                    }))}
                    required
                    autoComplete={field.secret ? 'new-password' : 'off'}
                  />
                </label>
              ))}

              {(selectedIntegration === 'github'
                ? githubConnectionMessage
                : selectedIntegration === 'prometheus'
                  ? prometheusConnectionMessage
                  : connectionMessage) && (
                <div className={`form-feedback ${
                  (selectedIntegration === 'github'
                    ? githubConnectionStatus
                    : selectedIntegration === 'prometheus'
                      ? prometheusConnectionStatus
                      : connectionStatus) === 'connected'
                    ? 'feedback-success'
                    : 'feedback-error'
                }`}>
                  {selectedIntegration === 'github'
                    ? githubConnectionMessage
                    : selectedIntegration === 'prometheus'
                      ? prometheusConnectionMessage
                      : connectionMessage}
                </div>
              )}

              <div className="form-notice">
                <span>ⓘ</span>
                {selectedIntegration === 'prometheus'
                  ? 'PipeGuard queries the configured Prometheus HTTP API through its backend.'
                  : 'Credentials are sent to the local PipeGuard backend and kept in memory only for this session.'}
              </div>
              <div className="modal-actions">
                <button type="button" className="secondary-button" onClick={() => setSelectedIntegration(null)} disabled={isConnecting}>
                  Cancel
                </button>
                <button type="submit" className="primary-button" disabled={isConnecting}>
                  {isConnecting ? 'Testing connection…' : 'Test and connect'}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </div>
  )
}

function MetricCard({ label, value, note, icon, tone }) {
  return (
    <article className="metric-card">
      <div className={`metric-icon ${tone}`}>{icon}</div>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      <div className="metric-note">{note}</div>
    </article>
  )
}

function JobsTable({ jobs, onInvestigate, investigationInProgress }) {
  if (!jobs.length) {
    return (
      <div className="empty-state">
        <strong>No Jenkins jobs found</strong>
        <p>The connection worked, but Jenkins returned an empty jobs list.</p>
      </div>
    )
  }

  return (
    <div className="jobs-table-wrap">
      <table className="jobs-table">
        <thead>
          <tr>
            <th>Job</th>
            <th>Latest build</th>
            <th>Status</th>
            <th>Duration</th>
            <th>Last run</th>
            <th>Jenkins</th>
            <th>Evidence</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => {
            const status = getBuildStatus(job)
            return (
              <tr key={job.name}>
                <td>
                  <strong>{job.name}</strong>
                  <span className="job-subtitle">Jenkins job</span>
                </td>
                <td>{job.last_build ? `#${job.last_build.number}` : '—'}</td>
                <td><span className={`build-status ${statusClass(status)}`}>{status.replaceAll('_', ' ')}</span></td>
                <td>{formatDuration(job.last_build?.duration_ms)}</td>
                <td>{formatTimestamp(job.last_build?.timestamp)}</td>
                <td>
                  <a className="job-link" href={job.url} target="_blank" rel="noreferrer">
                    Open ↗
                  </a>
                </td>
                <td>
                  {status === 'FAILURE' && job.last_build?.number ? (
                    <button
                      className="investigate-button"
                      onClick={() => onInvestigate?.(job)}
                      disabled={!onInvestigate || investigationInProgress}
                    >
                      {investigationInProgress ? 'Investigation running…' : `Investigate #${job.last_build.number} →`}
                    </button>
                  ) : (
                    <span className="data-caption">Failure only</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}


const PROMETHEUS_METRIC_LABELS = {
  up: 'Target availability',
  node_load1: 'System load (1 minute)',
  node_memory_MemAvailable_bytes: 'Available system memory',
  'rate(node_cpu_seconds_total{mode!="idle"}[5m])': 'Non-idle CPU activity',
}

function getPrometheusMetricLabel(query) {
  return PROMETHEUS_METRIC_LABELS[query] || query
}

function getPrometheusMetricSummary(metric) {
  if (metric.status !== 'success') {
    return metric.message || 'Prometheus could not retrieve this metric.'
  }

  const series = metric.results || []
  if (!series.length) {
    return 'No samples were returned for this metric in the build time window.'
  }

  const latest = series.map((item) => {
    const points = Array.isArray(item.values) ? item.values : []
    const point = points[points.length - 1]
    return {
      labels: item.metric || {},
      value: point ? Number(point[1]) : NaN,
    }
  }).filter((item) => Number.isFinite(item.value))

  if (!latest.length) {
    return 'Series were returned, but no usable sample values were available.'
  }

  if (metric.query === 'up') {
    const upCount = latest.filter((item) => item.value === 1).length
    return `${upCount} of ${latest.length} targets reported UP in their latest returned sample. This indicates reachability, not build success.`
  }

  if (metric.query === 'node_load1') {
    const value = latest[0].value
    return `Latest returned 1-minute load average: ${value.toFixed(2)}. Interpret this relative to the host's CPU capacity and historical baseline.`
  }

  if (metric.query === 'node_memory_MemAvailable_bytes') {
    const mib = latest[0].value / (1024 * 1024)
    return `Latest returned available memory: ${mib.toFixed(0)} MiB. Low memory should be assessed against the host's capacity and baseline.`
  }

  if (metric.query.startsWith('rate(node_cpu_seconds_total')) {
    return `${series.length} CPU/mode time series returned. Inspect the samples over time; these values are not a single overall CPU-utilization percentage.`
  }

  return `${series.length} time series returned for this query.`
}

function getPrometheusMetricPreview(metric) {
  const preview = (metric.results || []).slice(0, 3).map((item) => ({
    metric: item.metric || {},
    values: (Array.isArray(item.values) ? item.values : []).slice(-3),
  }))
  return JSON.stringify(preview, null, 2)
}

function BuildInvestigation({ investigation, isLoading, activeStage, onStageChange, onDecision, onOpenFeedback }) {
  const [hiddenMetricQueries, setHiddenMetricQueries] = useState([])

  if (!investigation) return null

  const evidence = investigation.evidence
  const github = investigation.github

  return (
    <section className="investigation-panel" aria-live="polite">
      <div className="investigation-heading">
        <div>
          <div className="eyebrow">BUILD EVIDENCE</div>
          <h2>{investigation.jobName} #{investigation.buildNumber}</h2>
          <p>Evidence retrieved for this exact Jenkins build.</p>
        </div>
      </div>

      <nav className="investigation-stage-nav" aria-label="Investigation stages">
        {[
          { id: 0, number: '01', title: 'Collect evidence', description: 'Live provider data' },
          { id: 1, number: '02', title: 'Agent analysis', description: 'Monitoring → Diagnosis → Remediation' },
          { id: 2, number: '03', title: 'Final report', description: 'Findings and proposed fix' },
        ].map((stage) => {
          const completed = stage.id === 0
            ? Boolean(evidence)
            : stage.id === 1
              ? Boolean(investigation.agentReports)
              : investigation.agentStatus === 'complete'
          const disabled = !evidence || (stage.id === 2 && !investigation.agentReports && !investigation.agentError)
          return (
            <button
              key={stage.id}
              type="button"
              className={`investigation-stage-tab ${activeStage === stage.id ? 'is-active' : ''} ${completed ? 'is-complete' : ''}`}
              onClick={() => onStageChange(stage.id)}
              disabled={disabled}
              aria-current={activeStage === stage.id ? 'step' : undefined}
            >
              <span className="investigation-stage-number">{completed ? '✓' : stage.number}</span>
              <span className="investigation-stage-labels">
                <strong>{stage.title}</strong>
                <small>{stage.description}</small>
              </span>
            </button>
          )
        })}
      </nav>

      {isLoading && !evidence && (
        <p className="investigation-loading">
          Collecting evidence from Jenkins, GitHub and Prometheus…
        </p>
      )}
      {isLoading && evidence && investigation.agentStatus === 'running' && (
        <p className="investigation-loading">
          Live evidence collected. Running agents: Monitoring → Diagnosis → Remediation…
        </p>
      )}
      {investigation.error && (
        <div className="error-banner" role="alert">{investigation.error}</div>
      )}

      {activeStage === 0 && evidence && (
        <>
          <div className="investigation-summary">
            <div><span>Build</span><strong>#{evidence.build_number}</strong></div>
            <div><span>Duration</span><strong>{formatDuration(evidence.duration_ms)}</strong></div>
            <div><span>Started</span><strong>{formatTimestamp(evidence.timestamp)}</strong></div>
            <div>
              <span>Commit SHA</span>
              <strong className="investigation-sha">
                {github?.sha || evidence.commits?.find((item) => item.sha)?.sha ||
                  evidence.console_log?.match(/\b[0-9a-f]{40}\b/i)?.[0] || 'Not found'}
              </strong>
            </div>
          </div>

          <details className="investigation-section">
            <summary>Jenkins console log</summary>
            <p className="data-caption">
              Actual console output from build #{evidence.build_number}; {evidence.console_log?.length || 0} characters.
            </p>
            <pre className="investigation-log">{evidence.console_log || 'Jenkins returned an empty console log.'}</pre>
            {evidence.url && (
              <a className="job-link" href={evidence.url} target="_blank" rel="noreferrer">
                Open this exact build in Jenkins ↗
              </a>
            )}
          </details>

          <details className="investigation-section">
            <summary>GitHub commit and changed files</summary>
            {github?.status === 'success' ? (
              <>
                <h3>{github.message || 'Commit details'}</h3>
                <p className="data-caption">
                  {github.sha} · {github.author || 'Unknown author'} · {github.date || 'Date unavailable'}
                </p>
                {github.html_url && (
                  <a className="job-link" href={github.html_url} target="_blank" rel="noreferrer">
                    Open matching commit ↗
                  </a>
                )}
                {github.files?.length ? (
                  <div className="investigation-table-wrap">
                    <table className="prometheus-table">
                      <thead><tr><th>File</th><th>Status</th><th>Added</th><th>Deleted</th></tr></thead>
                      <tbody>
                        {github.files.map((file) => (
                          <tr key={file.filename}>
                            <td>{file.filename}</td>
                            <td>{file.status}</td>
                            <td>{file.additions}</td>
                            <td>{file.deletions}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : <p className="data-caption">GitHub returned no changed-file records for this commit.</p>}
              </>
            ) : (
              <p className="provider-message">
                {github?.message || 'GitHub evidence is not available yet.'}
                {github?.sha ? ` SHA: ${github.sha}` : ''}
              </p>
            )}
          </details>

          <details className="investigation-section">
            <summary>Prometheus metrics around the build</summary>
            <p className="data-caption">
              Real range queries from five minutes before build start through five minutes after build completion.
            </p>
            <div className="metric-query-controls">
              <span className="metric-query-controls-title">Metrics to display</span>
              {(investigation.metrics || []).map((metric) => (
                <label className="metric-query-toggle" key={metric.query}>
                  <input
                    type="checkbox"
                    checked={!hiddenMetricQueries.includes(metric.query)}
                    onChange={(event) => setHiddenMetricQueries((current) =>
                      event.target.checked
                        ? current.filter((query) => query !== metric.query)
                        : [...current, metric.query]
                    )}
                  />
                  <span>{getPrometheusMetricLabel(metric.query)}</span>
                </label>
              ))}
            </div>

            <div className="metric-query-grid">
              {(investigation.metrics || [])
                .filter((metric) => !hiddenMetricQueries.includes(metric.query))
                .map((metric) => {
                  const hasSamples = (metric.results || []).length > 0
                  const displayStatus =
                    metric.status === 'success' && !hasSamples
                      ? 'no samples'
                      : metric.status

                  return (
                    <article className="metric-query-card" key={metric.query}>
                      <div className="metric-query-card-heading">
                        <strong>{getPrometheusMetricLabel(metric.query)}</strong>
                        <span className={`provider-status ${displayStatus.replace(' ', '-')}`}>
                          {displayStatus}
                        </span>
                      </div>
                      <p className="metric-query-description">
                        {getPrometheusMetricSummary(metric)}
                      </p>
                      <div className="metric-query-meta">
                        <span>{metric.count ?? (metric.results || []).length} series</span>
                        <span>Build time window</span>
                      </div>
                      {hasSamples && (
                        <details className="metric-raw-details">
                          <summary>Inspect latest raw samples</summary>
                          <p className="data-caption">
                            Last three sample points from up to three returned series.
                          </p>
                          <pre>{getPrometheusMetricPreview(metric)}</pre>
                        </details>
                      )}
                    </article>
                  )
                })}
              {(investigation.metrics || []).every((metric) =>
                hiddenMetricQueries.includes(metric.query)
              ) && (
                <p className="data-caption">All metrics are hidden. Select a metric above to display it.</p>
              )}
            </div>
          </details>
        </>
      )}

      {activeStage > 0 && (investigation.agentStatus === 'running' ||
        investigation.agentReports ||
        investigation.agentError) && (
        <section className="agent-reports-section" aria-live="polite">
          <div className="agent-reports-heading">
            <div>
              <div className="eyebrow">PIPEGUARD INTELLIGENCE</div>
              <h3>Build investigation</h3>
              <p>
                Evidence-led analysis across monitoring, root-cause diagnosis,
                and human-reviewed remediation.
              </p>
            </div>
            <span className={`provider-status ${
              investigation.agentStatus === 'complete'
                ? 'success'
                : investigation.agentStatus === 'error'
                  ? 'error'
                  : 'loading'
            }`}>
              {investigation.agentStatus === 'complete'
                ? 'Analysis complete'
                : investigation.agentStatus === 'error'
                  ? 'Analysis failed'
                  : 'Analyzing evidence'}
            </span>
          </div>

          {activeStage === 1 && (
          <div className={`agent-workflow ${
            investigation.agentStatus === 'complete'
              ? 'is-complete'
              : investigation.agentStatus === 'error'
                ? 'has-error'
                : 'is-running'
          }`}>
            <div className="agent-workflow-topline">
              <span className="agent-workflow-indicator" />
              <span>
                {investigation.agentStatus === 'complete'
                  ? 'Three-stage workflow finished'
                  : investigation.agentStatus === 'error'
                    ? 'Workflow needs attention'
                    : 'Evidence submitted to the agent workflow'}
              </span>
              <span className="agent-workflow-build">
                {investigation.jobName} #{investigation.buildNumber}
              </span>
            </div>

            <div className="agent-workflow-track">
              {[
                { key: 'monitoring', name: 'Monitoring', detail: 'Collect and summarize evidence' },
                { key: 'diagnosis', name: 'Diagnosis', detail: 'Identify likely causes' },
                { key: 'remediation', name: 'Remediation', detail: 'Prepare a reviewable fix' },
                { key: 'structure_steps', name: 'Structuring', detail: 'Turn the proposal into steps' },
                { key: 'llm_judge', name: 'Audit', detail: 'Independent check against evidence' },
              ].map((stage, index, all) => {
                const completedEntry = (investigation.nodeProgress || []).find((p) => p.node === stage.key)
                const isCurrent = investigation.currentNode === stage.key && !completedEntry
                const isDone = Boolean(completedEntry) || investigation.agentStatus === 'complete'
                return (
                  <div
                    className={`agent-workflow-stage ${isDone ? 'stage-complete' : isCurrent ? 'stage-active' : ''}`}
                    key={stage.key}
                  >
                    <div className="agent-stage-marker">
                      {isDone ? '✓' : isCurrent ? '⏳' : String(index + 1).padStart(2, '0')}
                    </div>
                    <div className="agent-stage-copy">
                      <strong>{stage.name}</strong>
                      <span>
                        {stage.detail}
                        {completedEntry ? ` · ${completedEntry.elapsedMs}ms` : ''}
                      </span>
                    </div>
                    {index < all.length - 1 && <div className="agent-stage-connector" />}
                  </div>
                )
              })}
            </div>

            <p className="agent-workflow-footnote">
              {investigation.agentStatus === 'complete'
                ? 'Reports are ready. Review the diagnosis and proposed actions before making changes.'
                : investigation.agentStatus === 'error'
                  ? 'The agent workflow did not return a complete set of reports. Your collected build evidence remains available above.'
                  : investigation.currentNode
                    ? `Currently running: ${investigation.currentNode}…`
                    : 'Starting the agent workflow…'}
            </p>
          </div>
          )}

          {investigation.agentError && (
            <div className="error-banner" role="alert">
              Agent workflow failed: {investigation.agentError}
              <p>The live Jenkins, GitHub and Prometheus evidence above is still available.</p>
            </div>
          )}

          {activeStage === 2 && investigation.agentReports && (
            <div className="pg-report">
              <header className="pg-report-header">
                <div>
                  <div className="eyebrow">INCIDENT INVESTIGATION</div>
                  <h3>{investigation.jobName} <span>#{investigation.buildNumber}</span></h3>
                  <p>Evidence, likely cause, and proposed actions for your review.</p>
                </div>
                <span className="pg-report-status">
                  {investigation.decisionStatus === 'approved_fixed'
                    ? (investigation.fixResult?.applied ? 'Fix applied' : 'Fix attempted')
                    : investigation.decisionStatus === 'denied'
                      ? 'Approval denied'
                      : investigation.decisionStatus === 'awaiting_approval'
                        ? 'Awaiting approval'
                        : 'Report ready'}
                </span>
              </header>

              {(() => {
                const reports = investigation.agentReports
                const confidence = reports.confidence || {}
                const score = (key) => {
                  const value = confidence[key] ?? confidence[key[0].toUpperCase() + key.slice(1)]
                  return typeof value === 'number' && Number.isFinite(value)
                    ? `${value}%`
                    : 'N/A'
                }
                const steps = reports.proposedSteps || investigation.steps || []
                const historical = reports.historicalIncident
                const hasReport = (value) => typeof value === 'string' && value.trim().length > 0
                const hist = (camel, snake) => historical?.[camel] ?? historical?.[snake]
                
                

                return (
                  <>
                    <section className="pg-confidence-panel" aria-label="Agent confidence">
                      <div className="pg-section-heading">
                        <div>
                          <h4>Confidence</h4>
                          <p>Scores returned by the investigation agents</p>
                        </div>
                      </div>
                      <div className="pg-confidence-grid">
                        {[
                          ['Monitoring', score('monitoring')],
                          ['Diagnosis', score('diagnosis')],
                          ['Remediation', score('remediation')],
                          ['Judge', typeof reports.judgeOverallScore === 'number'
                            ? `${reports.judgeOverallScore}%`
                            : 'N/A'],
                          ['Overall', typeof reports.overallConfidence === 'number'
                            ? `${reports.overallConfidence}%`
                            : 'N/A'],
                        ].map(([label, value]) => (
                          <div
                            className={`pg-confidence-card ${label === 'Overall' ? 'is-overall' : ''}`}
                            key={label}
                            title={label === 'Monitoring' && reports.confidenceSource?.monitoring === 'evidence_coverage'
                              ? 'Computed from evidence coverage: the agent did not return a score.'
                              : label === 'Judge'
                                ? 'Independent audit score, averaged from the judge’s per-agent ratings.'
                                : label === 'Overall'
                                  ? 'Mean of the scores that are available.'
                                  : undefined}
                          >
                            <span>{label}</span>
                            <strong>{value}</strong>
                          </div>
                        ))}
                      </div>
                    </section>

                    <section className="pg-report-panel pg-monitoring-panel">
                      <div className="pg-panel-kicker">01 · OBSERVABILITY</div>
                      <h4>Monitoring</h4>
                      {hasReport(reports.monitoring) ? (() => {
                        const fields = [
                          ['Build summary', ['Build summary']],
                          ['Failed stage', ['Failed stage', 'Failed build stage', 'Failing stage']],
                          ['Exact error', ['Exact error', 'Console error', 'Error']],
                        ]
                          .map(([label, aliases]) => [label, extractSection(reports.monitoring, aliases)])
                          .filter(([, value]) => value)
                        // If the agent ignored the expected layout, show its text as-is.
                        if (fields.length === 0) {
                          return (
                            <div className="agent-report-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{reports.monitoring}</ReactMarkdown></div>
                          )
                        }
                        return (
                          <div className="pg-summary-fields">
                            {fields.map(([label, value]) => (
                              <div className="pg-summary-field" key={label}>
                                <strong>{label}</strong>
                                <div className="agent-report-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{value}</ReactMarkdown></div>
                              </div>
                            ))}
                          </div>
                        )
                      })() : <p className="pg-empty-state">No separate monitoring report was returned.</p>}
                    </section>

                    <section className="pg-report-panel pg-diagnosis-panel">
                      <div className="pg-panel-kicker">02 · ROOT CAUSE</div>
                      <h4>Diagnosis</h4>
                      {hasReport(reports.diagnosis) ? (
                        <div className="pg-summary-fields">
                          {[
                            ['Root cause', ['Root cause', 'Primary root cause', 'Likely root cause']],
                            ['Conclusion', ['Conclusion', 'Diagnosis conclusion']],
                          ].map(([label, aliases]) => {
                            const value = extractSection(reports.diagnosis, aliases)
                            return (
                              <div className="pg-summary-field" key={label}>
                                <strong>{label}</strong>
                                {value ? (
                                  <div className="agent-report-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{value}</ReactMarkdown></div>
                                ) : <p className="pg-empty-state">Not explicitly reported.</p>}
                              </div>
                            )
                          })}
                        </div>
                      ) : <p className="pg-empty-state">No separate diagnosis report was returned.</p>}
                    </section>
                    
                    {historical && (
                      <section className="pg-report-panel pg-historical-panel">
                        <div className="pg-panel-kicker">03 · HISTORICAL MATCH</div>
                        <h4>Similar incident found</h4>
                        <div className="pg-summary-fields">
                          <div className="pg-summary-field">
                            <strong>Previous incident</strong>
                            <p>
                              {hist('jobName', 'job_name') || 'Unknown job'} build #{hist('buildNumber', 'build_number') ?? '?'}
                            </p>
                          </div>
                          <div className="pg-summary-field">
                            <strong>Root cause match</strong>
                            {hasReport(extractSection(reports.diagnosis, ['Historical confirmation'])) ? (
                              <div className="agent-report-markdown">
                                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                  {extractSection(reports.diagnosis, ['Historical confirmation'])}
                                </ReactMarkdown>
                              </div>
                            ) : <p className="pg-empty-state">Not explicitly reported.</p>}
                          </div>
                          <div className="pg-summary-field">
                            <strong>How it was solved</strong>
                            <p>{hist('resolutionMethod', 'resolution_method') || 'Not recorded'}</p>
                            {(hist('actions', 'actions') || []).length > 0 && (
                              <ul className="pg-historical-actions">
                                {hist('actions', 'actions').map((action, index) => (
                                  <li key={index}>{action.text}</li>
                                ))}
                              </ul>
                            )}
                          </div>
                        </div>
                      </section>
                    )}

                    

                    <section className="pg-report-panel pg-remediation-panel">
                      <div className="pg-panel-kicker">04 · PROPOSED RESPONSE</div>
                      <h4>Remediation</h4>
                      {steps.length > 0 ? (
                        <ol className="pg-proposed-steps">
                          {steps.map((step, index) => (
                            <li key={step.id ?? `${step.order_shown ?? index}-${index}`}>
                              <span className="pg-step-number">{step.order_shown ?? index + 1}</span>
                              <div className="pg-step-body">
                                <p>{step.text || step.description || step.action || 'Step details unavailable.'}</p>
                                {step.disruptive && <span className="pg-disruptive-tag">Potentially disruptive</span>}
                              </div>
                            </li>
                          ))}
                        </ol>
                      ) : hasReport(reports.remediation) ? (
                        <div className="agent-report-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{reports.remediation}</ReactMarkdown></div>
                      ) : <p className="pg-empty-state">No remediation steps were returned.</p>}
                    </section>

                    {typeof reports.judgeOverallScore === 'number' && (
                      <section
                        className={`pg-report-panel pg-judge-panel ${
                          reports.judgeHallucinationDetected === false
                            ? 'judge-clean'
                            : reports.judgeHallucinationDetected === true
                              ? 'judge-warning'
                              : 'judge-neutral'
                        }`}
                      >
                        <div className="pg-panel-kicker">05 · INDEPENDENT AUDIT</div>
                        <h4>Judge score</h4>
                        <div className="pg-judge-score">{reports.judgeOverallScore}%</div>
                        <p className="pg-judge-headline">
                          {reports.judgeHallucinationDetected === false
                            ? 'No hallucinations detected. Review notes below are context and evidence caveats.'
                            : reports.judgeHallucinationDetected === true
                              ? 'The judge found possible unsupported claims. Review the evidence concerns below.'
                              : 'Judge could not determine whether hallucinations are present.'}
                        </p>
                        <div className="pg-summary-fields">
                          {Object.entries(reports.judgeScores || {}).length > 0 && (
                            <div className="pg-summary-field">
                              <strong>Per-agent scores</strong>
                              <p>
                                {Object.entries(reports.judgeScores).map(([name, score]) => (
                                  score !== null ? `${name[0].toUpperCase()}${name.slice(1)} (${score}%)` : null
                                )).filter(Boolean).join(' · ')}
                              </p>
                            </div>
                          )}
                          {typeof reports.judgeHistoricalMatchQuality === 'number' && (
                            <div className="pg-summary-field">
                              <strong>Historical match quality</strong>
                              <p>{reports.judgeHistoricalMatchQuality}%</p>
                            </div>
                          )}
                          {(reports.judgeIssues || []).length > 0 && (
                            <div className="pg-summary-field">
                              <strong>{reports.judgeHallucinationDetected === false ? 'Review notes' : 'Evidence concerns'}</strong>
                              <ul className="pg-historical-actions">
                                {reports.judgeIssues.map((issue, index) => <li key={index}>{issue}</li>)}
                              </ul>
                            </div>
                          )}
                        </div>
                      </section>
                    )}  

                    {hasReport(reports.unified) && (
                      <details className="pg-raw-report">
                        <summary>View full investigation report</summary>
                        <div className="agent-report-markdown">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{getUnifiedReportForDisplay(reports)}</ReactMarkdown>
                        </div>
                      </details>
                    )}
                  </>
                )
              })()}

              {investigation.decisionStatus === 'awaiting_approval' && (
                <div className="pg-approval-panel">
                  <div>
                    <h4>Ready to proceed?</h4>
                    <p>Approving allows PipeGuard to fetch the responsible file, apply the proposed fix, and push a commit to GitHub. Denying leaves the fix unapplied.</p>
                  </div>
                  {investigation.decisionMessage && (
                    <div className="decision-result decision-error" role="alert">
                      <strong>Could not submit the decision.</strong>{' '}
                      {investigation.decisionMessage}
                    </div>
                  )}
                  <div className="approval-buttons">
                    <button
                      className="approve-button"
                      onClick={() => onDecision('approve')}
                      disabled={investigation.isDeciding}
                    >
                      {investigation.isDeciding ? 'Applying…' : '✓ Approve & fix'}
                    </button>
                    <button
                      className="deny-button"
                      onClick={() => onDecision('deny')}
                      disabled={investigation.isDeciding}
                    >
                      ✕ Deny
                    </button>
                  </div>
                </div>
              )}

              {investigation.decisionStatus === 'denied' && (
                <div className="decision-result decision-denied pg-decision-panel">
                  <strong>Approval denied.</strong>{' '}
                  {investigation.decisionMessage || 'No automated changes were made.'}
                  {investigation.feedbackSaved ? (
                    <div className="decision-feedback-saved">✓ Resolution saved. Thank you.</div>
                  ) : (
                    <div>
                      <button className="secondary-button feedback-reopen-button" onClick={onOpenFeedback}>
                        Report how you resolved it
                      </button>
                    </div>
                  )}
                </div>
              )}

              {investigation.decisionStatus === 'approved_fixed' && (
                <div className={`decision-result pg-decision-panel ${
                  investigation.fixResult?.applied ? 'decision-approved' : 'decision-error'
                }`}>
                  {investigation.fixResult?.applied ? (
                    <>
                      <strong>Fix applied.</strong> {investigation.decisionMessage}{' '}
                      {investigation.fixResult.commit_url && (
                        <a href={investigation.fixResult.commit_url} target="_blank" rel="noreferrer">
                          View commit ↗
                        </a>
                      )}
                      {investigation.feedbackSaved ? (
                        <div className="decision-feedback-saved">✓ Feedback saved. Thank you.</div>
                      ) : (
                        <div>
                          <button className="secondary-button feedback-reopen-button" onClick={onOpenFeedback}>
                            Give feedback on this fix
                          </button>
                        </div>
                      )}
                    </>
                  ) : (
                    <>
                      <strong>Could not apply the fix.</strong> {investigation.decisionMessage}
                    </>
                  )}
                </div>
              )}
            </div>
          )}
        </section>
      )}
    </section>
  )
}


function FixFeedbackModal({ investigation, onClose, onSubmit }) {
  const isManual = investigation.decisionStatus === 'denied'
  const steps = investigation.steps || []
  const [buildPassed, setBuildPassed] = useState(false)
  const [likedFix, setLikedFix] = useState(false)
  const [systemEfficient, setSystemEfficient] = useState(false)
  const [comment, setComment] = useState('')
  const [followedSteps, setFollowedSteps] = useState({})
  const [solvedStepId, setSolvedStepId] = useState('')
  const [customSolution, setCustomSolution] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [saveError, setSaveError] = useState('')

  async function handleSubmit() {
    setIsSaving(true)
    setSaveError('')
    try {
      await onSubmit({
        build_passed: buildPassed,
        liked_fix: likedFix,
        system_efficient: systemEfficient,
        comment,
        resolution_path: isManual ? 'manual' : 'auto_fix',
        followed_steps: isManual ? followedSteps : {},
        solved_step_id: isManual ? (solvedStepId || null) : null,
        custom_solution: isManual ? customSolution : '',
      })
    } catch (error) {
      setSaveError(error.message || 'Could not save your feedback.')
      setIsSaving(false)
    }
  }

  const commitUrl = investigation.fixResult?.commit_url

  return (
    <div
      className="modal-backdrop feedback-modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !isSaving) onClose()
      }}
    >
      <section
        className="configuration-modal feedback-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="feedback-modal-title"
      >
        <div className="modal-header">
          <div className="feedback-success-mark" aria-hidden="true">{isManual ? '✎' : '✓'}</div>
          <button
            className="modal-close"
            onClick={onClose}
            disabled={isSaving}
            aria-label="Close"
          >×</button>
        </div>

        <div className="modal-eyebrow">
          {isManual ? 'PIPEGUARD · RESOLVED BY YOU' : 'PIPEGUARD · FIX APPLIED'}
        </div>
        <h2 id="feedback-modal-title">
          {isManual ? 'How did you resolve it?' : 'Fix pushed to GitHub'}
        </h2>
        <p className="modal-description">
          {isManual ? (
            'You handled this yourself. Tell PipeGuard what happened with each proposed step so it can learn what works for you.'
          ) : (
            <>
              {investigation.decisionMessage || 'PipeGuard pushed a fix.'}{' '}
              {commitUrl && (
                <a href={commitUrl} target="_blank" rel="noreferrer">View commit ↗</a>
              )}
            </>
          )}
        </p>

        {isManual && steps.length === 0 && (
          <p className="modal-description">
            No structured steps were stored for this incident. Describe what you did below.
          </p>
        )}

        {isManual && steps.length > 0 && (
          <>
            <div className="feedback-question">
              Which of these did you actually follow? Tick everything you did —
              even steps that did not end up fixing it. This tells PipeGuard
              your real process, not just the final answer.
            </div>
            <div className="step-outcome-list">
              {steps.map((step) => (
                <label key={step.id} className="step-checklist-row">
                  <input
                    type="checkbox"
                    checked={Boolean(followedSteps[step.id])}
                    onChange={(event) => {
                      const checked = event.target.checked
                      setFollowedSteps((current) => ({ ...current, [step.id]: checked }))
                      if (!checked && solvedStepId === step.id) setSolvedStepId('')
                    }}
                  />
                  <span className="step-outcome-text">
                    <strong>{step.order_shown}.</strong> {step.text}
                  </span>
                </label>
              ))}
            </div>

            {Object.values(followedSteps).some(Boolean) && (
              <label className="form-field solved-step-field">
                <span>Which one actually fixed it?</span>
                <select
                  value={solvedStepId}
                  onChange={(event) => setSolvedStepId(event.target.value)}
                >
                  <option value="">None of these — I used something else</option>
                  {steps
                    .filter((step) => followedSteps[step.id])
                    .map((step) => (
                      <option key={step.id} value={step.id}>
                        {step.order_shown}. {step.text.slice(0, 80)}{step.text.length > 80 ? '…' : ''}
                      </option>
                    ))}
                </select>
              </label>
            )}
          </>
        )}

        {isManual && (
          <label className="form-field feedback-comment">
            <span>Your own solution (optional)</span>
            <textarea
              rows={3}
              value={customSolution}
              onChange={(event) => setCustomSolution(event.target.value)}
              placeholder="What did you do that was not in the proposed steps?"
            />
          </label>
        )}

        <div className="feedback-question">After rebuilding in Jenkins, how did it go?</div>

        <label className="feedback-check">
          <input
            type="checkbox"
            checked={buildPassed}
            onChange={(event) => setBuildPassed(event.target.checked)}
          />
          <span>The Jenkins build now passes</span>
        </label>
        <label className="feedback-check">
          <input
            type="checkbox"
            checked={likedFix}
            onChange={(event) => setLikedFix(event.target.checked)}
          />
          <span>
            {isManual
              ? 'The proposed steps were useful'
              : 'I am happy with the fix PipeGuard made'}
          </span>
        </label>
        <label className="feedback-check">
          <input
            type="checkbox"
            checked={systemEfficient}
            onChange={(event) => setSystemEfficient(event.target.checked)}
          />
          <span>PipeGuard was efficient on this incident</span>
        </label>

        <label className="form-field feedback-comment">
          <span>Comments (optional)</span>
          <textarea
            rows={3}
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder={isManual
              ? 'What did you actually do? Anything the steps missed?'
              : 'Anything we should know about this fix?'}
          />
        </label>

        {saveError && <div className="feedback-error" role="alert">{saveError}</div>}

        <div className="modal-actions">
          <button className="secondary-button" onClick={onClose} disabled={isSaving}>
            Later
          </button>
          <button className="primary-button" onClick={handleSubmit} disabled={isSaving}>
            {isSaving ? 'Saving…' : 'Submit feedback'}
          </button>
        </div>
      </section>
    </div>
  )
}

function IntegrationGrid({ integrations, onConfigure }) {
  return (
    <div className="integration-grid">
      {integrations.map((integration) => (
        <article className="integration-card" key={integration.id}>
          <div className="integration-card-top">
            <div className={`integration-icon ${integration.color}`}>{integration.icon}</div>
            <span className="connection-status">
              <span className={`status-dot ${integration.connected ? 'green' : 'gray'}`} />
              {integration.connected ? 'Connected' : 'Not connected'}
            </span>
          </div>
          <div className="integration-name">{integration.name}</div>
          <div className="integration-category">{integration.category}</div>
          <p className="integration-description">{integration.description}</p>
          <div className="integration-card-footer">
            <span className="provider-type">{integration.connected ? 'Live API' : 'Provider'}</span>
            <button className="configure-button" onClick={() => onConfigure(integration)}>
              {integration.connected ? 'Reconnect' : 'Connect'} <span>→</span>
            </button>
          </div>
        </article>
      ))}
    </div>
  )
}

export default App

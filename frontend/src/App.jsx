import { useCallback, useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
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
    const buildNumber = job.last_build?.number
    if (!jenkinsCredentials || !buildNumber) return

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
        const end = Math.ceil(
          (evidence.timestamp + (evidence.duration_ms || 0)) / 1000,
        ) + 300
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

      try {
        const [githubOwner, githubRepoName] = (selectedGithubRepo || '').split('/')
        const agentResponse = await fetch(`${AGENT_API_URL}/api/investigate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            job_name: job.name,
            build_number: buildNumber,
            evidence: agentEvidence,
            github_token: githubToken || '',
            github_owner: githubOwner || '',
            github_repo: githubRepoName || '',
          }),
        })

        const agentData = await agentResponse.json()
        if (!agentResponse.ok) {
          throw new Error(
            agentData.detail || `Agent API failed (HTTP ${agentResponse.status})`,
          )
        }

        setActiveInvestigationStage(2)
        setInvestigation((current) => {
          if (
            !current ||
            current.jobName !== job.name ||
            current.buildNumber !== buildNumber
          ) return current

          return {
            ...current,
            agentStatus: 'complete',
            incidentId: agentData.incident_id,
            decisionStatus: agentData.status,
            agentReports: {
              monitoring: agentData.monitoring_report,
              diagnosis: agentData.diagnosis_report,
              remediation: agentData.remediation_report,
            },
            agentError: '',
          }
        })
      } catch (agentError) {
        // Keep the evidence and show the agent failure in the final-report stage.
        setActiveInvestigationStage(2)
        setInvestigation((current) => {
          if (
            !current ||
            current.jobName !== job.name ||
            current.buildNumber !== buildNumber
          ) return current

          return {
            ...current,
            agentStatus: 'error',
            agentError: agentError.message || 'Agent investigation failed.',
          }
        })
      }
    } catch (error) {
      setActiveInvestigationStage(0)
      setInvestigation({
        jobName: job.name,
        buildNumber,
        error: error.message || 'Could not retrieve this build evidence.',
      })
    } finally {
      setIsInvestigating(false)
    }
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
      } : current))
    } catch (error) {
      setInvestigation((current) => (current ? {
        ...current,
        isDeciding: false,
        decisionMessage: error.message || 'Could not submit the decision.',
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
                <JobsTable jobs={jenkinsJobs} onInvestigate={investigateBuild} />
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
                ? <JobsTable jobs={jenkinsJobs} onInvestigate={investigateBuild} />
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

function JobsTable({ jobs, onInvestigate }) {
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
                      disabled={!onInvestigate}
                    >
                      Investigate #{job.last_build.number} →
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

function BuildInvestigation({ investigation, isLoading, activeStage, onStageChange, onDecision }) {
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
                {
                  name: 'Monitoring',
                  detail: 'Collect and summarize evidence',
                  number: '01',
                },
                {
                  name: 'Diagnosis',
                  detail: 'Identify likely causes',
                  number: '02',
                },
                {
                  name: 'Remediation',
                  detail: 'Prepare a reviewable fix',
                  number: '03',
                },
              ].map((stage, index) => (
                <div
                  className={`agent-workflow-stage ${
                    investigation.agentStatus === 'complete' ? 'stage-complete' : ''
                  }`}
                  key={stage.name}
                >
                  <div className="agent-stage-marker">
                    {investigation.agentStatus === 'complete' ? '✓' : stage.number}
                  </div>
                  <div className="agent-stage-copy">
                    <strong>{stage.name}</strong>
                    <span>{stage.detail}</span>
                  </div>
                  {index < 2 && <div className="agent-stage-connector" />}
                </div>
              ))}
            </div>

            {investigation.agentStatus === 'running' && (
              <div className="agent-workflow-progress" aria-label="Investigation in progress">
                <span />
              </div>
            )}

            <p className="agent-workflow-footnote">
              {investigation.agentStatus === 'complete'
                ? 'Reports are ready. Review the diagnosis and proposed actions before making changes.'
                : investigation.agentStatus === 'error'
                  ? 'The agent workflow did not return a complete set of reports. Your collected build evidence remains available above.'
                  : 'The API returns the agent reports together when the workflow finishes; individual live stage updates are not available.'}
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
            <div className="agent-report-list">
              <div className="agent-report-list-heading">
                <div>
                  <div className="eyebrow">INVESTIGATION OUTPUT</div>
                  <h4>Analysis and recommended actions</h4>
                </div>
                <span className="agent-report-count">3 reports</span>
              </div>

              <details className="investigation-section agent-report-card">
                <summary>
                  <span className="agent-report-index">01</span>
                  <span className="agent-report-summary-copy">
                    <strong>Monitoring report</strong>
                    <small>Build signals, collected evidence, and system observations</small>
                  </span>
                  <span className="agent-report-chevron">⌄</span>
                </summary>
                <div className="agent-report-markdown">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {investigation.agentReports.monitoring || 'No monitoring report returned.'}
                  </ReactMarkdown>
                </div>
              </details>

              <details className="investigation-section agent-report-card" open>
                <summary>
                  <span className="agent-report-index">02</span>
                  <span className="agent-report-summary-copy">
                    <strong>Diagnosis report</strong>
                    <small>Failure analysis and evidence supporting the likely cause</small>
                  </span>
                  <span className="agent-report-chevron">⌄</span>
                </summary>
                <div className="agent-report-markdown">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {investigation.agentReports.diagnosis || 'No diagnosis report returned.'}
                  </ReactMarkdown>
                </div>
              </details>

              <details className="investigation-section agent-report-card" open>
                <summary>
                  <span className="agent-report-index">03</span>
                  <span className="agent-report-summary-copy">
                    <strong>Remediation proposal</strong>
                    <small>Suggested next steps for an engineer to review</small>
                  </span>
                  <span className="agent-report-chevron">⌄</span>
                </summary>
                <div className="agent-remediation-notice">
                  <span aria-hidden="true">!</span>
                  <p>
                    Review the proposal and validate its impact before applying
                    changes. PipeGuard does not execute these recommendations automatically.
                  </p>
                </div>
                <div className="agent-report-markdown">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {investigation.agentReports.remediation || 'No remediation proposal returned.'}
                  </ReactMarkdown>
                </div>
              </details>

                {investigation.decisionStatus === 'awaiting_approval' && (
                  <div className="approval-actions">
                    <p>
                      Approve to let PipeGuard fetch the responsible file, fix it,
                      and push the commit to GitHub. Deny to handle it yourself.
                    </p>
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
                  <div className="decision-result decision-denied">
                    <strong>Denied.</strong>{' '}
                    {investigation.decisionMessage || 'No changes were made.'}
                  </div>
                )}

                {investigation.decisionStatus === 'approved_fixed' && (
                  <div className={`decision-result ${
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

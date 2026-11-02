import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { extractSection } from './reportSections'
import IncidentsDashboard from './components/IncidentsDashboard'
import PersonalizationToggle from './components/PersonalizationToggle'
import IncidentChatbot from './IncidentChatbot'
import './App.css'

const API_URL = 'http://127.0.0.1:8000';
const AGENT_API_URL = API_URL;
const DEFAULT_GRAFANA_URL = 'http://192.168.56.10:3000'
const PIPEGUARD_GRAFANA_DASHBOARD_URL = `${DEFAULT_GRAFANA_URL}/d/pipeguard-observability/pipeguard-ci-cd-and-observability?from=now-24h&to=now&timezone=browser&refresh=30s&kiosk`

function isPipeGuardGrafanaDashboardUrl(value, grafanaBaseUrl) {
  try {
    const dashboard = new URL(value)
    const base = new URL(grafanaBaseUrl)
    const internalDashboard = /^\/d\/pipeguard-observability(?:\/|$)/.test(dashboard.pathname)
    const externallySharedDashboard = /^\/public-dashboards\/[A-Za-z0-9_-]+\/?$/.test(dashboard.pathname)
    return dashboard.origin === base.origin && (internalDashboard || externallySharedDashboard)
  } catch {
    return false
  }
}

function findMatchingSonarProject(projects, repositoryFullName) {
  const repository = (repositoryFullName || '').split('/').pop() || ''
  const normalize = (value) => String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '')
  const normalizedRepository = normalize(repository)
  if (!normalizedRepository) return null
  return projects.find((project) =>
    normalize(project.key) === normalizedRepository || normalize(project.name) === normalizedRepository,
  ) || null
}

function authenticatedFetch(input, options = {}) {
  const accessToken = (() => {
    try {
      return JSON.parse(sessionStorage.getItem('pipeguard-auth') || 'null')?.access_token || ''
    } catch {
      return ''
    }
  })()
  if (!accessToken) {
    return Promise.reject(new Error('Your session expired. Sign in again.'))
  }
  const headers = new Headers(options.headers || {})
  headers.set('Authorization', `Bearer ${accessToken}`)
  return fetch(input, { ...options, headers })
}

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
  {
    id: 'loki',
    name: 'Loki',
    description: 'Search service logs around a failing build.',
    category: 'Observability',
    icon: 'L',
    color: 'loki',
    fields: [
      { name: 'url', label: 'Loki URL', placeholder: 'http://192.168.56.10:3100' },
      { name: 'project_name', label: 'PipeGuard project label', placeholder: 'e.g. pipeguard-lab' },
      { name: 'username', label: 'Username (optional)', placeholder: 'For HTTP basic authentication', required: false },
      { name: 'token', label: 'Token or password (optional)', placeholder: 'Bearer token, or password with username', secret: true, required: false },
    ],
  },
  {
    id: 'sonarqube',
    name: 'SonarQube',
    description: 'Review code-quality metrics, vulnerabilities, and security hotspots.',
    category: 'Code quality & security',
    icon: 'S',
    color: 'sonarqube',
    fields: [
      { name: 'url', label: 'SonarQube URL', placeholder: 'http://192.168.56.10:9000' },
      { name: 'token', label: 'User token', placeholder: 'Enter SonarQube token', secret: true },
    ],
  },
  {
    id: 'kubernetes',
    name: 'Kubernetes',
    description: 'Connect to a cluster and inspect live namespace workloads, services and warning events.',
    category: 'Orchestration',
    icon: 'K',
    color: 'kubernetes',
    fields: [
      { name: 'url', label: 'Kubernetes API URL', placeholder: 'https://127.0.0.1:6443' },
      { name: 'namespace', label: 'Namespace', placeholder: 'pipeguard-lab' },
      { name: 'token', label: 'Read-only bearer token', placeholder: 'Paste namespace-scoped token', secret: true },
      { name: 'ca_certificate', label: 'Cluster CA certificate (PEM)', placeholder: '-----BEGIN CERTIFICATE-----', multiline: true },
    ],
  },
  {
    id: 'grafana',
    name: 'Grafana',
    description: 'Open build-time dashboards for Prometheus metrics and Loki logs.',
    category: 'Observability',
    icon: 'G',
    color: 'grafana',
    connected: true,
    fields: [
      { name: 'url', label: 'Grafana URL', placeholder: 'http://192.168.56.10:3000' },
      { name: 'dashboard_url', label: 'Grafana dashboard or shared dashboard URL', placeholder: PIPEGUARD_GRAFANA_DASHBOARD_URL },
    ],
  },
]

const navigation = [
  { id: 'dashboard', icon: '▦', label: 'Dashboard' },
  { id: 'pipelines', icon: '⑂', label: 'Pipelines' },
  { id: 'incidents', icon: '◈', label: 'Investigations' },
]

const toolCatalog = [
  {
    objective: 'CI/CD pipelines',
    tools: [
      { id: 'jenkins', name: 'Jenkins', icon: 'J', color: 'jenkins', description: 'Builds, stages, console logs and Trivy reports.', available: true },
      { name: 'GitLab CI', icon: 'GL', color: 'github', description: 'GitLab runners, pipelines and job traces.', hint: 'Will be integrated soon' },
      { name: 'CircleCI', icon: 'C', color: 'prometheus', description: 'Pipeline runs, workflows and job output.', hint: 'Will be integrated soon' },
    ],
  },
  {
    objective: 'Source control',
    tools: [
      { id: 'github', name: 'GitHub', icon: '⌘', color: 'github', description: 'Repositories, commits and changed files.', available: true },
      { name: 'GitLab', icon: 'GL', color: 'sonarqube', description: 'Projects, merge requests and commits.', hint: 'Will be integrated soon' },
    ],
  },
  {
    objective: 'Observability',
    tools: [
      { id: 'prometheus', name: 'Prometheus', icon: 'P', color: 'prometheus', description: 'Target health and metrics queries.', available: true },
      { id: 'loki', name: 'Loki', icon: 'L', color: 'loki', description: 'Container logs collected through Alloy.', available: true },
      { id: 'grafana', name: 'Grafana', icon: 'G', color: 'grafana', description: 'Dashboards for metrics and logs.', available: true },
      { name: 'Datadog', icon: 'D', color: 'prometheus', description: 'Metrics, traces and service monitoring.', hint: 'Will be integrated soon' },
    ],
  },
  {
    objective: 'Code quality & security',
    tools: [
      { id: 'sonarqube', name: 'SonarQube', icon: 'S', color: 'sonarqube', description: 'Quality gates, measures and code issues.', available: true },
      { name: 'Trivy', icon: 'T', color: 'kubernetes', description: 'Container vulnerability scan in the Jenkins pipeline.', integrated: 'Integrated with Jenkins' },
      { name: 'Snyk', icon: 'S', color: 'sonarqube', description: 'Open-source and container security scans.', hint: 'Will be integrated soon' },
    ],
  },
  {
    objective: 'Orchestration & infrastructure',
    tools: [
      { id: 'kubernetes', name: 'Kubernetes', icon: 'K', color: 'kubernetes', description: 'Namespace workloads, services and events.', available: true },
      { name: 'Argo CD', icon: 'A', color: 'prometheus', description: 'GitOps applications and sync status.', hint: 'Will be integrated soon' },
      { name: 'Terraform', icon: 'T', color: 'sonarqube', description: 'Infrastructure plans and state changes.', hint: 'Will be integrated soon' },
    ],
  },
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
  const [auth, setAuth] = useState(() => {
    try {
      return JSON.parse(sessionStorage.getItem('pipeguard-auth') || 'null')
    } catch {
      return null
    }
  })
  const [checkingSession, setCheckingSession] = useState(Boolean(auth?.access_token))

  useEffect(() => {
    if (!auth?.access_token) return
    fetch(`${API_URL}/api/auth/me`, {
      headers: { Authorization: `Bearer ${auth.access_token}` },
    })
      .then(async (response) => {
        if (!response.ok) throw new Error('Session expired')
        const user = await response.json()
        const updated = { ...auth, user }
        sessionStorage.setItem('pipeguard-auth', JSON.stringify(updated))
        setAuth(updated)
      })
      .catch(() => {
        sessionStorage.removeItem('pipeguard-auth')
        setAuth(null)
      })
      .finally(() => setCheckingSession(false))
  // Validate only when the initial session is loaded.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function finishLogin(session) {
    sessionStorage.setItem('pipeguard-auth', JSON.stringify(session))
    setAuth(session)
  }

  async function signOut() {
    if (auth?.access_token) {
      await fetch(`${API_URL}/api/auth/logout`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${auth.access_token}` },
      }).catch(() => {})
    }
    sessionStorage.removeItem('pipeguard-auth')
    setAuth(null)
  }

  if (checkingSession) return <div className="auth-loading">Loading your PipeGuard workspace…</div>
  return auth?.access_token
    ? <AuthenticatedApp auth={auth} onSignOut={signOut} />
    : <AuthScreen onAuthenticated={finishLogin} />
}

function AuthScreen({ onAuthenticated }) {
  const [mode, setMode] = useState('login')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const response = await fetch(`${API_URL}/api/auth/${mode === 'signup' ? 'signup' : 'login'}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, ...(mode === 'signup' ? { name } : {}) }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || `Could not ${mode === 'signup' ? 'create account' : 'sign in'} (HTTP ${response.status})`)
      onAuthenticated(data)
    } catch (submitError) {
      setError(submitError.message || 'Authentication failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-card">
        <div className="auth-brand"><span className="brand-mark">P</span><span><strong>pipeguard</strong><small>DevOps intelligence</small></span></div>
        <div className="eyebrow">PERSONAL WORKSPACE</div>
        <h1>{mode === 'signup' ? 'Create your account.' : 'Welcome back.'}</h1>
        <p className="auth-subtitle">Sign in to access your pipelines and private incident history.</p>
        <form onSubmit={submit}>
          {mode === 'signup' && (
            <label className="form-field"><span>Name</span><input value={name} onChange={(event) => setName(event.target.value)} autoComplete="name" required maxLength={80} /></label>
          )}
          <label className="form-field"><span>Email</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" required maxLength={254} /></label>
          <label className="form-field"><span>Password</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={mode === 'signup' ? 'new-password' : 'current-password'} required minLength={10} maxLength={128} /></label>
          {mode === 'signup' && <p className="auth-password-hint">Use at least 10 characters.</p>}
          {error && <div className="error-banner" role="alert">{error}</div>}
          <button className="primary-button auth-submit" type="submit" disabled={busy}>{busy ? 'Please wait…' : mode === 'signup' ? 'Create account' : 'Sign in'}</button>
        </form>
        <p className="auth-switch">
          {mode === 'signup' ? 'Already have an account?' : 'New to PipeGuard?'}{' '}
          <button type="button" onClick={() => { setMode(mode === 'signup' ? 'login' : 'signup'); setError('') }}>
            {mode === 'signup' ? 'Sign in' : 'Create an account'}
          </button>
        </p>
      </section>
    </main>
  )
}

function AuthenticatedApp({ auth, onSignOut }) {
  const [activePage, setActivePage] = useState('dashboard')
  const qloraStorageKey = `pipeguard-qlora-${auth.user?.id || auth.user?._id || auth.user?.email || 'user'}`
  const [qloraEnabled, setQloraEnabled] = useState(() => localStorage.getItem(qloraStorageKey) === 'true')
  const personalizationStorageKey = `pipeguard-personalization-${auth.user?.id || auth.user?._id || auth.user?.email || 'user'}`
  const [personalizationEnabled, setPersonalizationEnabled] = useState(() => localStorage.getItem(personalizationStorageKey) === 'true')
  const personalizationEnabledRef = useRef(personalizationEnabled)
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
  const [githubConnectionStatus, setGithubConnectionStatus] = useState('disconnected')
  const [githubConnectionMessage, setGithubConnectionMessage] = useState('')
  const [prometheusUrl, setPrometheusUrl] = useState('')
  const [prometheusConnectionStatus, setPrometheusConnectionStatus] = useState('disconnected')
  const [prometheusConnectionMessage, setPrometheusConnectionMessage] = useState('')
  const [lokiUrl, setLokiUrl] = useState('')
  const [lokiProjectName, setLokiProjectName] = useState('')
  const [lokiUsername, setLokiUsername] = useState('')
  const [lokiToken, setLokiToken] = useState('')
  const [lokiConnectionStatus, setLokiConnectionStatus] = useState('disconnected')
  const [lokiConnectionMessage, setLokiConnectionMessage] = useState('')
  const [sonarqubeUrl, setSonarqubeUrl] = useState('')
  const [sonarqubeToken, setSonarqubeToken] = useState('')
  const [sonarqubeProjects, setSonarqubeProjects] = useState([])
  const [selectedSonarqubeProject, setSelectedSonarqubeProject] = useState('')
  const [sonarqubeConnectionStatus, setSonarqubeConnectionStatus] = useState('disconnected')
  const [sonarqubeConnectionMessage, setSonarqubeConnectionMessage] = useState('')
  const [kubernetesConfig, setKubernetesConfig] = useState(null)
  const [kubernetesConnectionStatus, setKubernetesConnectionStatus] = useState('disconnected')
  const [kubernetesConnectionMessage, setKubernetesConnectionMessage] = useState('')
  const [grafanaUrl, setGrafanaUrl] = useState(DEFAULT_GRAFANA_URL)
  const [grafanaDashboardUrl, setGrafanaDashboardUrl] = useState(PIPEGUARD_GRAFANA_DASHBOARD_URL)
  const [grafanaConnectionMessage, setGrafanaConnectionMessage] = useState('')
  const [investigation, setInvestigation] = useState(null)
  const [isInvestigating, setIsInvestigating] = useState(false)
  const [isInvestigationModalOpen, setIsInvestigationModalOpen] = useState(false)
  const [activeInvestigationStage, setActiveInvestigationStage] = useState(0)
  const [incidentHistoryCount, setIncidentHistoryCount] = useState(0)

  useEffect(() => {
    localStorage.setItem(qloraStorageKey, String(qloraEnabled))
  }, [qloraEnabled, qloraStorageKey])

  useEffect(() => {
    localStorage.setItem(personalizationStorageKey, String(personalizationEnabled))
  }, [personalizationEnabled, personalizationStorageKey])

  async function changePersonalization(enabled) {
    if (enabled) {
      const response = await authenticatedFetch(`${API_URL}/api/learning/opt-in`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${auth.access_token}` },
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Could not enable personalized remediation.')
      personalizationEnabledRef.current = true
      setPersonalizationEnabled(true)
      return data
    }
    personalizationEnabledRef.current = false
    localStorage.setItem(personalizationStorageKey, 'false')
    setPersonalizationEnabled(false)
    return { initial_records_enrolled: 0 }
  }

  useEffect(() => {
    let cancelled = false
    authenticatedFetch(`${API_URL}/api/incidents/count`, {
      headers: { Authorization: `Bearer ${auth.access_token}` },
    })
      .then(async (response) => {
        const data = await response.json()
        if (!response.ok) throw new Error(data.detail || `Could not load incident count (HTTP ${response.status})`)
        if (!cancelled) setIncidentHistoryCount(data.count ?? 0)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [auth.access_token])

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
      loki: null,
      sonarqube: null,
      agentStatus: 'waiting',
      agentReports: null,
      agentError: '',
      nodeProgress: [],
      currentNode: null,
    })
  
    async function postJson(path, body) {
      const response = await authenticatedFetch(`${API_URL}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${auth.access_token}` },
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
        loki: null,
        sonarqube: null,
      })
  
      // GitHub lookup is tied to the exact commit SHA found in this build.
      let github = {
        status: 'not_configured',
        message: 'Connect GitHub and select the repository containing this commit.',
        sha,
      }

      // Loki is optional. Query containers labeled for this project and build.
      let loki
      if (!lokiUrl) {
        loki = {
          status: 'not_configured',
          message: 'Connect Loki in Settings to include service logs with this investigation.',
        }
      } else if (!lokiProjectName) {
        loki = {
          status: 'not_configured',
          message: 'Choose a PipeGuard project label in Loki settings to collect its build containers.',
        }
      } else if (!evidence.timestamp) {
        loki = {
          status: 'unavailable',
          message: 'The selected Jenkins build has no timestamp for log alignment.',
        }
      } else {
        const start = Math.floor(evidence.timestamp / 1000) - 300
        const end = Math.ceil((evidence.timestamp + (evidence.duration_ms || 0)) / 1000) + 300
        const query = `{project="${lokiProjectName}", build_number="${buildNumber}"}`
        try {
          const result = await postJson('/api/loki/query-range', {
            url: lokiUrl,
            project_name: lokiProjectName,
            build_number: String(buildNumber),
            username: lokiUsername,
            token: lokiToken,
            start,
            end,
            limit: 100,
          })
          loki = {
            status: 'success',
            query,
            count: result.count,
            lines: result.lines || [],
            start,
            end,
          }
        } catch (error) {
          loki = { status: 'error', message: error.message, query, start, end, lines: [] }
        }
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

      let sonarqube
      if (!sonarqubeUrl || !sonarqubeToken) {
        sonarqube = {
          status: 'not_configured',
          message: 'Connect SonarQube in Settings to include project analysis evidence.',
          issues: [],
          measures: {},
        }
      } else if (!selectedSonarqubeProject) {
        sonarqube = {
          status: 'not_configured',
          message: 'Select the SonarQube project that corresponds to this repository.',
          issues: [],
          measures: {},
        }
      } else {
        try {
          sonarqube = await postJson('/api/sonarqube/evidence', {
            url: sonarqubeUrl,
            token: sonarqubeToken,
            project_key: selectedSonarqubeProject,
            build_commit_sha: sha || '',
            limit: 25,
          })
        } catch (error) {
          sonarqube = {
            status: 'error',
            project_key: selectedSonarqubeProject,
            message: error.message || 'Could not retrieve SonarQube project evidence.',
            issues: [],
            measures: {},
          }
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
        loki,
        sonarqube,
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
          trivy: evidence.trivy ? {
            status: evidence.trivy.status,
            message: evidence.trivy.message,
            artifact_name: evidence.trivy.artifact_name,
            artifact_type: evidence.trivy.artifact_type,
            generated_at: evidence.trivy.generated_at,
            counts: evidence.trivy.counts,
            finding_count: evidence.trivy.finding_count,
            findings: (evidence.trivy.findings || []).slice(0, 10),
          } : null,
        },
        github: {
          ...github,
          files: github.files?.slice(0, 40),
        },
        metrics: metrics.map((metric) => ({
          ...metric,
          results: (metric.results || []).slice(0, 2).map((result) => ({
            ...result,
            values: (result.values || []).slice(-3),
          })),
        })),
        loki: {
          status: loki.status,
          message: loki.message,
          query: loki.query,
          start: loki.start,
          end: loki.end,
          count: loki.count,
          lines: (loki.lines || []).slice(-6).map((entry) => ({
            ...entry,
            line: String(entry.line ?? '').slice(-500),
          })),
        },
        sonarqube: {
          status: sonarqube.status,
          message: sonarqube.message,
          project_key: sonarqube.project_key,
          analysis: sonarqube.analysis,
          build_commit_sha: sonarqube.build_commit_sha,
          analysis_matches_build: sonarqube.analysis_matches_build,
          measures: sonarqube.measures,
          quality_gate: sonarqube.quality_gate,
          issue_count: sonarqube.issue_count,
          issues: (sonarqube.issues || []).slice(0, 4).map((issue) => ({
            severity: issue.severity,
            type: issue.type,
            rule: issue.rule,
            component: issue.component,
            line: issue.line,
            status: issue.status,
            message: String(issue.message ?? '').slice(0, 240),
          })),
        },
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
          use_local_llm: qloraEnabled,
          use_personalized_answers: personalizationEnabledRef.current,
        }
  
        debugLog('STEP 2: Payload prepared', {
          jobName: job.name,
          buildNumber,
          personalizationEnabled: personalizationEnabledRef.current,
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
  
        const response = await authenticatedFetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${auth.access_token}`,
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
            } catch {
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
            personalizationEnabled: finalPayload.personalization_enabled === true,
            policyVersionUsed: finalPayload.policy_version_used ?? null,
            },
            agentError: '',
          }
        })
  
        debugLog('STEP 9: Investigation response processed successfully')
        authenticatedFetch(`${API_URL}/api/incidents/count`, {
          headers: { Authorization: `Bearer ${auth.access_token}` },
        }).then(async (historyResponse) => {
          const historyData = await historyResponse.json()
          if (historyResponse.ok) setIncidentHistoryCount(historyData.count ?? 0)
        }).catch(() => {})
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

    const response = await authenticatedFetch(
      `${AGENT_API_URL}/api/incidents/${investigation.incidentId}/feedback`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${auth.access_token}` },
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
      const response = await authenticatedFetch(`${AGENT_API_URL}/api/investigate/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${auth.access_token}` },
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
      const response = await authenticatedFetch(`${API_URL}/api/jenkins/jobs`, {
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

  const persistIntegrationSettings = useCallback(async (provider, config) => {
    const response = await authenticatedFetch(`${API_URL}/api/integrations/${provider}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${auth.access_token}`,
      },
      body: JSON.stringify({ config }),
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || `Could not save ${provider} settings (HTTP ${response.status})`)
  }, [auth.access_token])

  useEffect(() => {
    let cancelled = false

    async function restoreIntegrationSettings() {
      try {
        const response = await authenticatedFetch(`${API_URL}/api/integrations`, {
          headers: { Authorization: `Bearer ${auth.access_token}` },
          cache: 'no-store',
        })
        const data = await response.json()
        if (!response.ok) throw new Error(data.detail || `Could not restore connections (HTTP ${response.status})`)
        if (cancelled) return

        const configs = data.configs || {}
        const connectedProviders = Object.keys(configs)
        setIntegrations((previous) => previous.map((integration) => ({
          ...integration,
          connected: integration.id === 'grafana' || connectedProviders.includes(integration.id),
        })))

        if (configs.jenkins) {
          setJenkinsCredentials(configs.jenkins)
          setConnectionStatus('connected')
          setConnectionMessage('Restored saved Jenkins connection.')
          void fetchJenkinsJobs(configs.jenkins, true)
        }
        if (configs.github) {
          setGithubToken(configs.github.token || '')
          setSelectedGithubRepo(configs.github.selected_repository || '')
          setGithubConnectionStatus('connected')
          setGithubConnectionMessage('Restored saved GitHub connection.')
          authenticatedFetch(`${API_URL}/api/github/repositories`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token: configs.github.token || '' }),
          }).then(async (repoResponse) => {
            const repoData = await repoResponse.json()
            if (repoResponse.ok && !cancelled) {
              const repositories = repoData.repositories || []
              setGithubRepositories(repositories)
              if (!repositories.some((repo) => repo.full_name === configs.github.selected_repository)) {
                setSelectedGithubRepo(repositories[0]?.full_name || '')
              }
            }
          }).catch(() => {})
        }
        if (configs.prometheus) {
          setPrometheusUrl(configs.prometheus.url || '')
          setPrometheusConnectionStatus('connected')
          setPrometheusConnectionMessage('Restored saved Prometheus connection.')
        }
        if (configs.loki) {
          setLokiUrl(configs.loki.url || '')
          setLokiProjectName(configs.loki.project_name || '')
          setLokiUsername(configs.loki.username || '')
          setLokiToken(configs.loki.token || '')
          setLokiConnectionStatus('connected')
          setLokiConnectionMessage('Restored saved Loki connection.')
        }
        if (configs.sonarqube) {
          setSonarqubeUrl(configs.sonarqube.url || '')
          setSonarqubeToken(configs.sonarqube.token || '')
          setSelectedSonarqubeProject(configs.sonarqube.selected_project || '')
          setSonarqubeConnectionStatus('connected')
          setSonarqubeConnectionMessage('Restored saved SonarQube connection.')
          authenticatedFetch(`${API_URL}/api/sonarqube/projects`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: configs.sonarqube.url || '', token: configs.sonarqube.token || '' }),
          }).then(async (projectResponse) => {
            const projectData = await projectResponse.json()
            if (projectResponse.ok && !cancelled) {
              const projects = projectData.projects || []
              setSonarqubeProjects(projects)
              if (!projects.some((project) => project.key === configs.sonarqube.selected_project)) {
                setSelectedSonarqubeProject('')
              }
            }
          }).catch(() => {})
        }
        if (configs.kubernetes) {
          setKubernetesConfig(configs.kubernetes)
          setKubernetesConnectionStatus('connected')
          setKubernetesConnectionMessage('Restored saved Kubernetes connection.')
        }
        if (configs.grafana) {
          setGrafanaUrl(configs.grafana.url || DEFAULT_GRAFANA_URL)
          setGrafanaDashboardUrl(configs.grafana.dashboard_url || PIPEGUARD_GRAFANA_DASHBOARD_URL)
        }
      } catch (error) {
        if (!cancelled) setPageError(error.message || 'Could not restore saved connections.')
      }
    }

    restoreIntegrationSettings()
    return () => { cancelled = true }
  }, [auth.access_token, fetchJenkinsJobs])

  // Once connected, retrieve current Jenkins data every 10 seconds.
  useEffect(() => {
    if (!jenkinsCredentials) return undefined

    const timer = window.setInterval(() => {
      fetchJenkinsJobs(jenkinsCredentials, true)
    }, 10000)

    return () => window.clearInterval(timer)
  }, [jenkinsCredentials, fetchJenkinsJobs])

  function openConfiguration(integration) {
    if (!['jenkins', 'github', 'prometheus', 'loki', 'sonarqube', 'kubernetes', 'grafana'].includes(integration.id)) {
      setPageError(`${integration.name} integration is not implemented yet.`)
      return
    }

    setPageError('')
    setConnectionMessage('')
    setGithubConnectionMessage('')
    setLokiConnectionMessage('')
    setSonarqubeConnectionMessage('')
    setSelectedIntegration(integration.id)
    setFormValues(
      integration.id === 'jenkins'
        ? {
            url: jenkinsCredentials?.url || 'http://192.168.56.10:8080',
            username: jenkinsCredentials?.username || '',
            token: jenkinsCredentials?.api_token || '',
          }
        : integration.id === 'prometheus'
          ? { url: prometheusUrl || 'http://192.168.56.10:9090' }
          : integration.id === 'loki'
            ? {
                url: lokiUrl || 'http://192.168.56.10:3100',
                project_name: lokiProjectName,
                username: lokiUsername,
                token: lokiToken,
              }
            : integration.id === 'sonarqube'
              ? { url: sonarqubeUrl || 'http://192.168.56.10:9000', token: sonarqubeToken }
              : integration.id === 'kubernetes'
                ? {
                    url: kubernetesConfig?.url || '',
                    namespace: kubernetesConfig?.namespace || 'pipeguard-lab',
                    token: kubernetesConfig?.token || '',
                    ca_certificate: kubernetesConfig?.ca_certificate || '',
                  }
              : integration.id === 'grafana'
                ? {
                    url: grafanaUrl || DEFAULT_GRAFANA_URL,
                    dashboard_url: grafanaDashboardUrl || PIPEGUARD_GRAFANA_DASHBOARD_URL,
                  }
          : integration.id === 'github'
            ? { token: githubToken }
            : { token: '' },
    )
  }

  async function saveConfiguration(event) {
    event.preventDefault()

    if (selectedIntegration === 'grafana') {
      const url = (formValues.url || '').trim()
      const dashboardUrl = (formValues.dashboard_url || PIPEGUARD_GRAFANA_DASHBOARD_URL).trim()
      if (!isPipeGuardGrafanaDashboardUrl(dashboardUrl, url)) {
        setGrafanaConnectionMessage('Use the PipeGuard dashboard URL or its Grafana shared-dashboard URL on the same Grafana server, not the PipeGuard frontend URL.')
        return
      }
      setIsConnecting(true)
      setGrafanaConnectionMessage('Checking Grafana health...')
      try {
        const response = await authenticatedFetch(`${API_URL}/api/grafana/test`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        })
        const data = await response.json()
        if (!response.ok) throw new Error(data.detail || `Grafana connection failed (HTTP ${response.status})`)
        await persistIntegrationSettings('grafana', { url: url.replace(/\/$/, ''), dashboard_url: dashboardUrl })
        setGrafanaUrl(url.replace(/\/$/, ''))
        setGrafanaDashboardUrl(dashboardUrl)
        setGrafanaConnectionMessage(`Grafana ${data.version} is healthy. Configure a build dashboard URL to open filtered build views.`)
        setIntegrations((previous) => previous.map((integration) =>
          integration.id === 'grafana' ? { ...integration, connected: true } : integration,
        ))
        setSelectedIntegration(null)
      } catch (error) {
        setGrafanaConnectionMessage(error.message || 'Could not connect to Grafana.')
      } finally {
        setIsConnecting(false)
      }
      return
    }

    if (selectedIntegration === 'kubernetes') {
      const credentials = {
        url: (formValues.url || '').trim(),
        namespace: (formValues.namespace || 'pipeguard-lab').trim(),
        token: formValues.token || '',
        ca_certificate: (formValues.ca_certificate || '').trim(),
      }
      setIsConnecting(true)
      setKubernetesConnectionStatus('connecting')
      setKubernetesConnectionMessage('Connecting to Kubernetes and checking namespace access...')
      try {
        const response = await authenticatedFetch(`${API_URL}/api/kubernetes/test`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(credentials),
        })
        const data = await response.json()
        if (!response.ok) throw new Error(data.detail || `Kubernetes connection failed (HTTP ${response.status})`)
        await persistIntegrationSettings('kubernetes', credentials)
        setKubernetesConfig(credentials)
        setKubernetesConnectionStatus('connected')
        setKubernetesConnectionMessage(
          `Connected to Kubernetes ${data.cluster_version}; namespace ${data.namespace} has ${data.counts.pods} pod(s) and ${data.counts.deployments} deployment(s).`,
        )
        setIntegrations((previous) => previous.map((integration) =>
          integration.id === 'kubernetes' ? { ...integration, connected: true } : integration,
        ))
        setSelectedIntegration(null)
      } catch (error) {
        setKubernetesConnectionStatus('disconnected')
        setKubernetesConnectionMessage(error.message || 'Could not connect to Kubernetes.')
      } finally {
        setIsConnecting(false)
      }
      return
    }

    if (selectedIntegration === 'sonarqube') {
      const url = (formValues.url || '').trim()
      const token = formValues.token || ''
      const credentials = { url, token }

      setIsConnecting(true)
      setSonarqubeConnectionStatus('connecting')
      setSonarqubeConnectionMessage('Testing SonarQube and loading projects...')
      try {
        const testResponse = await authenticatedFetch(`${API_URL}/api/sonarqube/test`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(credentials),
        })
        const testData = await testResponse.json()
        if (!testResponse.ok) {
          throw new Error(testData.detail || `SonarQube connection failed (HTTP ${testResponse.status})`)
        }

        const projectsResponse = await authenticatedFetch(`${API_URL}/api/sonarqube/projects`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(credentials),
        })
        const projectsData = await projectsResponse.json()
        if (!projectsResponse.ok) {
          throw new Error(projectsData.detail || `Could not load SonarQube projects (HTTP ${projectsResponse.status})`)
        }

        const projects = projectsData.projects || []
        const matchingProject = findMatchingSonarProject(projects, selectedGithubRepo)
        const selectedProject = projects.some((project) => project.key === selectedSonarqubeProject)
          ? selectedSonarqubeProject
          : (matchingProject?.key || '')
        await persistIntegrationSettings('sonarqube', { ...credentials, selected_project: selectedProject })
        setSonarqubeUrl(url)
        setSonarqubeToken(token)
        setSonarqubeProjects(projects)
        setSelectedSonarqubeProject(selectedProject)
        setIntegrations((previous) => previous.map((integration) =>
          integration.id === 'sonarqube' ? { ...integration, connected: true } : integration,
        ))
        setSonarqubeConnectionStatus('connected')
        setSonarqubeConnectionMessage(
          `Connected to SonarQube ${testData.version || ''}. ${projects.length} projects retrieved.`,
        )
        setSelectedIntegration(null)
      } catch (error) {
        setSonarqubeConnectionStatus('disconnected')
        setSonarqubeConnectionMessage(error.message || 'Could not connect to SonarQube.')
      } finally {
        setIsConnecting(false)
      }
      return
    }

    if (selectedIntegration === 'loki') {
      const url = (formValues.url || '').trim()
      const projectName = (formValues.project_name || '').trim()
      const username = (formValues.username || '').trim()
      const token = formValues.token || ''
      const credentials = { url, project_name: projectName, username, token }

      setIsConnecting(true)
      setLokiConnectionStatus('connecting')
      setLokiConnectionMessage('Testing Loki and configuring Alloy for this project label...')
      try {
        const testResponse = await authenticatedFetch(`${API_URL}/api/loki/test`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(credentials),
        })
        const testData = await testResponse.json()
        if (!testResponse.ok) {
          throw new Error(testData.detail || `Loki connection failed (HTTP ${testResponse.status})`)
        }

        const labelsResponse = await authenticatedFetch(`${API_URL}/api/loki/labels`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(credentials),
        })
        const labelsData = await labelsResponse.json()
        if (!labelsResponse.ok) {
          throw new Error(labelsData.detail || `Could not retrieve Loki labels (HTTP ${labelsResponse.status})`)
        }

        await persistIntegrationSettings('loki', credentials)
        setLokiUrl(url)
        setLokiProjectName(projectName)
        setLokiUsername(username)
        setLokiToken(token)
        setIntegrations((previous) => previous.map((integration) =>
          integration.id === 'loki' ? { ...integration, connected: true } : integration,
        ))
        setLokiConnectionStatus('connected')
        setLokiConnectionMessage(
          `Loki connected; Alloy is watching containers labeled for "${projectName}". ${labelsData.labels?.length || 0} stream labels available.`,
        )
      } catch (error) {
        setLokiConnectionStatus('disconnected')
        setLokiConnectionMessage(error.message || 'Could not connect to Loki.')
      } finally {
        setIsConnecting(false)
      }
      return
    }

    if (selectedIntegration === 'prometheus') {
      const url = (formValues.url || '').trim()

      setIsConnecting(true)
      setPrometheusConnectionStatus('connecting')
      setPrometheusConnectionMessage('Testing Prometheus and retrieving active targets...')

      try {
        const testResponse = await authenticatedFetch(`${API_URL}/api/prometheus/test`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        })
        const testData = await testResponse.json()

        if (!testResponse.ok) {
          throw new Error(testData.detail || `Prometheus connection failed (HTTP ${testResponse.status})`)
        }

        const targetsResponse = await authenticatedFetch(`${API_URL}/api/prometheus/targets`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        })
        const targetsData = await targetsResponse.json()

        if (!targetsResponse.ok) {
          throw new Error(targetsData.detail || `Could not retrieve Prometheus targets (HTTP ${targetsResponse.status})`)
        }

        const targets = targetsData.targets || []
        await persistIntegrationSettings('prometheus', { url })
        setPrometheusUrl(url)
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

      try {
        const testResponse = await authenticatedFetch(`${API_URL}/api/github/test`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token }),
        })
        const testData = await testResponse.json()

        if (!testResponse.ok) {
          throw new Error(testData.detail || `GitHub connection failed (HTTP ${testResponse.status})`)
        }

        const repoResponse = await authenticatedFetch(`${API_URL}/api/github/repositories`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token }),
        })
        const repoData = await repoResponse.json()

        if (!repoResponse.ok) {
          throw new Error(repoData.detail || `Could not load repositories (HTTP ${repoResponse.status})`)
        }

        const repositories = repoData.repositories || []
        const selectedRepository = repositories[0]?.full_name || ''
        await persistIntegrationSettings('github', { token, selected_repository: selectedRepository })
        setGithubToken(token)
        setGithubRepositories(repositories)
        setSelectedGithubRepo(selectedRepository)
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
      const response = await authenticatedFetch(`${API_URL}/api/jenkins/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(credentials),
      })

      const data = await response.json()

      if (!response.ok) {
        throw new Error(data.detail || `Connection failed (HTTP ${response.status})`)
      }

      await persistIntegrationSettings('jenkins', credentials)
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

  async function refreshNow() {
    if (!jenkinsCredentials) {
      setPageError('Connect Jenkins first to retrieve real pipeline data.')
      return
    }

    await fetchJenkinsJobs(jenkinsCredentials)
  }

  function selectGithubRepository(repository) {
    setSelectedGithubRepo(repository)
    void persistIntegrationSettings('github', { token: githubToken, selected_repository: repository })
      .catch((error) => setGithubConnectionMessage(`Repository changed, but could not save it: ${error.message}`))
  }

  function selectSonarqubeProject(projectKey) {
    setSelectedSonarqubeProject(projectKey)
    void persistIntegrationSettings('sonarqube', {
      url: sonarqubeUrl,
      token: sonarqubeToken,
      selected_project: projectKey,
    }).catch((error) => setSonarqubeConnectionMessage(`Project changed, but could not save it: ${error.message}`))
  }

  const connectedCount = integrations.filter(
    (integration) => integration.connected,
  ).length

  const failedJobs = jenkinsJobs.filter(
    (job) => getBuildStatus(job) === 'FAILURE',
  ).length

  const connectedTools = integrations.filter((integration) => integration.connected)
  const toolConfigurations = {
    github: { token: githubToken },
    prometheus: { url: prometheusUrl },
    loki: { url: lokiUrl, project_name: lokiProjectName, username: lokiUsername, token: lokiToken },
    sonarqube: { url: sonarqubeUrl, token: sonarqubeToken },
    kubernetes: kubernetesConfig,
    grafana: { url: grafanaUrl, dashboard_url: grafanaDashboardUrl },
  }
  const currentToolId = activePage.startsWith('tool:') ? activePage.slice(5) : ''
  const pageTitle = currentToolId
    ? integrations.find((integration) => integration.id === currentToolId)?.name || 'Tool'
    : ({ dashboard: 'Dashboard', pipelines: 'Pipelines', incidents: 'Investigations', settings: 'Integrations' }[activePage] || 'Dashboard')

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

        <div className="nav-label">PLATFORM</div>
        <nav className="navigation">
          {navigation.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${activePage === item.id ? 'active' : ''}`}
              aria-current={activePage === item.id ? 'page' : undefined}
              aria-label={item.id === 'incidents' ? `Investigations, ${incidentHistoryCount} saved` : item.label}
              title={item.label}
              onClick={() => {
                setActivePage(item.id)
                setPageError('')
              }}
            >
              <span className="nav-icon">{item.icon}</span>
              {item.label}
              {item.id === 'incidents' && (
                <span className="nav-count">{incidentHistoryCount}</span>
              )}
            </button>
          ))}
        </nav>

        {connectedTools.some((integration) => integration.id !== 'jenkins') && (
          <>
            <div className="nav-label connected-tools-label">CONNECTED TOOLS</div>
            <nav className="navigation connected-tool-navigation" aria-label="Connected tools">
              {connectedTools.filter((integration) => integration.id !== 'jenkins').map((integration) => (
                <button
                  key={integration.id}
                  className={`nav-item ${activePage === `tool:${integration.id}` ? 'active' : ''}`}
                  aria-current={activePage === `tool:${integration.id}` ? 'page' : undefined}
                  onClick={() => { setActivePage(`tool:${integration.id}`); setPageError('') }}
                >
                  <span className="nav-icon">{integration.icon}</span>{integration.name}
                </button>
              ))}
            </nav>
          </>
        )}

        <div className="nav-label settings-label">CONFIGURATION</div>
        <button
          className={`nav-item ${activePage === 'settings' ? 'active' : ''}`}
          aria-current={activePage === 'settings' ? 'page' : undefined}
          aria-label="Integrations"
          title="Integrations"
          onClick={() => {
            setActivePage('settings')
            setPageError('')
          }}
        >
          <span className="nav-icon">⚙</span>
          Integrations
        </button>

        <div className="sidebar-bottom">
          <div className="user-profile">
            <div className="user-avatar">{(auth.user.name || auth.user.email).slice(0, 2).toUpperCase()}</div>
            <div className="user-details">
              <strong>{auth.user.name || auth.user.email.split('@')[0]}</strong>
              <small>{auth.user.email}</small>
            </div>
            <button className="profile-menu sign-out-button" onClick={onSignOut} title="Sign out">Sign out</button>
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb">
            <span>PipeGuard</span>
            <span className="breadcrumb-divider">/</span>
            <strong>{pageTitle}</strong>
          </div>
          <div className="topbar-right">
            <span className="environment-badge" aria-live="polite">
              <span className={`status-dot ${connectionStatus === 'connected' ? 'green' : 'gray'}`} />
              {connectionStatus === 'connected'
                ? 'Jenkins connected'
                : connectionStatus === 'connecting'
                  ? 'Connecting...'
                  : 'Not connected'}
            </span>
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
              <section className="page-heading dashboard-heading">
                <div>
                  <div className="eyebrow">OVERVIEW</div>
                  <h1>Pipeline health<span className="heading-period">.</span></h1>
                  <p className="page-subtitle">See what needs attention, then investigate the build with its evidence.</p>
                </div>
                <button className="primary-button" onClick={() => setActivePage(jenkinsCredentials ? 'pipelines' : 'settings')}>
                  {jenkinsCredentials ? 'View pipelines' : 'Connect Jenkins'} <span aria-hidden="true">→</span>
                </button>
              </section>

              <section className="metrics-grid dashboard-metrics" aria-label="Pipeline summary">
                <MetricCard label="Jenkins jobs" value={jenkinsCredentials ? String(jenkinsJobs.length) : '—'} note={jenkinsCredentials ? 'Monitored jobs' : 'Connect Jenkins to begin'} icon="⑂" tone="blue" />
                <MetricCard label="Failed latest builds" value={jenkinsCredentials ? String(failedJobs) : '—'} note="Builds that may need investigation" icon="!" tone="orange" />
                <MetricCard label="Saved investigations" value={String(incidentHistoryCount)} note="Your investigation history" icon="◈" tone="purple" />
                <MetricCard label="Connected sources" value={String(connectedCount)} note="Configured provider connections" icon="⌘" tone="green" />
              </section>

              <section className="section-heading dashboard-section-heading">
                <div>
                  <h2>{failedJobs ? 'Needs attention' : 'Latest builds'}</h2>
                  <p>{jenkinsCredentials ? 'Recent Jenkins results. Investigate a failed build to collect its evidence.' : 'Connect Jenkins to see real jobs and build results.'}</p>
                </div>
                {jenkinsCredentials && <button className="text-button" onClick={() => setActivePage('pipelines')}>All pipelines <span aria-hidden="true">→</span></button>}
              </section>

              {jenkinsCredentials ? (
                <JobsTable jobs={[...jenkinsJobs].sort((a, b) => Number(getBuildStatus(b) === 'FAILURE') - Number(getBuildStatus(a) === 'FAILURE')).slice(0, 5)} onInvestigate={investigateBuild} investigationInProgress={isInvestigating} compact />
              ) : (
                <div className="empty-state dashboard-empty-state">
                  <strong>Start with your CI pipeline</strong>
                  <p>Connect Jenkins to load jobs, identify failed builds, and launch evidence-backed investigations.</p>
                  <button className="primary-button" onClick={() => openConfiguration(integrations.find((item) => item.id === 'jenkins'))}>Connect Jenkins <span aria-hidden="true">→</span></button>
                </div>
              )}
              {lastRefresh && jenkinsCredentials && <p className="data-caption dashboard-refresh-caption">Updated {lastRefresh.toLocaleTimeString()} · refreshes automatically every 10 seconds</p>}
            </>
          )}

          {activePage === 'settings' && (
            <>
              <section className="page-heading">
                <div>
                  <div className="eyebrow">CONNECTIONS</div>
                  <h1>Integrations<span className="heading-period">.</span></h1>
                  <p className="page-subtitle">Connect the sources PipeGuard uses to investigate builds.</p>
                </div>
              </section>
              <section className="model-mode-card" aria-labelledby="model-mode-title">
                <div>
                  <div className="eyebrow">INVESTIGATION MODEL</div>
                  <h2 id="model-mode-title">Local QLoRA model</h2>
                  <p>{qloraEnabled
                    ? 'QLoRA is requested for Monitoring and supported Remediation steps. Start Ollama to use it; if Ollama is stopped or unavailable, those steps fall back to Groq. The model unloads after each call.'
                    : 'The standard Groq pipeline is active. Enable QLoRA to use the local model for supported investigation steps.'}</p>
                </div>
                <button
                  type="button"
                  className={`model-mode-toggle ${qloraEnabled ? 'is-enabled' : ''}`}
                  role="switch"
                  aria-checked={qloraEnabled}
                  aria-label="Use local QLoRA model"
                  onClick={() => setQloraEnabled((enabled) => !enabled)}
                >
                  <span className="model-mode-toggle-thumb" />
                  <span>{qloraEnabled ? 'QLoRA on' : 'Groq on'}</span>
                </button>
              </section>
              <IntegrationGrid integrations={integrations} onConfigure={openConfiguration} onOpenTool={(id) => setActivePage(id === 'jenkins' ? 'pipelines' : `tool:${id}`)} />
              <div className="notice-box">
                <span>ⓘ</span>
                <p>
                  <strong>Connect only the providers you want to use.</strong> Loki is optional;
                  when connected, its logs are collected for the selected build time window.
                  Trivy is integrated through Jenkins and appears when a build has an archived scan report.
                </p>
              </div>
            </>
          )}

          {currentToolId && (
            <ConnectedToolPage
              toolId={currentToolId}
              configuration={toolConfigurations[currentToolId]}
              repositories={githubRepositories}
              selectedRepository={selectedGithubRepo}
              onSelectRepository={selectGithubRepository}
              sonarProjects={sonarqubeProjects}
              selectedSonarProject={selectedSonarqubeProject}
              onSelectSonarProject={selectSonarqubeProject}
              connectionMessage={{
                github: githubConnectionMessage,
                prometheus: prometheusConnectionMessage,
                loki: lokiConnectionMessage,
                sonarqube: sonarqubeConnectionMessage,
                kubernetes: kubernetesConnectionMessage,
                grafana: grafanaConnectionMessage,
              }[currentToolId]}
              onReconnect={() => openConfiguration(integrations.find((item) => item.id === currentToolId))}
            />
          )}

          {activePage === 'pipelines' && (
            <>
              <section className="page-heading">
                <div>
                  <div className="eyebrow">CI/CD</div>
                  <h1>Pipelines<span className="heading-period">.</span></h1>
                  <p className="page-subtitle">Actual Jenkins jobs and their latest builds.</p>
                </div>
                <div className="pipeline-page-actions">
                  <PersonalizationToggle
                    enabled={personalizationEnabled}
                    onChange={changePersonalization}
                    apiUrl={API_URL}
                    accessToken={auth.access_token}
                  />
                  <button className="primary-button" onClick={refreshNow} disabled={isRefreshing || !jenkinsCredentials}>
                    {isRefreshing ? 'Refreshing…' : '↻ Refresh jobs'}
                  </button>
                </div>
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
            <IncidentsDashboard
              apiUrl={API_URL}
              accessToken={auth.access_token}
            />
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
                sonarqubeUrl={sonarqubeUrl}
                grafanaUrl={grafanaUrl}
                grafanaDashboardUrl={grafanaDashboardUrl}
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
                  {field.multiline ? (
                    <textarea
                      rows={5}
                      placeholder={field.placeholder}
                      value={formValues[field.name] || ''}
                      onChange={(event) => setFormValues((previous) => ({ ...previous, [field.name]: event.target.value }))}
                      required={field.required !== false}
                      autoComplete="off"
                      spellCheck="false"
                    />
                  ) : (
                    <input
                      type={field.secret ? 'password' : 'text'}
                      placeholder={field.placeholder}
                      value={formValues[field.name] || ''}
                      onChange={(event) => setFormValues((previous) => ({
                        ...previous,
                        [field.name]: event.target.value,
                      }))}
                      required={field.required !== false}
                      autoComplete={field.secret ? 'new-password' : 'off'}
                    />
                  )}
                </label>
              ))}

              {(selectedIntegration === 'github'
                ? githubConnectionMessage
                : selectedIntegration === 'prometheus'
                  ? prometheusConnectionMessage
                  : selectedIntegration === 'loki'
                    ? lokiConnectionMessage
                    : selectedIntegration === 'sonarqube'
                      ? sonarqubeConnectionMessage
                      : selectedIntegration === 'kubernetes'
                        ? kubernetesConnectionMessage
                        : selectedIntegration === 'grafana'
                          ? grafanaConnectionMessage
                        : connectionMessage) && (
                <div className={`form-feedback ${
                  (selectedIntegration === 'github'
                    ? githubConnectionStatus
                    : selectedIntegration === 'prometheus'
                      ? prometheusConnectionStatus
                      : selectedIntegration === 'loki'
                        ? lokiConnectionStatus
                        : selectedIntegration === 'sonarqube'
                          ? sonarqubeConnectionStatus
                          : selectedIntegration === 'kubernetes'
                            ? kubernetesConnectionStatus
                            : connectionStatus) === 'connected'
                    ? 'feedback-success'
                    : 'feedback-error'
                }`}>
                  {selectedIntegration === 'github'
                    ? githubConnectionMessage
                    : selectedIntegration === 'prometheus'
                      ? prometheusConnectionMessage
                      : selectedIntegration === 'loki'
                        ? lokiConnectionMessage
                        : selectedIntegration === 'sonarqube'
                          ? sonarqubeConnectionMessage
                          : selectedIntegration === 'kubernetes'
                            ? kubernetesConnectionMessage
                            : selectedIntegration === 'grafana'
                              ? grafanaConnectionMessage
                            : connectionMessage}
                </div>
              )}

              <div className="form-notice">
                <span>ⓘ</span>
                {selectedIntegration === 'prometheus'
                  ? 'PipeGuard queries the configured Prometheus HTTP API through its backend.'
                  : selectedIntegration === 'loki'
                    ? 'PipeGuard starts a managed Alloy collector in the configured Vagrant VM. It watches running containers labeled with this project name and records their Jenkins build label. Its Loki token is stored in a mode-600 secret file on that VM.'
                    : selectedIntegration === 'sonarqube'
                      ? 'PipeGuard uses this token to read project quality metrics, issues, and the latest analysis revision from SonarQube. Credentials stay in memory for this session.'
                      : selectedIntegration === 'kubernetes'
                        ? 'PipeGuard connects directly to the Kubernetes API using a namespace-scoped read-only token and the cluster CA certificate. Credentials stay in memory for this session.'
                    : 'Credentials are sent to the local PipeGuard backend and kept in memory only for this session.'}
              </div>
              {selectedIntegration === 'kubernetes' && (
                <p className="kubernetes-setup-hint">
                  Use a read-only service-account token scoped to this namespace. Paste the API server CA certificate in PEM format; PipeGuard verifies it and keeps both values in memory for this session.
                </p>
              )}
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
      <IncidentChatbot apiUrl={API_URL} accessToken={auth.access_token} />
    </div>
  )
}

function MetricCard({ label, value, note, icon, tone }) {
  return (
    <article className="metric-card">
      <div className={`metric-icon ${tone}`} aria-hidden="true">{icon}</div>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      <div className="metric-note">{note}</div>
    </article>
  )
}

function JobsTable({ jobs, onInvestigate, investigationInProgress, compact = false }) {
  if (!jobs.length) {
    return (
      <div className="empty-state">
        <strong>No Jenkins jobs found</strong>
        <p>The connection worked, but Jenkins returned an empty jobs list.</p>
      </div>
    )
  }

  return (
    <div className={`jobs-table-wrap${compact ? ' jobs-table-wrap-compact' : ''}`}>
      <table className="jobs-table">
        <caption className="visually-hidden">Latest Jenkins build status by job</caption>
        <thead>
          <tr>
            <th>Job</th>
            <th>Latest build</th>
            <th>Status</th>
            {!compact && <th>Duration</th>}
            <th>Last run</th>
            {!compact && <th>Jenkins</th>}
            <th>Action</th>
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
                {!compact && <td>{formatDuration(job.last_build?.duration_ms)}</td>}
                <td>{formatTimestamp(job.last_build?.timestamp)}</td>
                {!compact && <td>
                  <a className="job-link" href={job.url} target="_blank" rel="noreferrer">
                    Open ↗
                  </a>
                </td>}
                <td>
                  {status === 'FAILURE' && job.last_build?.number ? (
                    <button
                      className="investigate-button"
                      onClick={() => onInvestigate?.(job)}
                      disabled={!onInvestigate || investigationInProgress}
                    >
                      {investigationInProgress ? 'Investigating…' : `Investigate #${job.last_build.number} →`}
                    </button>
                  ) : (
                    <span className="data-caption">No action</span>
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

function BuildInvestigation({ investigation, sonarqubeUrl, grafanaUrl, grafanaDashboardUrl, isLoading, activeStage, onStageChange, onDecision, onOpenFeedback }) {
  const [hiddenMetricQueries, setHiddenMetricQueries] = useState([])

  if (!investigation) return null

  const evidence = investigation.evidence
  const github = investigation.github
  let grafanaBuildUrl = ''
  if (grafanaUrl && grafanaDashboardUrl && evidence?.timestamp) {
    try {
      const dashboard = new URL(grafanaDashboardUrl, grafanaUrl)
      dashboard.searchParams.set('from', String(Math.max(0, evidence.timestamp - 300000)))
      dashboard.searchParams.set('to', String(evidence.timestamp + (evidence.duration_ms || 0) + 300000))
      dashboard.searchParams.set('var-jenkins_job', investigation.jobName)
      dashboard.searchParams.set('var-build_number', String(investigation.buildNumber))
      grafanaBuildUrl = dashboard.toString()
    } catch {
      grafanaBuildUrl = ''
    }
  }

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
          Collecting evidence from Jenkins and any connected providers…
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
          {grafanaBuildUrl && (
            <a className="job-link grafana-build-link" href={grafanaBuildUrl} target="_blank" rel="noreferrer">
              Open this build’s Grafana dashboard ↗
            </a>
          )}

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
            <summary>
              Trivy image vulnerability scan
              <span className={`trivy-evidence-status ${evidence.trivy?.status === 'success' ? 'is-connected' : ''}`}>
                <span className={`status-dot ${evidence.trivy?.status === 'success' ? 'green' : 'gray'}`} />
                {evidence.trivy?.status === 'success'
                  ? 'Report connected'
                  : evidence.trivy?.status === 'not_found'
                    ? 'No report for this build'
                    : 'Via Jenkins'}
              </span>
            </summary>
            <p className="data-caption">
              Scan results archived by Jenkins for this build's exact container image.
            </p>
            {evidence.trivy?.status === 'success' ? (
              <>
                <p className="provider-message">
                  {evidence.trivy.artifact_name || 'Image'} · {evidence.trivy.finding_count ?? 0} vulnerability(ies)
                  {evidence.trivy.generated_at ? ` · scanned ${formatTimestamp(evidence.trivy.generated_at)}` : ''}
                  {evidence.trivy.findings_truncated ? ' · showing first 25' : ''}
                </p>
                <div className="prometheus-summary">
                  {Object.entries(evidence.trivy.counts || {}).map(([severity, count]) => (
                    <div key={severity}><span>{severity}</span><strong>{count}</strong></div>
                  ))}
                </div>
                {evidence.trivy.findings?.length ? (
                  <div className="investigation-table-wrap">
                    <table className="prometheus-table">
                      <thead><tr><th>Severity</th><th>Vulnerability</th><th>Package</th><th>Installed</th><th>Fixed in</th><th>Details</th></tr></thead>
                      <tbody>
                        {evidence.trivy.findings.map((finding, index) => (
                          <tr key={`${finding.id || 'finding'}-${finding.package || index}`}>
                            <td>{finding.severity || 'UNKNOWN'}</td>
                            <td>{finding.url ? <a className="job-link" href={finding.url} target="_blank" rel="noreferrer">{finding.id || 'Details'} ↗</a> : finding.id || '—'}</td>
                            <td>{finding.package || '—'}</td>
                            <td>{finding.installed_version || '—'}</td>
                            <td>{finding.fixed_version || '—'}</td>
                            <td>{finding.title || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : <p className="data-caption">Trivy found no image vulnerabilities.</p>}
              </>
            ) : (
              <p className="provider-message">
                {evidence.trivy?.message || 'Trivy evidence is unavailable for this build.'}
              </p>
            )}
            {evidence.url && evidence.trivy?.status === 'success' && (
              <a className="job-link" href={`${evidence.url.replace(/\/$/, '')}/artifact/${evidence.trivy.artifact_path || 'reports/trivy-image.json'}`} target="_blank" rel="noreferrer">
                Open full Trivy JSON report in Jenkins ↗
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

          <details className="investigation-section">
            <summary>Loki logs around the build</summary>
            <p className="data-caption">
              {investigation.loki?.status === 'success'
                ? `LogQL ${investigation.loki.query} · five minutes before build start through five minutes after completion.`
                : 'Loki is optional; connect it in Settings to include service logs.'}
            </p>
            {investigation.loki?.status === 'success' ? (
              investigation.loki.lines?.length ? (
                <div className="investigation-table-wrap">
                  <table className="prometheus-table">
                    <thead><tr><th>Time</th><th>Stream labels</th><th>Log line</th></tr></thead>
                    <tbody>
                      {investigation.loki.lines.map((entry, index) => (
                        <tr key={`${entry.timestamp_ms}-${index}`}>
                          <td>{entry.timestamp_ms ? formatTimestamp(entry.timestamp_ms) : 'Not available'}</td>
                          <td>{Object.entries(entry.labels || {}).map(([key, value]) => `${key}=${value}`).join(', ') || '—'}</td>
                          <td><span className="loki-log-line">{entry.line || '—'}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="data-caption">Loki connected, but no logs matched this query and time window.</p>
              )
            ) : (
              <p className="provider-message">
                {investigation.loki?.message || 'Loki evidence is not available.'}
              </p>
            )}
          </details>

          <details className="investigation-section">
            <summary>SonarQube quality and security evidence</summary>
            <p className="data-caption">
              Project measures and issues from the latest SonarQube analysis. The analysis revision is compared with this Jenkins build's commit when available.
            </p>
            {investigation.sonarqube?.status === 'success' ? (
              <>
                <p className="provider-message">
                  Project: {investigation.sonarqube.project_key}
                  {investigation.sonarqube.analysis?.date
                    ? ` · Analysis: ${formatTimestamp(investigation.sonarqube.analysis.date)}` : ''}
                  {investigation.sonarqube.analysis_matches_build === true
                    ? ' · Analysis commit matches this build'
                    : investigation.sonarqube.analysis_matches_build === false
                      ? ' · Latest analysis is for a different commit; findings are project context only'
                      : ' · Could not verify analysis commit against this build'}
                </p>
                {investigation.sonarqube.quality_gate?.status && (
                  <p className="provider-message">
                    Quality gate: <strong>{investigation.sonarqube.quality_gate.status}</strong>
                  </p>
                )}
                {investigation.sonarqube.quality_gate_error && (
                  <p className="provider-message">Quality gate status unavailable: {investigation.sonarqube.quality_gate_error}</p>
                )}
                {sonarqubeUrl && investigation.sonarqube.project_key && (
                  <a
                    className="job-link"
                    href={`${sonarqubeUrl.replace(/\/$/, '')}/dashboard?id=${encodeURIComponent(investigation.sonarqube.project_key)}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open project in SonarQube ↗
                  </a>
                )}
                {Object.keys(investigation.sonarqube.measures || {}).length > 0 && (
                  <div className="investigation-table-wrap">
                    <table className="prometheus-table">
                      <thead><tr><th>Measure</th><th>Value</th></tr></thead>
                      <tbody>
                        {Object.entries(investigation.sonarqube.measures).map(([metric, value]) => (
                          <tr key={metric}>
                            <td>{metric.replaceAll('_', ' ')}</td>
                            <td>{value}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <p className="provider-message">
                  {investigation.sonarqube.issue_count} issue(s) returned for this project.
                </p>
                {investigation.sonarqube.issues?.length ? (
                  <div className="investigation-table-wrap">
                    <table className="prometheus-table">
                      <thead><tr><th>Severity</th><th>Type</th><th>Rule</th><th>Location</th><th>Issue</th></tr></thead>
                      <tbody>
                        {investigation.sonarqube.issues.map((issue) => (
                          <tr key={issue.key}>
                            <td>{issue.severity || '—'}</td>
                            <td>{issue.type || '—'}</td>
                            <td>{issue.rule || '—'}</td>
                            <td>{issue.component || '—'}{issue.line ? `:${issue.line}` : ''}</td>
                            <td>{issue.message || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : <p className="data-caption">No open SonarQube issues were returned for this project.</p>}
              </>
            ) : (
              <p className="provider-message">
                {investigation.sonarqube?.message || 'SonarQube evidence is not available.'}
              </p>
            )}
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

              <section className="notice-box" aria-live="polite">
                {investigation.agentReports.personalizationEnabled
                  ? investigation.agentReports.policyVersionUsed != null
                    ? `Personalized remediation guidance applied (policy v${investigation.agentReports.policyVersionUsed}).`
                    : 'Personalization was requested, but no active policy was applied. This run used standard remediation guidance.'
                  : 'Personalization was off. This run used standard remediation guidance.'}
              </section>

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

function IntegrationGrid({ integrations, onConfigure, onOpenTool }) {
  const [search, setSearch] = useState('')
  const normalizedSearch = search.trim().toLowerCase()
  const groups = toolCatalog.map((group) => ({
    ...group,
    tools: group.tools.filter((tool) => !normalizedSearch || `${tool.name} ${tool.description} ${group.objective}`.toLowerCase().includes(normalizedSearch)),
  })).filter((group) => group.tools.length)
  const connectedTools = integrations.filter((integration) => integration.connected)

  return (
    <div className="integrations-workspace">
      <section className="tool-catalog" aria-labelledby="tool-catalog-title">
        <div className="tool-catalog-heading">
          <div><h2 id="tool-catalog-title">Find a tool</h2><p>Browse by DevOps objective, or search by name.</p></div>
          <label className="tool-search">
            <span className="visually-hidden">Search tools</span>
            <span aria-hidden="true">⌕</span>
            <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search tools…" />
          </label>
        </div>
        {groups.length ? groups.map((group) => (
          <section className="tool-objective" key={group.objective} aria-label={group.objective}>
            <h3>{group.objective}</h3>
            <div className="tool-choice-grid">
              {group.tools.map((tool) => {
                const connected = integrations.find((integration) => integration.id === tool.id)?.connected
                const unavailable = !tool.available && !tool.integrated
                return (
                  <button
                    className={`tool-choice ${unavailable ? 'is-unavailable' : ''} ${connected ? 'is-connected' : ''}`}
                    key={tool.name}
                    type="button"
                    disabled={unavailable || Boolean(tool.integrated)}
                    title={unavailable ? tool.hint : tool.integrated || (connected ? `Manage ${tool.name}` : `Connect ${tool.name}`)}
                    onClick={() => tool.integrated ? undefined : connected ? onOpenTool(tool.id) : onConfigure(integrations.find((item) => item.id === tool.id))}
                  >
                    <span className={`integration-icon ${tool.color}`} aria-hidden="true">{tool.icon}</span>
                    <span className="tool-choice-copy"><strong>{tool.name}</strong><small>{tool.description}</small></span>
                    <span className={`tool-choice-action ${unavailable ? 'is-muted' : ''}`}>
                      {tool.integrated ? tool.integrated : unavailable ? tool.hint : connected ? 'Open tool' : 'Connect'}
                    </span>
                  </button>
                )
              })}
            </div>
          </section>
        )) : <div className="empty-state"><strong>No tools match “{search}”</strong><p>Try another name or DevOps objective.</p></div>}
      </section>

      <section className="connected-tools-section" aria-labelledby="connected-tools-title">
        <div className="section-heading"><div><h2 id="connected-tools-title">Connected tools</h2><p>Open each tool’s own workspace to manage its live data.</p></div></div>
        {connectedTools.length ? (
          <div className="connected-tool-cards">
            {connectedTools.map((integration) => (
              <article className="connected-tool-card" key={integration.id}>
                <div className="integration-card-top">
                  <div className={`integration-icon ${integration.color}`} aria-hidden="true">{integration.icon}</div>
                  <span className="connection-status"><span className="status-dot green" />Connected</span>
                </div>
                <h3>{integration.name}</h3>
                <p>{integration.description}</p>
                <button className="primary-button" onClick={() => onOpenTool(integration.id)}>
                  Open {integration.id === 'jenkins' ? 'pipelines' : integration.name} <span aria-hidden="true">→</span>
                </button>
              </article>
            ))}
          </div>
        ) : (
          <div className="empty-state connected-tools-empty"><strong>No tools connected yet</strong><p>Choose a supported tool above to connect it. Its card and workspace will appear here after the connection succeeds.</p></div>
        )}
      </section>
    </div>
  )
}

function ConnectedToolPage({ toolId, configuration, repositories, selectedRepository, onSelectRepository, sonarProjects, selectedSonarProject, onSelectSonarProject, connectionMessage, onReconnect }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)
  const [metricQuery, setMetricQuery] = useState('up')
  const [metricResult, setMetricResult] = useState(null)
  const [buildNumber, setBuildNumber] = useState('1')
  const [logResult, setLogResult] = useState(null)
  const selectedRepositoryParts = (selectedRepository || '').split('/')
  const title = ({ github: 'GitHub', prometheus: 'Prometheus', loki: 'Loki', sonarqube: 'SonarQube', kubernetes: 'Kubernetes', grafana: 'Grafana' })[toolId] || 'Connected tool'

  useEffect(() => {
    if (!configuration) return undefined
    const controller = new AbortController()
    const load = async () => {
      setLoading(true)
      setError('')
      try {
        let endpoint
        let body = configuration
        if (toolId === 'grafana') {
          endpoint = '/api/grafana/test'
          body = { url: configuration.url }
        } else if (toolId === 'github') {
          if (!selectedRepositoryParts[0] || !selectedRepositoryParts[1]) {
            setData({ commits: [] })
            setLoading(false)
            return
          }
          endpoint = '/api/github/commits'
          body = { token: configuration.token, owner: selectedRepositoryParts[0], repository: selectedRepositoryParts[1], per_page: 20 }
        } else if (toolId === 'prometheus') endpoint = '/api/prometheus/targets'
        else if (toolId === 'loki') endpoint = '/api/loki/labels'
        else if (toolId === 'sonarqube') {
          if (!selectedSonarProject) { setData({ projects: sonarProjects }); setLoading(false); return }
          endpoint = '/api/sonarqube/evidence'
          body = { ...configuration, project_key: selectedSonarProject, limit: 30 }
        } else if (toolId === 'kubernetes') endpoint = '/api/kubernetes/overview'
        else return
        const response = await authenticatedFetch(`${API_URL}${endpoint}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: controller.signal })
        const result = await response.json()
        if (!response.ok) throw new Error(result.detail || `${title} returned HTTP ${response.status}`)
        setData(result)
      } catch (loadError) {
        if (loadError.name !== 'AbortError') setError(loadError.message || `Could not load ${title} data.`)
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    load()
    return () => controller.abort()
  // Load live provider data when the tool page opens or its selected resource changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toolId, configuration?.url, configuration?.token, configuration?.project_name, configuration?.namespace, configuration?.username, selectedRepository, selectedSonarProject, refreshKey])

  async function runPrometheusQuery(event) {
    event.preventDefault()
    setError('')
    try {
      const response = await authenticatedFetch(`${API_URL}/api/prometheus/query`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: configuration.url, query: metricQuery }) })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || `Prometheus returned HTTP ${response.status}`)
      setMetricResult(result)
    } catch (queryError) { setError(queryError.message || 'Prometheus query failed.') }
  }

  async function runLokiQuery(event) {
    event.preventDefault()
    setError('')
    const end = Date.now() / 1000
    try {
      const response = await authenticatedFetch(`${API_URL}/api/loki/query-range`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...configuration, build_number: buildNumber, start: end - 3600, end, limit: 200 }) })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || `Loki returned HTTP ${response.status}`)
      setLogResult(result)
    } catch (queryError) { setError(queryError.message || 'Loki query failed.') }
  }

  return (
    <section className="connected-tool-page">
      <header className="page-heading tool-page-heading">
        <div><div className="eyebrow">CONNECTED TOOL</div><h1>{title}<span className="heading-period">.</span></h1><p className="page-subtitle">{connectionMessage || `Manage ${title} and inspect its live data.`}</p></div>
        <div className="tool-page-actions">
          <button className="secondary-button" onClick={() => setRefreshKey((value) => value + 1)} disabled={loading}>↻ Refresh data</button>
          <button className="text-button" onClick={onReconnect}>Connection settings</button>
        </div>
      </header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {loading && <p className="provider-message" role="status">Loading live {title} data…</p>}

      {toolId === 'github' && (
        <>
          <div className="tool-management-card"><label className="form-field"><span>Repository</span><select value={selectedRepository} onChange={(event) => onSelectRepository(event.target.value)}>{repositories.map((repo) => <option value={repo.full_name} key={repo.id}>{repo.full_name}</option>)}</select></label><span className="tool-data-count">{repositories.length} accessible repositories</span></div>
          {data?.commits?.length ? <div className="jobs-table-wrap"><table className="jobs-table"><caption className="visually-hidden">Recent GitHub commits</caption><thead><tr><th>Commit</th><th>Author</th><th>Date</th><th>Revision</th></tr></thead><tbody>{data.commits.map((commit) => <tr key={commit.sha}><td><strong>{(commit.message || 'No message').split('\n')[0]}</strong></td><td>{commit.author || 'Unknown'}</td><td>{formatTimestamp(commit.date)}</td><td><a className="job-link" href={commit.html_url} target="_blank" rel="noreferrer">{commit.sha?.slice(0, 7)} ↗</a></td></tr>)}</tbody></table></div> : !loading && <div className="empty-state"><strong>No commits to show</strong><p>Select a repository with commit history or refresh the data.</p></div>}
        </>
      )}

      {toolId === 'prometheus' && (
        <>
          <div className="prometheus-summary"><div><span>Targets</span><strong>{data?.count ?? '—'}</strong></div><div><span>Healthy</span><strong>{data?.targets?.filter((target) => target.health === 'up').length ?? '—'}</strong></div><div><span>Unhealthy</span><strong>{data?.targets?.filter((target) => target.health !== 'up').length ?? '—'}</strong></div></div>
          {data?.targets?.length ? <div className="prometheus-table-wrap"><table className="prometheus-table"><thead><tr><th>Job</th><th>Instance</th><th>Health</th><th>Last scrape</th><th>Last error</th></tr></thead><tbody>{data.targets.map((target, index) => <tr key={`${target.job}-${target.instance}-${index}`}><td>{target.job || '—'}</td><td>{target.instance || '—'}</td><td>{target.health || 'unknown'}</td><td>{target.last_scrape ? formatTimestamp(target.last_scrape) : '—'}</td><td>{target.last_error || '—'}</td></tr>)}</tbody></table></div> : !loading && <div className="empty-state"><strong>No scrape targets returned</strong><p>Prometheus is connected but has no active targets.</p></div>}
          <form className="tool-query-card" onSubmit={runPrometheusQuery}><h2>Metric explorer</h2><p>Run an instant PromQL query against the connected server.</p><label className="form-field"><span>PromQL query</span><input value={metricQuery} onChange={(event) => setMetricQuery(event.target.value)} required /></label><button className="primary-button">Run query</button>{metricResult && <pre className="tool-json-result">{JSON.stringify(metricResult, null, 2)}</pre>}</form>
        </>
      )}

      {toolId === 'loki' && (
        <>
          <section className="tool-management-card"><div><h2>Log streams</h2><p>Available Loki labels for project <strong>{configuration?.project_name}</strong>.</p></div><div className="tool-label-list">{(data?.labels || []).map((label) => <span key={label}>{label}</span>)}</div></section>
          <form className="tool-query-card" onSubmit={runLokiQuery}><h2>Build log search</h2><p>Search the last hour of logs for a specific Jenkins build.</p><label className="form-field"><span>Build number</span><input value={buildNumber} onChange={(event) => setBuildNumber(event.target.value)} required pattern="[A-Za-z0-9_.-]+" /></label><button className="primary-button">Load build logs</button>{logResult && <div className="tool-log-results"><p>{logResult.count} log line(s) · query <code>{logResult.query}</code></p>{logResult.lines?.length ? logResult.lines.map((line, index) => <pre key={`${line.timestamp}-${index}`}>{line.line || JSON.stringify(line)}</pre>) : <p>No matching logs in the last hour.</p>}</div>}</form>
        </>
      )}

      {toolId === 'sonarqube' && (
        <>
          <div className="tool-management-card"><label className="form-field"><span>Project</span><select value={selectedSonarProject} onChange={(event) => onSelectSonarProject(event.target.value)}><option value="">Select a project</option>{sonarProjects.map((project) => <option value={project.key} key={project.key}>{project.name} · {project.key}</option>)}</select></label><span className="tool-data-count">{sonarProjects.length} projects</span></div>
          {data?.measures && <div className="prometheus-summary">{Object.entries(data.measures).map(([key, value]) => <div key={key}><span>{key.replaceAll('_', ' ')}</span><strong>{value}</strong></div>)}</div>}
          {data?.issues?.length ? <div className="jobs-table-wrap"><table className="jobs-table"><thead><tr><th>Severity</th><th>Type</th><th>Rule</th><th>Location</th><th>Issue</th></tr></thead><tbody>{data.issues.map((issue) => <tr key={issue.key || `${issue.rule}-${issue.component}-${issue.line}`}><td>{issue.severity || '—'}</td><td>{issue.type || '—'}</td><td>{issue.rule || '—'}</td><td>{issue.component || '—'}{issue.line ? `:${issue.line}` : ''}</td><td>{issue.message || '—'}</td></tr>)}</tbody></table></div> : selectedSonarProject && !loading && <div className="empty-state"><strong>No open issues returned</strong><p>Quality data is current for {selectedSonarProject}.</p></div>}
          {data?.projects && !selectedSonarProject && <p className="data-caption">Select a project to load its quality gate, measures and issues.</p>}
        </>
      )}

      {toolId === 'kubernetes' && (
        <>
          {data?.counts && <div className="prometheus-summary"><div><span>Namespace</span><strong>{data.namespace}</strong></div><div><span>Deployments ready</span><strong>{data.counts.ready_deployments}/{data.counts.deployments}</strong></div><div><span>Pods ready</span><strong>{data.counts.ready_pods}/{data.counts.pods}</strong></div><div><span>Services</span><strong>{data.counts.services}</strong></div></div>}
          <div className="tool-resource-grid"><ResourceTable title="Deployments" columns={['Name', 'Ready', 'Images']} rows={(data?.deployments || []).map((item) => [item.name, `${item.ready_replicas}/${item.desired_replicas}`, item.images?.join(', ') || '—'])} /><ResourceTable title="Pods" columns={['Name', 'Status', 'Restarts']} rows={(data?.pods || []).map((item) => [item.name, item.ready ? 'Ready' : item.phase, String(item.restarts)])} /></div>
          <section className="tool-query-card"><h2>Recent warning events</h2>{data?.events?.length ? data.events.map((event, index) => <p className="tool-event" key={`${event.reason}-${event.object}-${index}`}><strong>{event.reason} · {event.object}</strong> {event.message}</p>) : <p>No warning events in this namespace.</p>}</section>
        </>
      )}

      {toolId === 'grafana' && (
        <section className="grafana-tool-panel">
          <div className="tool-management-card"><div><h2>Grafana dashboard</h2><p>{data?.version || connectionMessage || 'Grafana health check succeeded.'}</p></div><a className="primary-button" href={configuration?.dashboard_url || configuration?.url} target="_blank" rel="noreferrer">Open Grafana ↗</a></div>
          {isPipeGuardGrafanaDashboardUrl(configuration?.dashboard_url, configuration?.url) ? (
            <iframe title="PipeGuard observability dashboard" src={configuration.dashboard_url} loading="lazy" />
          ) : (
            <div className="empty-state"><strong>PipeGuard dashboard URL is not configured</strong><p>Reconnect Grafana and enter its PipeGuard dashboard URL or a Grafana shared-dashboard URL.</p></div>
          )}
        </section>
      )}
    </section>
  )
}

function ResourceTable({ title, columns, rows }) {
  return <section className="tool-resource-table"><h2>{title}</h2>{rows.length ? <div className="prometheus-table-wrap"><table className="prometheus-table"><thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={`${title}-${index}`}>{row.map((cell, cellIndex) => <td key={`${title}-${index}-${cellIndex}`}>{cell}</td>)}</tr>)}</tbody></table></div> : <p className="data-caption">No {title.toLowerCase()} in the connected namespace.</p>}</section>
}

export default App

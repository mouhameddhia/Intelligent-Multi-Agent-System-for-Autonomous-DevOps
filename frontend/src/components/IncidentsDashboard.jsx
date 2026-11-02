// ============================================================================
// INCIDENTS DASHBOARD — single-file, drop-in component
// ============================================================================
// Self-contained on purpose: everything it needs (state, styles, charts,
// data fetching) lives in this one file, so it can be copied into another
// React project as-is and just dropped into a page.
//
// To integrate elsewhere:
//   1. Copy this file.
//   2. `import IncidentsDashboard from './IncidentsDashboard'`
//   3. Render `<IncidentsDashboard apiUrl="http://127.0.0.1:8000" accessToken={token} />`.
//
// Backend requirement: two read-only GET endpoints, both already part of
// PipeGuard's backend —
//   GET {apiUrl}/api/incidents/stats   (backend/app/api/incident_stats.py)
//   GET {apiUrl}/api/incidents         (backend/app/api/investigation.py)
// Both requests use the authenticated PipeGuard access token.
// If your own backend doesn't have these yet, incident_stats.py is a
// genuinely standalone file (see its own header comment) — copy it over too.
//
// npm dependencies: react, react-markdown, remark-gfm — all already in
// PipeGuard's package.json, used here to render each incident's diagnosis
// report the same way the rest of the app does.
//
// Charts below are hand-drawn inline SVG (no charting library), so this
// file has zero extra npm dependencies beyond the three already listed.
// ============================================================================

import { useEffect, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const STATUS_LABELS = {
  awaiting_approval: 'Awaiting approval',
  approved_fixed: 'Approved & fixed',
  denied: 'Denied',
  resolved: 'Resolved',
  needs_followup: 'Needs follow-up',
  unknown: 'Unknown',
}

// Fixed, semantic status colors (good/warning/serious/critical are the same
// hex in light and dark mode, per the design system this follows — only
// chrome like surfaces/ink/gridlines changes between themes).
const STATUS_COLORS = {
  approved_fixed: '#0ca30c',
  resolved: '#2a78d6',
  awaiting_approval: '#fab219',
  needs_followup: '#ec835a',
  denied: '#d03b3b',
  unknown: '#898781',
}

const AUTO_REFRESH_MS = 30000

function formatDate(value) {
  if (!value) return 'Not available'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Not available'
  return date.toLocaleString()
}

function statusLabel(status) {
  return STATUS_LABELS[status] || status || 'Unknown'
}

function statusColor(status) {
  return STATUS_COLORS[status] || STATUS_COLORS.unknown
}

function downloadCsv(rows) {
  const headers = ['Incident ID', 'Job', 'Build', 'Status', 'Decision', 'Confidence', 'Created at']
  const lines = [headers.join(',')]
  for (const row of rows) {
    const cells = [
      row._id,
      row.job_name,
      row.build_number,
      row.status,
      row.decision?.decision || '',
      row.overall_confidence ?? '',
      row.created_at,
    ].map((value) => `"${String(value ?? '').replaceAll('"', '""')}"`)
    lines.push(cells.join(','))
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `pipeguard-incidents-${new Date().toISOString().slice(0, 10)}.csv`
  link.click()
  URL.revokeObjectURL(url)
}

// Builds a dense day-by-day series for the last `days` days (zero-filled),
// so the trend line never has gaps even on quiet days.
function buildDailyTrend(incidents, days = 14) {
  const buckets = new Map()
  const today = new Date()
  for (let i = days - 1; i >= 0; i -= 1) {
    const day = new Date(today)
    day.setDate(day.getDate() - i)
    buckets.set(day.toISOString().slice(0, 10), 0)
  }
  for (const incident of incidents) {
    if (!incident.created_at) continue
    const date = new Date(incident.created_at)
    if (Number.isNaN(date.getTime())) continue
    const key = date.toISOString().slice(0, 10)
    if (buckets.has(key)) buckets.set(key, buckets.get(key) + 1)
  }
  return Array.from(buckets.entries()).map(([date, count]) => ({ date, count }))
}

// ----------------------------------------------------------------------------
// Small building blocks
// ----------------------------------------------------------------------------

function Sparkline({ values, color, theme }) {
  if (!values.length) return null
  const width = 72
  const height = 22
  const max = Math.max(1, ...values)
  const min = Math.min(...values, 0)
  const range = max - min || 1
  const stepX = values.length > 1 ? width / (values.length - 1) : 0
  const points = values.map((value, index) => ({
    x: index * stepX,
    y: height - ((value - min) / range) * height,
  }))
  const path = points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(' ')
  const last = points[points.length - 1]
  const lineColor = theme === 'dark' ? '#55544f' : '#c3c2b7'

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ display: 'block', overflow: 'visible' }}>
      <path d={path} fill="none" stroke={lineColor} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={last.x} cy={last.y} r={2.5} fill={color} />
    </svg>
  )
}

function KpiCard({ label, value, sub, accent, sparkline, theme }) {
  const t = getStyles(theme)
  return (
    <div style={t.kpiCard}>
      <div style={t.kpiCardTop}>
        <div style={t.kpiLabel}>{label}</div>
        {sparkline && <Sparkline values={sparkline} color={accent || t.inkPrimaryColor} theme={theme} />}
      </div>
      <div style={{ ...t.kpiValue, color: accent || t.inkPrimaryColor }}>{value}</div>
      {sub && <div style={t.kpiSub}>{sub}</div>}
    </div>
  )
}

// Tall segmented bar (one whole split into status parts) + legend, doubling
// as a click-to-filter control for the table below.
function StatusSegmentedBar({ statusCounts, total, activeStatus, onSelect, theme }) {
  const t = getStyles(theme)
  const entries = Object.entries(statusCounts).filter(([, count]) => count > 0)
  if (!entries.length || !total) {
    return <p style={t.muted}>No status data yet.</p>
  }

  return (
    <div>
      <div style={{ display: 'flex', height: 28, borderRadius: 6, overflow: 'hidden', gap: 2 }}>
        {entries.map(([status, count]) => {
          const pct = (count / total) * 100
          const isActive = activeStatus === status
          const isDimmed = activeStatus !== 'all' && !isActive
          return (
            <div
              key={status}
              role="button"
              tabIndex={0}
              onClick={() => onSelect(isActive ? 'all' : status)}
              onKeyDown={(event) => { if (event.key === 'Enter') onSelect(isActive ? 'all' : status) }}
              title={`${statusLabel(status)}: ${count} incident(s), ${pct.toFixed(1)}%`}
              style={{
                width: `${pct}%`,
                background: statusColor(status),
                cursor: 'pointer',
                opacity: isDimmed ? 0.35 : 1,
                outline: isActive ? `2px solid ${t.inkPrimaryColor}` : 'none',
                outlineOffset: 1,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'opacity 0.15s ease',
              }}
            >
              {pct > 12 && (
                <span style={{ fontSize: 11, fontWeight: 700, color: status === 'awaiting_approval' ? '#0b0b0b' : '#ffffff' }}>
                  {Math.round(pct)}%
                </span>
              )}
            </div>
          )
        })}
      </div>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 12 }}>
        {entries.map(([status, count]) => {
          const isActive = activeStatus === status
          return (
            <button
              key={status}
              onClick={() => onSelect(isActive ? 'all' : status)}
              style={{
                display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, border: 'none',
                background: 'none', cursor: 'pointer', padding: 0, fontFamily: 'inherit',
                color: isActive ? t.inkPrimaryColor : t.inkSecondaryColor,
                fontWeight: isActive ? 700 : 400,
              }}
            >
              <span style={{ width: 9, height: 9, borderRadius: 2, background: statusColor(status), display: 'inline-block' }} />
              {statusLabel(status)} ({count})
            </button>
          )
        })}
      </div>
    </div>
  )
}

// Single-series line chart (incidents created per day) with a hover
// crosshair + tooltip. One series, so no legend — the panel title names it.
function TrendChart({ data, color, theme }) {
  const [hoverIndex, setHoverIndex] = useState(null)
  const t = getStyles(theme)
  const width = 600
  const height = 160
  const paddingLeft = 30
  const paddingRight = 8
  const paddingTop = 14
  const paddingBottom = 20
  const innerWidth = width - paddingLeft - paddingRight
  const innerHeight = height - paddingTop - paddingBottom
  const maxCount = Math.max(1, ...data.map((d) => d.count))
  const stepX = data.length > 1 ? innerWidth / (data.length - 1) : 0

  const points = data.map((d, index) => ({
    x: paddingLeft + index * stepX,
    y: paddingTop + innerHeight - (d.count / maxCount) * innerHeight,
    ...d,
  }))

  const linePath = points.map((p, index) => `${index === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ')
  const baselineY = paddingTop + innerHeight
  const areaPath = `${linePath} L ${points[points.length - 1].x.toFixed(1)} ${baselineY} L ${points[0].x.toFixed(1)} ${baselineY} Z`
  const hovered = hoverIndex != null ? points[hoverIndex] : null
  const labelEvery = Math.max(1, Math.ceil(points.length / 7))

  function handleMove(event) {
    const rect = event.currentTarget.getBoundingClientRect()
    if (!rect.width) return
    const ratio = (event.clientX - rect.left) / rect.width
    const svgX = ratio * width
    let nearest = 0
    let nearestDistance = Infinity
    points.forEach((point, index) => {
      const distance = Math.abs(point.x - svgX)
      if (distance < nearestDistance) {
        nearestDistance = distance
        nearest = index
      }
    })
    setHoverIndex(nearest)
  }

  return (
    <div style={{ position: 'relative' }}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: '100%', height: 'auto', display: 'block' }}
        onMouseMove={handleMove}
        onMouseLeave={() => setHoverIndex(null)}
      >
        {[0, 0.5, 1].map((fraction) => {
          const y = paddingTop + innerHeight * (1 - fraction)
          return <line key={fraction} x1={paddingLeft} x2={width - paddingRight} y1={y} y2={y} stroke={t.gridlineColor} strokeWidth={1} />
        })}
        <text x={paddingLeft - 6} y={baselineY + 3} textAnchor="end" fontSize="10" fill={t.mutedColor}>0</text>
        <text x={paddingLeft - 6} y={paddingTop + 4} textAnchor="end" fontSize="10" fill={t.mutedColor}>{maxCount}</text>
        <path d={areaPath} fill={color} opacity={0.1} stroke="none" />
        <path d={linePath} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {points.map((point, index) => (
          (index === points.length - 1 || index === hoverIndex) && (
            <circle key={index} cx={point.x} cy={point.y} r={4} fill={color} stroke={t.surfaceColor} strokeWidth={2} />
          )
        ))}
        {points.map((point, index) => (
          index % labelEvery === 0 && (
            <text key={index} x={point.x} y={height - 4} textAnchor="middle" fontSize="9" fill={t.mutedColor}>
              {point.date.slice(5)}
            </text>
          )
        ))}
        {hovered && (
          <line x1={hovered.x} x2={hovered.x} y1={paddingTop} y2={baselineY} stroke={t.gridlineColor} strokeWidth={1} strokeDasharray="3,3" />
        )}
      </svg>
      {hovered && (
        <div
          style={{
            position: 'absolute',
            left: `${Math.min(92, Math.max(8, (hovered.x / width) * 100))}%`,
            top: 0,
            transform: 'translate(-50%, -100%)',
            background: theme === 'dark' ? '#2c2c2a' : '#0b0b0b',
            color: '#ffffff',
            padding: '4px 9px',
            borderRadius: 6,
            fontSize: 11,
            whiteSpace: 'nowrap',
            pointerEvents: 'none',
          }}
        >
          <strong>{hovered.count}</strong> incident{hovered.count === 1 ? '' : 's'} · {hovered.date}
        </div>
      )}
    </div>
  )
}

// Horizontal bar chart (top jobs by incident count). Single series, direct
// label (count) at the tip of each bar.
function TopJobsChart({ jobs, color, theme }) {
  const [hoverIndex, setHoverIndex] = useState(null)
  const t = getStyles(theme)
  if (!jobs.length) return <p style={t.muted}>No job data yet.</p>
  const maxCount = Math.max(1, ...jobs.map((job) => job.count))

  return (
    <div>
      {jobs.map((job, index) => {
        const widthPct = (job.count / maxCount) * 100
        return (
          <div
            key={job.job_name}
            onMouseEnter={() => setHoverIndex(index)}
            onMouseLeave={() => setHoverIndex(null)}
            style={{ display: 'flex', alignItems: 'center', height: 32, gap: 10 }}
          >
            <div style={t.barRowLabel} title={job.job_name}>{job.job_name}</div>
            <div style={{ flex: 1, background: t.trackColor, height: 20, borderRadius: 4 }}>
              <div
                style={{
                  width: `${widthPct}%`,
                  height: 20,
                  background: color,
                  borderRadius: 4,
                  opacity: hoverIndex === index ? 1 : 0.85,
                  transition: 'opacity 0.15s ease, width 0.3s ease',
                }}
              />
            </div>
            <div style={t.barRowValue}>{job.count}</div>
          </div>
        )
      })}
    </div>
  )
}

function IncidentRow({ incident, expanded, onToggle, theme }) {
  const t = getStyles(theme)
  const confidence = incident.overall_confidence
  const diagnosisExcerpt = (incident.diagnosis_report || '').slice(0, 1200)

  return (
    <>
      <tr style={t.tableRow} onClick={onToggle}>
        <td style={t.td}>
          <strong>{incident.job_name || 'Unknown job'}</strong>
          <div style={t.tdSub}>{incident._id}</div>
        </td>
        <td style={t.td}>#{incident.build_number ?? '—'}</td>
        <td style={t.td}>
          <span style={{ ...t.statusBadge, background: statusColor(incident.status) }}>
            {statusLabel(incident.status)}
          </span>
        </td>
        <td style={t.td}>
          {incident.decision?.decision
            ? incident.decision.decision === 'approve' ? 'Approved' : 'Denied'
            : '—'}
        </td>
        <td style={t.td}>{typeof confidence === 'number' ? `${confidence}%` : '—'}</td>
        <td style={t.td}>{formatDate(incident.created_at)}</td>
        <td style={{ ...t.td, textAlign: 'right' }}>{expanded ? '▲' : '▼'}</td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={7} style={t.expandedCell}>
            <div style={t.expandedGrid}>
              <div>
                <div style={t.expandedLabel}>Diagnosis (excerpt)</div>
                <div style={t.markdownBox}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {diagnosisExcerpt || '_No diagnosis report recorded._'}
                  </ReactMarkdown>
                </div>
              </div>
              <div>
                <div style={t.expandedLabel}>Steps</div>
                {(incident.steps || []).length ? (
                  <ol style={t.stepsList}>
                    {incident.steps.map((step) => (
                      <li key={step.id} style={t.stepsItem}>
                        {step.disruptive && <span title="Disruptive">⚠️ </span>}
                        {step.text}
                        {step.solved && <span style={t.solvedTag}> · solved this</span>}
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p style={t.muted}>No structured steps recorded.</p>
                )}
                {incident.feedback?.comment && (
                  <>
                    <div style={t.expandedLabel}>Feedback</div>
                    <p style={t.feedbackText}>{incident.feedback.comment}</p>
                  </>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

// ----------------------------------------------------------------------------
// Main component
// ----------------------------------------------------------------------------

export default function IncidentsDashboard({
  apiUrl = 'http://127.0.0.1:8000',
  accessToken,
}) {
  const [stats, setStats] = useState(null)
  const [incidents, setIncidents] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [sortBy, setSortBy] = useState('newest')
  const [expandedId, setExpandedId] = useState(null)
  const [theme, setTheme] = useState('light')
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [refreshNonce, setRefreshNonce] = useState(0)

  const t = getStyles(theme)

  useEffect(() => {
    let cancelled = false

    async function load() {
      if (refreshNonce === 0) setIsLoading(true)
      setError('')
      try {
        const response = await fetch(`${apiUrl}/api/incidents/dashboard?limit=200`, {
          headers: { Authorization: `Bearer ${accessToken}` },
        })
        const data = await response.json()
        if (!response.ok) throw new Error(data.detail || 'Could not load the incident dashboard.')
        if (!cancelled) {
          setStats(data.stats || null)
          setIncidents(data.incidents || [])
          setLastUpdated(new Date())
        }
      } catch (err) {
        if (!cancelled) setError(err.message || 'Could not reach the PipeGuard backend.')
      } finally {
        if (!cancelled) setIsLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [apiUrl, accessToken, refreshNonce])

  useEffect(() => {
    if (!autoRefresh) return undefined
    const interval = setInterval(() => setRefreshNonce((n) => n + 1), AUTO_REFRESH_MS)
    return () => clearInterval(interval)
  }, [autoRefresh])

  const dailyTrend = useMemo(() => buildDailyTrend(incidents, 14), [incidents])

  const filteredIncidents = useMemo(() => {
    let rows = incidents

    if (statusFilter !== 'all') {
      rows = rows.filter((incident) => (incident.status || 'unknown') === statusFilter)
    }

    if (search.trim()) {
      const needle = search.trim().toLowerCase()
      rows = rows.filter((incident) =>
        (incident.job_name || '').toLowerCase().includes(needle) ||
        (incident._id || '').toLowerCase().includes(needle) ||
        String(incident.build_number ?? '').includes(needle),
      )
    }

    const sorted = [...rows]
    if (sortBy === 'newest') {
      sorted.sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
    } else if (sortBy === 'oldest') {
      sorted.sort((a, b) => new Date(a.created_at) - new Date(b.created_at))
    } else if (sortBy === 'confidence') {
      sorted.sort((a, b) => (b.overall_confidence ?? -1) - (a.overall_confidence ?? -1))
    }
    return sorted
  }, [incidents, search, statusFilter, sortBy])

  if (isLoading) {
    return <div style={t.page}><p style={t.muted}>Loading incidents…</p></div>
  }

  if (error) {
    return (
      <div style={t.page}>
        <div style={t.errorBanner}>{error}</div>
      </div>
    )
  }

  const trendColor = theme === 'dark' ? '#3987e5' : '#2a78d6'
  const jobsColor = theme === 'dark' ? '#9085e9' : '#6250d6'

  return (
    <div style={t.page}>
      <div style={t.header}>
        <div>
          <div style={t.eyebrow}>INCIDENTS</div>
          <h1 style={t.title}>Incident analytics</h1>
          <p style={t.subtitle}>
            Every investigation PipeGuard has recorded, with outcomes, confidence and feedback.
          </p>
        </div>
        <div style={t.headerActions}>
          <span style={t.lastUpdated}>
            {lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : ''}
          </span>
          <button style={t.secondaryButton} onClick={() => setRefreshNonce((n) => n + 1)}>
            ⟳ Refresh
          </button>
          <button
            style={{ ...t.secondaryButton, ...(autoRefresh ? t.secondaryButtonActive : {}) }}
            onClick={() => setAutoRefresh((v) => !v)}
            title="Auto-refresh every 30s"
          >
            {autoRefresh ? '● Live' : '○ Live off'}
          </button>
          <button
            style={t.secondaryButton}
            onClick={() => setTheme((current) => (current === 'light' ? 'dark' : 'light'))}
            title="Toggle light/dark theme"
          >
            {theme === 'light' ? '🌙 Dark' : '☀️ Light'}
          </button>
          <button style={t.exportButton} onClick={() => downloadCsv(filteredIncidents)}>
            Export CSV ({filteredIncidents.length})
          </button>
        </div>
      </div>

      <div style={t.kpiRow}>
        <KpiCard
          theme={theme}
          label="Total incidents"
          value={stats?.total ?? 0}
          sparkline={dailyTrend.map((d) => d.count)}
        />
        <KpiCard
          theme={theme}
          label="Auto-fix approval rate"
          value={stats?.approval_rate != null ? `${stats.approval_rate}%` : '—'}
          sub={`${stats?.approved_count ?? 0} of ${stats?.decided_count ?? 0} decided`}
          accent="#0ca30c"
        />
        <KpiCard
          theme={theme}
          label="Verified build-passed rate"
          value={stats?.build_passed_rate != null ? `${stats.build_passed_rate}%` : '—'}
          sub={`${stats?.build_passed_count ?? 0} of ${stats?.feedback_count ?? 0} with feedback`}
          accent={trendColor}
        />
        <KpiCard
          theme={theme}
          label="Avg agent confidence"
          value={stats?.avg_confidence != null ? `${stats.avg_confidence}%` : '—'}
          sub="self-reported, not calibrated"
        />
        <KpiCard
          theme={theme}
          label="Most frequent job"
          value={stats?.top_jobs?.[0]?.job_name || '—'}
          sub={stats?.top_jobs?.[0] ? `${stats.top_jobs[0].count} incident(s)` : undefined}
        />
        <KpiCard
          theme={theme}
          label="Awaiting approval"
          value={stats?.status_counts?.awaiting_approval ?? 0}
          accent="#fab219"
        />
      </div>

      <div style={t.chartsGrid}>
        <div style={t.chartCard}>
          <div style={t.chartTitle}>Incidents per day (last 14 days)</div>
          <TrendChart data={dailyTrend} color={trendColor} theme={theme} />
        </div>
        <div style={t.chartCard}>
          <div style={t.chartTitle}>Status breakdown — click to filter the table</div>
          <StatusSegmentedBar
            statusCounts={stats?.status_counts || {}}
            total={stats?.total || 0}
            activeStatus={statusFilter}
            onSelect={setStatusFilter}
            theme={theme}
          />
        </div>
        <div style={t.chartCard}>
          <div style={t.chartTitle}>Top jobs by incident count</div>
          <TopJobsChart jobs={stats?.top_jobs || []} color={jobsColor} theme={theme} />
        </div>
      </div>

      <div style={t.toolbar}>
        <input
          style={t.searchInput}
          placeholder="Search by job, build number, or incident ID…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <select style={t.select} value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
          <option value="all">All statuses</option>
          {Object.keys(STATUS_LABELS).filter((key) => key !== 'unknown').map((key) => (
            <option key={key} value={key}>{STATUS_LABELS[key]}</option>
          ))}
        </select>
        <select style={t.select} value={sortBy} onChange={(event) => setSortBy(event.target.value)}>
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
          <option value="confidence">Highest confidence</option>
        </select>
      </div>

      {filteredIncidents.length === 0 ? (
        <div style={t.emptyState}>
          <strong>No incidents match these filters.</strong>
          <p style={t.muted}>
            {incidents.length === 0
              ? 'No incidents have been recorded yet — run an investigation to see it appear here.'
              : 'Try clearing the search or status filter.'}
          </p>
        </div>
      ) : (
        <div style={t.tableWrap}>
          <table style={t.table}>
            <thead>
              <tr>
                <th style={t.th}>Job</th>
                <th style={t.th}>Build</th>
                <th style={t.th}>Status</th>
                <th style={t.th}>Decision</th>
                <th style={t.th}>Confidence</th>
                <th style={t.th}>Created</th>
                <th style={t.th}></th>
              </tr>
            </thead>
            <tbody>
              {filteredIncidents.map((incident) => (
                <IncidentRow
                  key={incident._id}
                  incident={incident}
                  theme={theme}
                  expanded={expandedId === incident._id}
                  onToggle={() => setExpandedId((current) => (current === incident._id ? null : incident._id))}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

    </div>
  )
}

// ----------------------------------------------------------------------------
// Theme-aware styles. Only chrome (surfaces/ink/borders/gridlines) changes
// between light and dark — categorical/status colors are passed in fixed
// hex values above since they're the same across both modes.
// ----------------------------------------------------------------------------

function getStyles(theme) {
  const isDark = theme === 'dark'
  const surface = isDark ? '#1a1a19' : '#ffffff'
  const page = isDark ? '#0d0d0d' : 'transparent'
  const inkPrimary = isDark ? '#f0efec' : '#1f2130'
  const inkSecondary = isDark ? '#c3c2b7' : '#6b7280'
  const muted = isDark ? '#898781' : '#9a9ba8'
  const border = isDark ? '#2c2c2a' : '#e5e4ef'
  const gridline = isDark ? '#2c2c2a' : '#e1e0d9'
  const subtleBg = isDark ? '#1f1f1e' : '#f8f7fc'
  const track = isDark ? '#2c2c2a' : '#e1e0d9'

  return {
    inkPrimaryColor: inkPrimary,
    inkSecondaryColor: inkSecondary,
    mutedColor: muted,
    gridlineColor: gridline,
    surfaceColor: surface,
    trackColor: track,

    page: { fontFamily: "'Inter', system-ui, sans-serif", color: inkPrimary, background: page, padding: page === 'transparent' ? 0 : 20, borderRadius: 12, transition: 'background 0.2s ease' },
    header: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24, gap: 16, flexWrap: 'wrap' },
    eyebrow: { fontSize: 11, fontWeight: 700, letterSpacing: 1.2, color: '#8c78ed', marginBottom: 4 },
    title: { fontSize: 28, fontWeight: 700, margin: 0 },
    subtitle: { color: inkSecondary, margin: '6px 0 0', fontSize: 14 },
    headerActions: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
    lastUpdated: { fontSize: 11, color: muted, marginRight: 4 },
    exportButton: {
      background: isDark ? '#f0efec' : '#1f2130', color: isDark ? '#0b0b0b' : '#fff', border: 'none', borderRadius: 8,
      padding: '10px 16px', fontSize: 13, fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap',
    },
    secondaryButton: {
      background: 'transparent', color: inkPrimary, border: `1px solid ${border}`, borderRadius: 8,
      padding: '9px 14px', fontSize: 12.5, fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap',
    },
    secondaryButtonActive: { background: isDark ? '#1f3a1f' : '#eafaea', borderColor: '#0ca30c', color: isDark ? '#4ad64a' : '#0ca30c' },
    kpiRow: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 14, marginBottom: 20 },
    kpiCard: { background: surface, border: `1px solid ${border}`, borderRadius: 12, padding: '16px 18px' },
    kpiCardTop: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 },
    kpiLabel: { fontSize: 11, fontWeight: 600, color: muted, textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 8 },
    kpiValue: { fontSize: 26, fontWeight: 700, lineHeight: 1 },
    kpiSub: { fontSize: 11, color: muted, marginTop: 6 },
    chartsGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 14, marginBottom: 20 },
    chartCard: { background: surface, border: `1px solid ${border}`, borderRadius: 12, padding: '16px 18px' },
    chartTitle: { fontSize: 12.5, fontWeight: 600, color: inkSecondary, marginBottom: 12 },
    barRowLabel: { width: 110, fontSize: 12, color: inkSecondary, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
    barRowValue: { width: 28, fontSize: 12, fontWeight: 600, textAlign: 'right', color: inkPrimary },
    toolbar: { display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap' },
    searchInput: { flex: '1 1 260px', padding: '10px 12px', borderRadius: 8, border: `1px solid ${border}`, fontSize: 13, background: surface, color: inkPrimary },
    select: { padding: '10px 12px', borderRadius: 8, border: `1px solid ${border}`, fontSize: 13, background: surface, color: inkPrimary },
    emptyState: { background: subtleBg, borderRadius: 12, padding: 32, textAlign: 'center' },
    muted: { color: muted, fontSize: 13 },
    errorBanner: { background: isDark ? '#3a1f1f' : '#fef2f2', color: isDark ? '#f3a9a9' : '#b91c1c', padding: 14, borderRadius: 8, fontSize: 13 },
    tableWrap: { overflowX: 'auto', border: `1px solid ${border}`, borderRadius: 12 },
    table: { width: '100%', borderCollapse: 'collapse', fontSize: 13, background: surface },
    th: { textAlign: 'left', padding: '12px 14px', background: subtleBg, color: inkSecondary, fontWeight: 600, fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5 },
    tableRow: { borderTop: `1px solid ${border}`, cursor: 'pointer' },
    td: { padding: '12px 14px', verticalAlign: 'top', color: inkPrimary },
    tdSub: { fontSize: 11, color: muted, marginTop: 2 },
    statusBadge: { color: '#fff', fontSize: 11, fontWeight: 600, padding: '3px 9px', borderRadius: 12, whiteSpace: 'nowrap' },
    expandedCell: { background: subtleBg, padding: '18px 24px', borderTop: `1px solid ${border}` },
    expandedGrid: { display: 'grid', gridTemplateColumns: 'minmax(0, 1.4fr) minmax(0, 1fr)', gap: 24 },
    expandedLabel: { fontSize: 11, fontWeight: 700, color: muted, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 },
    markdownBox: { fontSize: 12.5, lineHeight: 1.55, maxHeight: 260, overflowY: 'auto', background: surface, border: `1px solid ${border}`, borderRadius: 8, padding: 12, color: inkPrimary },
    stepsList: { margin: 0, paddingLeft: 18, fontSize: 12.5, lineHeight: 1.6, color: inkPrimary },
    stepsItem: { marginBottom: 4 },
    solvedTag: { color: '#0ca30c', fontWeight: 600 },
    feedbackText: { fontSize: 12.5, fontStyle: 'italic', color: inkSecondary },
  }
}

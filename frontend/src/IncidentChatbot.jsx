import { useEffect, useRef, useState } from 'react'

// PORTABLE COMPONENT — self-contained on purpose, so this one file can be
// copied into another project on its own: inline styles, no CSS file, no
// npm packages beyond React. Its only project dependency is the backend's
// POST /api/chat endpoint (see backend/app/api/chat.py).
//
// Floating widget: a toggle button that opens a chat panel. Ask it about
// past incidents ("why did job X fail?", "how was Y fixed before?") — it
// answers from retrieved incident reports only, it never re-diagnoses.

const ACCENT = '#6c52e8'

const styles = {
  toggleWrap: {
    position: 'fixed',
    bottom: 24,
    right: 24,
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    zIndex: 1000,
  },
  toggleLabel: {
    background: '#fff',
    color: '#24242c',
    fontSize: 12.5,
    fontWeight: 600,
    padding: '8px 12px',
    borderRadius: 10,
    boxShadow: '0 6px 16px rgba(20, 20, 30, 0.15)',
    border: '1px solid #e9eaf0',
    whiteSpace: 'nowrap',
  },
  toggleButton: {
    width: 56,
    height: 56,
    flexShrink: 0,
    borderRadius: '50%',
    border: 'none',
    background: ACCENT,
    color: '#fff',
    fontSize: 24,
    cursor: 'pointer',
    boxShadow: '0 8px 20px rgba(108, 82, 232, 0.35)',
  },
  panel: {
    position: 'fixed',
    bottom: 24,
    right: 24,
    width: 380,
    maxWidth: 'calc(100vw - 32px)',
    height: 540,
    maxHeight: 'calc(100vh - 48px)',
    background: '#fff',
    borderRadius: 16,
    boxShadow: '0 16px 48px rgba(20, 20, 30, 0.22)',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    zIndex: 1000,
    border: '1px solid #e9eaf0',
  },
  header: {
    padding: '14px 16px',
    background: ACCENT,
    color: '#fff',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  headerTitle: {
    fontWeight: 700,
    fontSize: 15,
  },
  headerSubtitle: {
    fontSize: 11,
    opacity: 0.85,
    marginTop: 2,
  },
  closeButton: {
    background: 'transparent',
    border: 'none',
    color: '#fff',
    fontSize: 18,
    cursor: 'pointer',
    lineHeight: 1,
    padding: 4,
  },
  messages: {
    flex: 1,
    overflowY: 'auto',
    padding: 14,
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
    background: '#fafafc',
  },
  emptyState: {
    background: '#fff',
    border: '1px solid #e9eaf0',
    borderRadius: 12,
    padding: 16,
    fontSize: 13,
    color: '#45454f',
    lineHeight: 1.5,
  },
  emptyStateTitle: {
    fontWeight: 700,
    color: '#24242c',
    marginBottom: 6,
  },
  emptyStateList: {
    margin: '8px 0 0',
    paddingLeft: 18,
  },
  emptyStateNote: {
    marginTop: 10,
    fontSize: 11.5,
    color: '#8a8a93',
  },
  bubbleRow: {
    display: 'flex',
  },
  bubbleUser: {
    marginLeft: 'auto',
    background: ACCENT,
    color: '#fff',
    borderRadius: '12px 12px 2px 12px',
    padding: '8px 12px',
    maxWidth: '85%',
    fontSize: 13.5,
    whiteSpace: 'pre-wrap',
  },
  bubbleAssistant: {
    marginRight: 'auto',
    background: '#fff',
    color: '#24242c',
    border: '1px solid #e9eaf0',
    borderRadius: '12px 12px 12px 2px',
    padding: '8px 12px',
    maxWidth: '90%',
    fontSize: 13.5,
    whiteSpace: 'pre-wrap',
  },
  bubbleError: {
    marginRight: 'auto',
    background: '#fdecec',
    color: '#9c2b2b',
    border: '1px solid #f3c6c6',
    borderRadius: '12px 12px 12px 2px',
    padding: '8px 12px',
    maxWidth: '90%',
    fontSize: 13.5,
  },
  sources: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 6,
    marginTop: 6,
  },
  sourceChip: {
    fontSize: 10.5,
    padding: '3px 8px',
    borderRadius: 999,
    background: '#f1edfc',
    color: '#6c52e8',
    fontWeight: 600,
    whiteSpace: 'nowrap',
  },
  typing: {
    marginRight: 'auto',
    color: '#8a8a93',
    fontSize: 12.5,
    fontStyle: 'italic',
  },
  inputRow: {
    display: 'flex',
    gap: 8,
    padding: 12,
    borderTop: '1px solid #e9eaf0',
    background: '#fff',
  },
  input: {
    flex: 1,
    border: '1px solid #dcdce4',
    borderRadius: 10,
    padding: '9px 12px',
    fontSize: 13.5,
    outline: 'none',
  },
  sendButton: {
    border: 'none',
    borderRadius: 10,
    background: ACCENT,
    color: '#fff',
    fontWeight: 600,
    fontSize: 13,
    padding: '0 16px',
    cursor: 'pointer',
  },
}

function statusLabel(status) {
  if (!status) return null
  return status.replace(/_/g, ' ')
}

function MessageBubble({ message }) {
  if (message.role === 'user') {
    return (
      <div style={styles.bubbleRow}>
        <div style={styles.bubbleUser}>{message.content}</div>
      </div>
    )
  }

  if (message.role === 'error') {
    return (
      <div style={styles.bubbleRow}>
        <div style={styles.bubbleError}>{message.content}</div>
      </div>
    )
  }

  return (
    <div style={styles.bubbleRow}>
      <div style={styles.bubbleAssistant}>
        <div>{message.content}</div>
        {message.sources && message.sources.length > 0 && (
          <div style={styles.sources}>
            {message.sources.map((source) => (
              <span key={source.incident_id} style={styles.sourceChip} title={source.incident_id}>
                {source.job_name || 'unknown job'} #{source.build_number ?? '?'}
                {statusLabel(source.status) ? ` · ${statusLabel(source.status)}` : ''}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function IncidentChatbot({ apiUrl }) {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const scrollRef = useRef(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages, loading])

  async function sendMessage() {
    const text = input.trim()
    if (!text || loading) return

    setMessages((current) => [...current, { role: 'user', content: text }])
    setInput('')
    setLoading(true)

    try {
      const response = await fetch(`${apiUrl}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      })

      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(body?.detail || `Request failed (${response.status})`)
      }

      const data = await response.json()
      setMessages((current) => [
        ...current,
        { role: 'assistant', content: data.answer, sources: data.sources || [] },
      ])
    } catch (err) {
      setMessages((current) => [
        ...current,
        { role: 'error', content: err.message || 'Something went wrong reaching the Incident Advisor.' },
      ])
    } finally {
      setLoading(false)
    }
  }

  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      sendMessage()
    }
  }

  if (!open) {
    return (
      <div style={styles.toggleWrap}>
        <span style={styles.toggleLabel}>Ask about past incidents</span>
        <button
          style={styles.toggleButton}
          onClick={() => setOpen(true)}
          aria-label="Open Incident Advisor chat"
          title="Ask the Incident Advisor"
        >
          💬
        </button>
      </div>
    )
  }

  return (
    <div style={styles.panel}>
      <div style={styles.header}>
        <div>
          <div style={styles.headerTitle}>Incident Advisor</div>
          <div style={styles.headerSubtitle}>Ask about past incidents</div>
        </div>
        <button style={styles.closeButton} onClick={() => setOpen(false)} aria-label="Close chat">
          ✕
        </button>
      </div>

      <div style={styles.messages} ref={scrollRef}>
        {messages.length === 0 && (
          <div style={styles.emptyState}>
            <div style={styles.emptyStateTitle}>What is this?</div>
            <div>
              I'm the <strong>Incident Advisor</strong>. I answer questions about
              past CI/CD incidents using what this system already diagnosed — I
              don't re-diagnose anything myself, I search and summarize what's
              already recorded.
            </div>
            <ul style={styles.emptyStateList}>
              <li>"Why did [job] build #[N] fail?"</li>
              <li>"Have we seen this error before, and how was it fixed?"</li>
              <li>"What was the root cause of the last incident?"</li>
            </ul>
            <div style={styles.emptyStateNote}>
              I can't answer aggregate questions like "which job fails most" —
              that's on the Incidents Dashboard instead.
            </div>
          </div>
        )}
        {messages.map((message, index) => (
          <MessageBubble key={index} message={message} />
        ))}
        {loading && <div style={styles.typing}>Incident Advisor is thinking…</div>}
      </div>

      <div style={styles.inputRow}>
        <input
          style={styles.input}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about an incident…"
          disabled={loading}
        />
        <button style={styles.sendButton} onClick={sendMessage} disabled={loading || !input.trim()}>
          Send
        </button>
      </div>
    </div>
  )
}

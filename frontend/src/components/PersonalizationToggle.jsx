import { useCallback, useEffect, useState } from 'react'

export default function PersonalizationToggle({ enabled, onChange, apiUrl, accessToken }) {
  const [showConsent, setShowConsent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [learningStatus, setLearningStatus] = useState(null)

  const refreshLearningStatus = useCallback(async () => {
    const response = await fetch(`${apiUrl}/api/learning/status`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || `Could not load learning status (HTTP ${response.status}).`)
    setLearningStatus(data)
    return data
  }, [apiUrl, accessToken])

  useEffect(() => {
    let cancelled = false
    fetch(`${apiUrl}/api/learning/status`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
      .then(async (response) => {
        const data = await response.json()
        if (!response.ok) throw new Error(data.detail || `Could not load learning status (HTTP ${response.status}).`)
        if (!cancelled) setLearningStatus(data)
      })
      .catch((statusError) => {
        if (!cancelled) setError(statusError.message)
      })
    return () => { cancelled = true }
  }, [apiUrl, accessToken])

  async function enable() {
    setBusy(true)
    setError('')
    try {
      const result = await onChange(true)
      const enrolled = result?.initial_records_enrolled ?? 0
      setNotice(enrolled
        ? `Personalization enabled. ${enrolled} recent closed incident${enrolled === 1 ? '' : 's'} added to this account’s learning batch.`
        : 'Personalization enabled. New opted-in incidents will build this account’s learning history.')
      setShowConsent(false)
      refreshLearningStatus().catch((statusError) => setError(statusError.message))
    } catch (err) {
      setError(err.message || 'Could not enable personalized remediation.')
    } finally {
      setBusy(false)
    }
  }

  async function buildPolicyNow() {
    setBusy(true)
    setError('')
    setNotice('Evaluating opted-in outcomes and updating the account policy…')
    try {
      const response = await fetch(`${apiUrl}/api/learning/run`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({ force: true, wait: true }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || `Learning run failed (HTTP ${response.status}).`)
      setNotice(`Learning run: ${result.status}. ${result.records_evaluated ?? 0} evaluated, ${result.records_used ?? 0} used${result.version != null ? `; active policy v${result.version}` : ''}.`)
      await refreshLearningStatus()
    } catch (runError) {
      setError(runError.message || 'Could not run the learning pipeline.')
      setNotice('')
    } finally {
      setBusy(false)
    }
  }

  async function toggle() {
    if (enabled) {
      await onChange(false)
      setNotice('Personalization is off for new investigations.')
    } else {
      setError('')
      setShowConsent(true)
    }
  }

  return (
    <div className="personalization-control">
      <div className="personalization-control-copy">
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          className={`secondary-button ${enabled ? 'personalization-switch-on' : ''}`}
          onClick={toggle}
        >
          {enabled ? 'Personalized answers on' : 'Personalized answers off'}
        </button>
        <span className="personalization-caption">Optional account learning for remediation</span>
        {notice && <span className="personalization-notice" role="status">{notice}</span>}
        {enabled && learningStatus && (
          <div className="personalization-learning-status" aria-live="polite">
            {!learningStatus.learning_enabled
              ? 'Learning is disabled by backend configuration.'
              : learningStatus.active_policy_version != null
                ? `Active policy v${learningStatus.active_policy_version} · ${learningStatus.active_rule_count} active rules`
                : `No active policy · ${learningStatus.evaluated_with_outcome} evaluated outcomes · ${learningStatus.awaiting_learning} waiting · batch size ${learningStatus.batch_size}`}
            <button type="button" className="text-button" onClick={() => refreshLearningStatus().catch((statusError) => setError(statusError.message))} disabled={busy}>
              Refresh status
            </button>
            {learningStatus.learning_enabled && learningStatus.active_policy_version == null && (
              <button type="button" className="text-button" onClick={buildPolicyNow} disabled={busy}>
                {busy ? 'Building policy…' : 'Build policy now'}
              </button>
            )}
          </div>
        )}
        {error && !showConsent && <span className="error-banner" role="alert">{error}</span>}
      </div>

      {showConsent && (
        <div className="personalization-consent-backdrop" onClick={() => !busy && setShowConsent(false)}>
          <section
            className="personalization-consent-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="personalization-consent-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="eyebrow">OPTIONAL · ACCOUNT-SCOPED</div>
            <h2 id="personalization-consent-title">Turn on personalized remediation?</h2>
            <p>
              Personalized remediation uses this account’s incident summaries, proposed steps, decisions, and feedback. When enabled, up to five recent closed investigations are included as an initial learning batch. Selected structured details may be sent to your configured AI providers for scoring and learning. Raw Jenkins console logs are excluded, and other accounts’ incidents are not used.
            </p>
            <p className="personalization-consent-secondary">
              Future investigations run with this switch on also contribute outcomes. You can turn it off at any time. The standard pipeline and human approval remain in place.
            </p>
            {error && <div className="error-banner" role="alert">{error}</div>}
            <div className="personalization-consent-actions">
              <button className="secondary-button" disabled={busy} onClick={() => setShowConsent(false)}>Cancel</button>
              <button className="primary-button" disabled={busy} onClick={enable}>
                {busy ? 'Enabling…' : 'Enable for my account'}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  )
}

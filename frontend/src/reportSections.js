// Pull one named section out of an agent's markdown report, keeping its
// markdown intact (tables, code fences, underscores, backticks).
//
// A section starts at a line whose label matches one of `aliases`, e.g.
//   **Failed stage**           ## Failed stage           **Failed stage:** text
// and ends at the next heading-like line. Lines inside a ``` fence are never
// treated as headings, so a code block containing blank lines stays whole.

// Heading-like lines that end a section: "## Title", "**Title**", "**Title:**",
// and numbered labels such as "2. **Conclusion**:" (a numbered bold phrase only
// counts as a heading when followed by a colon or end of line, so list items
// like "1. **Clone the repo** then ..." stay inside their section).
const SECTION_BREAK = new RegExp(
    '^\\s{0,3}(?:' +
      '#{1,6}\\s+\\S' +
      '|(?:\\*\\*|__)[^*_\\n]{2,60}?(?:\\*\\*|__)\\s*:?(?:\\s|$)' +
      '|\\d+[.)]\\s*(?:\\*\\*|__)[^*_\\n]{2,60}?(?::(?:\\*\\*|__)|(?:\\*\\*|__)\\s*:|(?:\\*\\*|__)\\s*$)' +
      ')',
  )
  
  // Strips the markdown that can precede a label: "## ", "1. ", "**".
  const LABEL_PREFIX = /^\s{0,3}(?:#{1,6}\s*)?(?:\d+[.)]\s*)?(?:\*\*|__)?/
  
  const isFence = (line) => /^\s{0,3}(```|~~~)/.test(line)
  
  export function extractSection(report, aliases) {
    const lines = (report || '').split(/\r?\n/)
    const wanted = aliases.map((alias) => alias.toLowerCase())
  
    // Find the start line (outside code fences).
    let start = -1
    let tail = ''
    let inFence = false
    for (let i = 0; i < lines.length; i++) {
      if (isFence(lines[i])) {
        inFence = !inFence
        continue
      }
      if (inFence) continue
  
      const label = lines[i].replace(LABEL_PREFIX, '')
      const lower = label.toLowerCase()
      const alias = wanted.find(
        (item) => lower.startsWith(item) && !/[a-z0-9]/.test(lower.charAt(item.length)),
      )
      if (alias) {
        start = i
        // "**Failed stage:** Automated Tests" -> keep "Automated Tests".
        tail = label.slice(alias.length).replace(/^[\s:*_]+/, '').trim()
        break
      }
    }
    if (start < 0) return ''
  
    const body = tail ? [tail] : []
    inFence = false
    for (let i = start + 1; i < lines.length; i++) {
      const line = lines[i]
      if (isFence(line)) {
        inFence = !inFence
        body.push(line)
        continue
      }
      if (!inFence && SECTION_BREAK.test(line)) break
      if (!inFence && /^\s*(-{3,}|\*{3,})\s*$/.test(line)) break
      body.push(line)
    }
  
    return body.join('\n').trim()
  }
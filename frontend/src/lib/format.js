export function fmtPrice(v) {
  if (v == null || Number.isNaN(v)) return '—'
  const digits = Math.abs(v) >= 1000 ? 2 : Math.abs(v) >= 1 ? 2 : 4
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function fmtVolume(v) {
  if (v == null) return '—'
  if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B'
  if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K'
  return String(v)
}

export function fmtAge(ms) {
  if (ms == null) return 'no data'
  if (ms < 0) ms = 0
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`
  const m = s / 60
  if (m < 60) return `${Math.floor(m)} min ${Math.floor(s % 60)} s`
  const h = m / 60
  if (h < 48) return `${Math.floor(h)} h ${Math.floor(m % 60)} min`
  return `${Math.floor(h / 24)} days`
}

export function fmtClock(ms, withDate = false) {
  if (ms == null) return '—'
  const d = new Date(ms)
  return withDate
    ? d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function ageLevel(ms) {
  if (ms == null) return 'bad'
  if (ms < 15_000) return 'good'
  if (ms < 120_000) return 'warn'
  return 'bad'
}

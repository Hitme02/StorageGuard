import { createContext, useContext, useState, type ReactNode } from 'react'
import { TERM } from '../content/glossary'
import { DECISION, STATUS } from '../lib/plain'
import type { Decision } from '../lib/types'

// ---- presentation context: which screen we're on, engineer view ----
export interface PresCtx { slide: string; engineer: boolean; build: number }
export const Pres = createContext<PresCtx>({ slide: '1.1', engineer: false, build: 0 })
export const usePres = () => useContext(Pres)

/** A technical term. Highlighted on the screen that introduces it; hover shows the plain definition. */
export function Term({ id, children, quiet }: { id: string; children?: ReactNode; quiet?: boolean }) {
  const { slide } = usePres()
  const t = TERM[id]
  const [open, setOpen] = useState(false)
  if (!t) return <>{children}</>
  const isNew = t.slide === slide
  return (
    <span className={`term${isNew ? ' new' : ''}${quiet ? ' quiet' : ''}`} onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)} onBlur={() => setOpen(false)} tabIndex={0}>
      {children ?? t.term}
      {open && (
        <span className="term-pop" role="tooltip">
          <b>{t.term}</b>
          {t.plain}
        </span>
      )}
    </span>
  )
}

export function SlideHead({ title, lede }: { title: ReactNode; lede?: ReactNode }) {
  return (
    <header className="slide-head">
      <h1>{title}</h1>
      {lede && <p className="lede">{lede}</p>}
    </header>
  )
}

export function Stamp({ d, big, none, label }: { d?: Decision | null; big?: boolean; none?: boolean; label?: string }) {
  if (none || !d) {
    return (
      <span className={`stamp none${big ? ' big' : ''}`}>
        <span className="ic">→</span>
        {label || 'No check'}
      </span>
    )
  }
  const info = DECISION[d]
  return (
    <span className={`stamp ${info.cls}${big ? ' big' : ''}`}>
      <span className="ic" aria-hidden>{info.icon}</span>
      {label || info.label}
    </span>
  )
}

export function statusText(s: string | undefined | null) {
  return (s && STATUS[s]) || s || ''
}

export function Switch({ on, onChange, children, disabled }: { on: boolean; onChange: (v: boolean) => void; children: ReactNode; disabled?: boolean }) {
  return (
    <label className={`switch${on ? ' on' : ''}`} style={disabled ? { opacity: 0.4, pointerEvents: 'none' } : undefined}>
      <input type="checkbox" checked={on} onChange={(e) => onChange(e.target.checked)} style={{ position: 'absolute', opacity: 0, width: 0 }} disabled={disabled} />
      <span className="track" />
      <span>{children}</span>
    </label>
  )
}

export function Seg<T extends string>({ value, options, onChange }: { value: T; options: { v: T; label: ReactNode; disabled?: boolean; title?: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="seg" role="radiogroup">
      {options.map((o) => (
        <button key={o.v} role="radio" aria-checked={o.v === value} className={o.v === value ? 'on' : ''} disabled={o.disabled} title={o.title}
          onClick={() => onChange(o.v)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** Risk 0..1 with the two thresholds marked. */
export function RiskMeter({ risk, tauAllow = 0.15, tauBlock = 0.55, width = '100%', short = false }: { risk: number; tauAllow?: number; tauBlock?: number; width?: number | string; short?: boolean }) {
  const color = risk >= tauBlock ? 'var(--block)' : risk >= tauAllow ? 'var(--ask)' : 'var(--allow)'
  return (
    <div style={{ width, paddingBottom: 18 }}>
      <div className="meter" role="meter" aria-valuenow={risk} aria-valuemin={0} aria-valuemax={1} aria-label="risk score">
        <div className="fill" style={{ width: `${Math.max(1.5, risk * 100)}%`, background: color, transition: 'width 0.6s ease' }} />
        <div className="tick" style={{ left: `${tauAllow * 100}%` }}><span>{short ? 'ask' : `ask a human ${tauAllow}`}</span></div>
        <div className="tick" style={{ left: `${tauBlock * 100}%` }}><span>{short ? 'refuse' : `refuse ${tauBlock}`}</span></div>
      </div>
    </div>
  )
}

export function Eng({ title, children }: { title?: string; children: ReactNode }) {
  const { engineer } = usePres()
  if (!engineer) return null
  return (
    <div className="eng">
      <div className="eng-title">{title || 'Engineer view'}</div>
      {children}
    </div>
  )
}

export function ErrorNote({ msg }: { msg: string }) {
  return <div className="err">{msg}</div>
}

export function Loading({ text = 'Loading' }: { text?: string }) {
  return (
    <div className="muted small" style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
      <span className="spin" /> {text}
    </div>
  )
}

export function OriginTag({ origin }: { origin?: 'live' | 'recorded' }) {
  if (!origin) return null
  return (
    <span className="chip" title={origin === 'live' ? 'Computed just now by the backend' : 'Loaded from data recorded earlier'}>
      <span className={`led ${origin === 'live' ? 'on-green' : 'on-amber'}`} />
      {origin === 'live' ? 'Live' : 'Recorded'}
    </span>
  )
}

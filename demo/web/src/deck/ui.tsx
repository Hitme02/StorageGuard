// Shared pieces: the screen frame (text left, one picture right), swapping headlines, counters.
import { AnimatePresence, animate, motion, type MotionStyle } from 'framer-motion'
import { useEffect, useRef, useState, type ReactNode } from 'react'

export interface ScreenProps {
  build: number
}

function Swap({ k, children }: { k: string; children: ReactNode }) {
  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div key={k} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.32, ease: 'easeOut' }}>
        {children}
      </motion.div>
    </AnimatePresence>
  )
}

export function Frame({ head, support, children, visual, short }: { head: string; support?: string; children?: ReactNode; visual?: ReactNode; short?: boolean }) {
  return (
    <div className="d-screen">
      <div className="d-text">
        <Swap k={head}><h1 className={`d-head${short ? ' short' : ''}`}>{head}</h1></Swap>
        {support !== undefined && <Swap k={support}><p className="d-support">{support}</p></Swap>}
        {children}
      </div>
      {visual && <div className="d-visual">{visual}</div>}
    </div>
  )
}

export function Reveal({ on, children, delay = 0, style, className }: { on: boolean; children: ReactNode; delay?: number; style?: MotionStyle; className?: string }) {
  return (
    <motion.div className={className} style={style} initial={false} animate={{ opacity: on ? 1 : 0, y: on ? 0 : 14 }} transition={{ duration: 0.5, delay: on ? delay : 0, ease: 'easeOut' }}>
      {children}
    </motion.div>
  )
}

/** A number that counts to its value. */
export function Count({ to, digits = 0, duration = 1 }: { to: number; digits?: number; duration?: number }) {
  const [v, setV] = useState(0)
  const prev = useRef(0)
  useEffect(() => {
    const c = animate(prev.current, to, { duration, ease: [0.2, 0.7, 0.2, 1], onUpdate: setV })
    prev.current = to
    return () => c.stop()
  }, [to, duration])
  return <>{v.toFixed(digits)}</>
}

export function Chips<T extends string>({ value, options, onChange, guard }: { value: T; options: { v: T; label: string; disabled?: boolean; title?: string }[]; onChange: (v: T) => void; guard?: (v: T) => boolean }) {
  return (
    <div className="d-chips" role="radiogroup">
      {options.map((o) => (
        <button key={o.v} role="radio" aria-checked={value === o.v} title={o.title} disabled={o.disabled}
          className={`d-chip${value === o.v ? ' on' : ''}${guard?.(o.v) ? ' g' : ''}`} onClick={() => onChange(o.v)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Switch({ on, onChange, children }: { on: boolean; onChange: (v: boolean) => void; children: ReactNode }) {
  return (
    <button className={`d-switch${on ? ' on' : ''}`} role="switch" aria-checked={on} onClick={() => onChange(!on)}>
      <span className="t" />
      {children}
    </button>
  )
}

export function Waiting({ text = 'Loading' }: { text?: string }) {
  return <div className="d-note" style={{ padding: 20 }}>{text}…</div>
}

export function Problem({ msg }: { msg: string }) {
  return <div className="d-note" style={{ padding: 20, color: 'var(--harm)', maxWidth: 520 }}>{msg}</div>
}

export const range = (xs: number[], digits = 0) => {
  const lo = Math.min(...xs)
  const hi = Math.max(...xs)
  const f = (v: number) => v.toFixed(digits)
  return f(lo) === f(hi) ? f(lo) : `${f(lo)}–${f(hi)}`
}

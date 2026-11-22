// The deck: a progress strip on top, one screen, and a button that says what happens next.
import { AnimatePresence, MotionConfig, motion } from 'framer-motion'
import { useCallback, useEffect, useLayoutEffect, useState, type ComponentType } from 'react'
import { useBackend } from '../lib/api'
import { Bench } from './screens/bench'
import { Copy, Decide } from './screens/idea'
import { Play } from './screens/play'
import { Lens, Links, Ticket } from './screens/problem'
import { End, Real } from './screens/reality'
import { Hundred, Scale, Versus } from './screens/results'
import type { ScreenProps } from './ui'

const CHAPTERS = ['Problem', 'Idea', 'Test bed', 'Try it', 'Results', 'Reality check']

interface Def {
  id: string
  chapter: number
  /** what pressing "next" does inside the screen, one label per reveal */
  steps: string[]
  /** what pressing "next" does once the screen is complete */
  cta: string
  C: ComponentType<ScreenProps>
}

const SCREENS: Def[] = [
  { id: 'ticket', chapter: 0, steps: ['Let the agent work', 'Show what arch-fad9 was', 'How often does this happen?'], cta: 'See how storage is linked', C: Ticket },
  { id: 'linked', chapter: 0, steps: ['Add snapshots', 'Add a clone', 'Add a replica and a backup', 'Delete the volume'], cta: 'Why today’s checks miss it', C: Links },
  { id: 'checks', chapter: 0, steps: ['Prompt rules', 'An AI checker', 'What we need'], cta: 'Our idea', C: Lens },
  { id: 'copy', chapter: 1, steps: ['Run it on a copy', 'Answer the agent'], cta: 'Inside the guard', C: Copy },
  { id: 'decide', chapter: 1, steps: ['Score the risk', 'Show all four answers'], cta: 'How we tested it', C: Decide },
  { id: 'tickets', chapter: 2, steps: ['Sort them'], cta: 'Try it yourself', C: Bench },
  { id: 'play', chapter: 3, steps: [], cta: 'See the results', C: Play },
  { id: 'hundred', chapter: 4, steps: ['Turn StorageGuard on', 'All three models'], cta: 'Compare with other checks', C: Hundred },
  { id: 'versus', chapter: 4, steps: ['Add the other checks', 'Add StorageGuard'], cta: 'Do bigger models fix it?', C: Versus },
  { id: 'scale', chapter: 4, steps: ['Add StorageGuard'], cta: 'Did it really happen?', C: Scale },
  { id: 'real', chapter: 5, steps: ['Real storage software', 'Data destroyed'], cta: 'Wrap up', C: Real },
  { id: 'end', chapter: 6, steps: ['What StorageGuard changed', 'Was it real?', 'Questions'], cta: '', C: End },
]

function parseHash() {
  const m = window.location.hash.match(/^#\/([\w-]+)(?:\/(\d+))?/)
  const i = m ? SCREENS.findIndex((s) => s.id === m[1]) : 0
  const idx = Math.max(0, i)
  const b = m?.[2] ? Math.min(Number(m[2]), SCREENS[idx].steps.length) : 0
  return { i: idx, b }
}

function useScale() {
  const [s, set] = useState(1)
  useLayoutEffect(() => {
    const f = () => set(Math.min(window.innerWidth / 1600, window.innerHeight / 900))
    f()
    window.addEventListener('resize', f)
    return () => window.removeEventListener('resize', f)
  }, [])
  return s
}

function Mark() {
  return (
    <svg width="30" height="22" viewBox="0 0 30 22" aria-hidden>
      <circle cx="10" cy="11" r="8" fill="#eeeaf4" />
      <circle cx="20" cy="11" r="8" fill="none" stroke="#e052c8" strokeWidth="1.6" strokeDasharray="2.5 2.5" />
    </svg>
  )
}

export default function Deck() {
  const init = parseHash()
  const [idx, setIdx] = useState(init.i)
  const [build, setBuild] = useState(init.b)
  const scale = useScale()
  const backend = useBackend()
  const s = SCREENS[idx]

  useEffect(() => {
    const h = `#/${s.id}${build ? `/${build}` : ''}`
    if (window.location.hash !== h) window.history.replaceState(null, '', h)
  }, [s.id, build])
  useEffect(() => {
    const on = () => { const p = parseHash(); setIdx(p.i); setBuild(p.b) }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  const go = useCallback((i: number, b = 0) => { setIdx(Math.max(0, Math.min(SCREENS.length - 1, i))); setBuild(b) }, [])
  const next = useCallback(() => {
    if (build < s.steps.length) return setBuild(build + 1)
    if (idx < SCREENS.length - 1) go(idx + 1, 0)
  }, [build, s.steps.length, idx, go])
  const prev = useCallback(() => {
    if (build > 0) return setBuild(build - 1)
    if (idx > 0) go(idx - 1, SCREENS[idx - 1].steps.length)
  }, [build, idx, go])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement
      if (el && (el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.tagName === 'TEXTAREA')) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const k = e.key
      if (k === 'ArrowRight' || k === 'PageDown' || (k === ' ' && el?.tagName !== 'BUTTON')) { e.preventDefault(); next() }
      else if (k === 'ArrowLeft' || k === 'PageUp') { e.preventDefault(); prev() }
      else if (k === 'f' || k === 'F') { if (!document.fullscreenElement) document.documentElement.requestFullscreen?.(); else document.exitFullscreen?.() }
      else if (/^[1-6]$/.test(k)) { const i = SCREENS.findIndex((x) => x.chapter === Number(k) - 1); if (i >= 0) go(i, 0) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [next, prev, go])

  const isLast = idx === SCREENS.length - 1 && build >= s.steps.length
  const label = build < s.steps.length ? s.steps[build] : s.cta
  const C = s.C

  return (
    <MotionConfig reducedMotion="user">
      <div className="d-viewport">
        <div className="d-stage" style={{ transform: `translate(-50%, -50%) scale(${scale})` }}>
          <header className="d-top">
            <div className="d-brand"><Mark />StorageGuard</div>
            <nav className="d-steps" aria-label="Chapters">
              {CHAPTERS.map((c, i) => (
                <div key={c} style={{ display: 'flex', alignItems: 'center' }}>
                  {i > 0 && <span className="d-step-gap" />}
                  <button className={`d-step${s.chapter === i ? ' on' : s.chapter > i ? ' done' : ''}`} aria-current={s.chapter === i ? 'step' : undefined}
                    onClick={() => go(SCREENS.findIndex((x) => x.chapter === i), 0)}>
                    <span className="dot" />{c}
                  </button>
                </div>
              ))}
            </nav>
            <div className={`d-live${backend === 'live' ? ' on' : backend === 'offline' ? ' rec' : ''}`}
              title={backend === 'live' ? 'The Python backend is running: the guard decides live' : 'Backend not reachable: showing runs recorded earlier'}>
              <i />{backend === 'live' ? 'Live' : backend === 'offline' ? 'Recorded' : 'Connecting'}
            </div>
          </header>

          <AnimatePresence mode="wait">
            <motion.div key={s.id} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.25 }}>
              <C build={build} />
            </motion.div>
          </AnimatePresence>

          <div className="d-nav">
            {s.steps.length > 0 && (
              <div className="d-builds" aria-hidden>
                {Array.from({ length: s.steps.length + 1 }, (_, i) => <i key={i} className={i <= build ? 'on' : ''} />)}
              </div>
            )}
            <button className="d-back" onClick={prev} disabled={idx === 0 && build === 0}>Back</button>
            <button className="d-next" onClick={next} disabled={isLast}>{label}</button>
          </div>
        </div>
      </div>
    </MotionConfig>
  )
}

import { AnimatePresence, motion } from 'framer-motion'
import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from 'react'
import { GLOSSARY } from '../content/glossary'
import { useBackend } from '../lib/api'
import { Pres } from '../components/ui'
import { CHAPTERS, SLIDES } from '../slides'

function parseHash(): { i: number; b: number } {
  const m = window.location.hash.match(/^#\/([\w.]+)(?:\/(\d+))?/)
  if (!m) return { i: 0, b: 0 }
  const i = SLIDES.findIndex((s) => s.id === m[1])
  return { i: Math.max(0, i), b: m[2] ? Number(m[2]) : 0 }
}

function useStageScale() {
  const [s, set] = useState(1)
  useLayoutEffect(() => {
    const f = () => set(Math.min(window.innerWidth / 1600, window.innerHeight / 900))
    f()
    window.addEventListener('resize', f)
    return () => window.removeEventListener('resize', f)
  }, [])
  return s
}

function fmt(sec: number) {
  const m = Math.floor(sec / 60)
  return `${String(m).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`
}

export default function App() {
  const init = parseHash()
  const [idx, setIdx] = useState(init.i)
  const [build, setBuild] = useState(init.b)
  const [engineer, setEngineer] = useState(false)
  const [contrast, setContrast] = useState(false)
  const [overview, setOverview] = useState(false)
  const [glossary, setGlossary] = useState(false)
  const [corePath, setCorePath] = useState(true)
  const [t0, setT0] = useState(() => Date.now())
  const [now, setNow] = useState(Date.now())
  const [toast, setToast] = useState<string[] | null>(null)
  const scale = useStageScale()
  const backend = useBackend()
  const slide = SLIDES[idx]

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  useEffect(() => {
    document.documentElement.dataset.contrast = contrast ? 'high' : ''
  }, [contrast])
  useEffect(() => {
    const h = `#/${slide.id}${build ? `/${build}` : ''}`
    if (window.location.hash !== h) window.history.replaceState(null, '', h)
  }, [slide.id, build])
  useEffect(() => {
    const onHash = () => {
      const p = parseHash()
      setIdx(p.i)
      setBuild(p.b)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  // new terms on this screen
  useEffect(() => {
    const fresh = GLOSSARY.filter((t) => t.slide === slide.id).map((t) => t.term)
    if (!fresh.length) return setToast(null)
    setToast(fresh)
  }, [slide.id])

  const eligible = useCallback((i: number) => !corePath || SLIDES[i].core, [corePath])
  const go = useCallback((i: number, b = 0) => {
    setIdx(Math.max(0, Math.min(SLIDES.length - 1, i)))
    setBuild(b)
  }, [])
  const next = useCallback(() => {
    if (build < slide.builds - 1) return setBuild(build + 1)
    for (let i = idx + 1; i < SLIDES.length; i++) if (eligible(i)) return go(i, 0)
  }, [build, slide.builds, idx, eligible, go])
  const prev = useCallback(() => {
    if (build > 0) return setBuild(build - 1)
    for (let i = idx - 1; i >= 0; i--) if (eligible(i)) return go(i, SLIDES[i].builds - 1)
  }, [build, idx, eligible, go])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement
      if (el && (el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.tagName === 'TEXTAREA') && !['Escape'].includes(e.key)) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const k = e.key
      if (k === 'ArrowRight' || k === 'PageDown' || (k === ' ' && el?.tagName !== 'BUTTON')) { e.preventDefault(); next() }
      else if (k === 'ArrowLeft' || k === 'PageUp') { e.preventDefault(); prev() }
      else if (k === 'Escape') { setOverview(false); setGlossary(false) }
      else if (k === 'o' || k === 'O') setOverview((v) => !v)
      else if (k === 'g' || k === 'G') setGlossary((v) => !v)
      else if (k === 'e' || k === 'E') setEngineer((v) => !v)
      else if (k === 'h' || k === 'H') setContrast((v) => !v)
      else if (k === 'c' || k === 'C') setCorePath((v) => !v)
      else if (k === 'f' || k === 'F') { if (!document.fullscreenElement) document.documentElement.requestFullscreen?.(); else document.exitFullscreen?.() }
      else if (/^[1-7]$/.test(k)) { const i = SLIDES.findIndex((s) => s.chapter === Number(k)); if (i >= 0) go(i, 0) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [next, prev, go])

  const seenTerms = useMemo(() => {
    const order = SLIDES.map((s) => s.id)
    const cur = order.indexOf(slide.id)
    return GLOSSARY.filter((t) => order.indexOf(t.slide) <= cur)
  }, [slide.id])

  const Comp = slide.C
  const elapsed = Math.floor((now - t0) / 1000)
  const isFirst = idx === 0 && build === 0
  const isLast = idx === SLIDES.length - 1 && build >= slide.builds - 1

  return (
    <Pres.Provider value={{ slide: slide.id, engineer, build }}>
      <div className="viewport">
        <div className="stage" style={{ transform: `translate(-50%, -50%) scale(${scale})` }}>
          {/* status strip */}
          <div className="statusbar">
            <div className="wordmark"><span className="bay" />StorageGuard <small>control room</small></div>
            <nav className="chapters" aria-label="Chapters">
              {CHAPTERS.map((c, i) => (
                <div key={c.n} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  {i > 0 && <span className="chap-sep" />}
                  <button className={`chap${slide.chapter === c.n ? ' on' : slide.chapter > c.n ? ' done' : ''}`}
                    onClick={() => go(SLIDES.findIndex((s) => s.chapter === c.n), 0)}>
                    {c.n <= 6 && <span className="n">{slide.chapter > c.n ? '✓' : c.n}</span>}
                    {c.title}
                  </button>
                </div>
              ))}
            </nav>
            <div className="status-right">
              <span className="backend" title={backend === 'live' ? 'The Python backend is running: the guard decides live' : 'Backend not reachable: showing data recorded earlier'}>
                <span className={`led ${backend === 'live' ? 'on-green' : backend === 'offline' ? 'on-amber' : ''}`} />
                {backend === 'live' ? 'Live backend' : backend === 'offline' ? 'Recorded data' : 'Connecting'}
              </span>
              <button className="timer" onClick={() => setT0(Date.now())} title="Talk time. Click to restart.">{fmt(elapsed)}</button>
            </div>
          </div>

          {/* screen */}
          <AnimatePresence mode="wait">
            <motion.main key={slide.id} className="slide" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}>
              <Comp build={build} setBuild={setBuild} go={(id: string) => go(SLIDES.findIndex((s) => s.id === id), 0)} />
            </motion.main>
          </AnimatePresence>


          {/* overview */}
          {overview && (
            <div className="overlay" onClick={() => setOverview(false)}>
              <h2>All screens</h2>
              <p className="muted small" style={{ marginTop: -8 }}>Core path: {SLIDES.filter((s) => s.core).length} screens. Detours are marked and skipped while the core path is on (press <kbd>C</kbd>).</p>
              {CHAPTERS.map((c) => (
                <div key={c.n} style={{ marginBottom: 18 }}>
                  <div className="small ink2" style={{ marginBottom: 8 }}>{c.n <= 6 ? `${c.n}. ` : ''}{c.title}</div>
                  <div className="ov-grid">
                    {SLIDES.filter((s) => s.chapter === c.n).map((s) => (
                      <button key={s.id} className={`ov-card${s.id === slide.id ? ' on' : ''}`} onClick={(e) => { e.stopPropagation(); go(SLIDES.indexOf(s), 0); setOverview(false) }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}><span className="sid">{s.id}</span>{!s.core && <span className="tag-detour">detour</span>}</div>
                        <div className="t">{s.title}</div>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* glossary */}
          {glossary && (
            <aside className="drawer" aria-label="Glossary">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <h2>Glossary</h2>
                <button className="btn sm" onClick={() => setGlossary(false)}>Close</button>
              </div>
              <p className="muted small" style={{ margin: '0 0 10px' }}>Terms introduced so far ({seenTerms.length} of {GLOSSARY.length}).</p>
              {seenTerms.map((t) => (
                <div key={t.id} className={`gl-item${t.slide === slide.id ? ' fresh' : ''}`}>
                  <b>{t.term}</b> <span className="tiny muted">from {t.slide}</span>
                  <p>{t.plain}</p>
                </div>
              ))}
            </aside>
          )}

          {/* transport */}
          <div className="transport">
            <div className="where">
              <span className="sid">{slide.id}</span>
              <span className="stitle">{slide.title}</span>
              {!slide.core && <span className="tag-detour">detour</span>}
            </div>
            <div className="notice" role="status" title={toast ? toast.join(', ') : undefined} style={{ visibility: toast && !overview ? 'visible' : 'hidden' }}>
              <span className="led on-brand" style={{ flex: 'none' }} />
              <span className="txt">New in the glossary: {toast?.join(', ')}</span>
              <span className="muted" style={{ flex: 'none' }}><kbd>G</kbd> to see them</span>
            </div>
            <div className="builds" aria-hidden>
              {slide.builds > 1 && Array.from({ length: slide.builds }, (_, i) => <i key={i} className={i <= build ? 'on' : ''} />)}
            </div>
            <div className="keys">
              <button className={corePath ? 'on' : ''} onClick={() => setCorePath((v) => !v)} title="Skip detours"><kbd>C</kbd>{corePath ? 'Core path' : 'All screens'}</button>
              <button className={glossary ? 'on' : ''} onClick={() => setGlossary((v) => !v)}><kbd>G</kbd>Glossary</button>
              <button className={engineer ? 'on' : ''} onClick={() => setEngineer((v) => !v)}><kbd>E</kbd>Engineer view</button>
              <button className={overview ? 'on' : ''} onClick={() => setOverview((v) => !v)}><kbd>O</kbd>Overview</button>
              <button className={contrast ? 'on' : ''} onClick={() => setContrast((v) => !v)}><kbd>H</kbd>Contrast</button>
              <button className="navbtn" onClick={prev} disabled={isFirst} aria-label="Previous">‹</button>
              <button className="navbtn" onClick={next} disabled={isLast} aria-label="Next">›</button>
            </div>
          </div>
        </div>
      </div>
    </Pres.Provider>
  )
}

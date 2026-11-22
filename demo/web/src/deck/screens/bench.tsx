// Chapter 3: the test bed (StorageAgentBench).
import { fetchResults, useLoad } from '../../lib/api'
import { C } from '../Graph'
import { AnimatePresence, motion } from 'framer-motion'
import { Frame, type ScreenProps } from '../ui'

const GROUPS = [
  { key: 'routine', label: 'routine', color: C.node2 },
  { key: 'ambiguous', label: 'unclear', color: '#8a83a0' },
  { key: 'trap', label: 'traps', color: C.rewrite },
  { key: 'injection', label: 'planted instructions', color: C.harm },
]

const rnd = (i: number) => {
  const x = Math.sin(i * 12.9898 + 78.233) * 43758.5453
  return x - Math.floor(x)
}

export function Bench({ build }: ScreenProps) {
  const { data: res } = useLoad(fetchResults, [])
  const counts: Record<string, number> = { routine: 45, ambiguous: 25, trap: 65, injection: 15 }
  if (res) {
    for (const g of GROUPS) counts[g.key] = res.benchmark.filter((r: any) => r.category === g.key).reduce((a: number, r: any) => a + r.total, 0)
  }
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  const traps = res ? new Set(res.benchmark.filter((r: any) => r.category === 'trap').map((r: any) => r.type)).size : 12
  const sorted = build >= 1
  // tiles: one per ticket, in category order
  const tiles: { g: number; i: number }[] = []
  GROUPS.forEach((g, gi) => { for (let i = 0; i < counts[g.key]; i++) tiles.push({ g: gi, i }) })
  const S = 26
  const P = 34
  const pos = (t: { g: number; i: number }, k: number) => {
    if (!sorted) {
      const cols = 15
      return { x: 180 + (k % cols) * 40, y: 110 + Math.floor(k / cols) * 40 }
    }
    const cols = 5
    const bx = 40 + t.g * 230
    return { x: bx + (t.i % cols) * P, y: 560 - S - Math.floor(t.i / cols) * P }
  }
  return (
    <Frame
      head={sorted ? `${counts.trap} of them hide a trap.` : `We built ${total} test tickets.`}
      support={sorted
        ? `A hidden link makes the obvious plan harmful. ${counts.injection} more carry instructions planted in the data.`
        : 'They run on a pretend storage company that behaves like a real one, so nothing real can break.'}
      visual={
        <svg viewBox="0 0 960 660" preserveAspectRatio="xMidYMid meet">
          {tiles.map((t, k) => {
            const p = pos(t, k)
            const fill = sorted ? GROUPS[t.g].color : C.node
            const start = { x: rnd(k) * 900, y: rnd(k + 500) * 600 }
            return (
              <rect key={k} width={S} height={S} rx={5}
                style={{
                  fill,
                  transform: `translate(${p.x}px, ${p.y}px)`,
                  transition: `transform 0.9s cubic-bezier(.2,.8,.2,1) ${(sorted ? t.i * 6 + t.g * 60 : k * 4)}ms, fill 0.5s ${(sorted ? 300 + t.g * 120 : 0)}ms`,
                  animation: `tile-in 1.1s cubic-bezier(.2,.8,.2,1) ${k * 4}ms backwards`,
                  ['--sx' as string]: `${start.x}px`,
                  ['--sy' as string]: `${start.y}px`,
                }} />
            )
          })}
          {GROUPS.map((g, gi) => (
            <g key={g.key} style={{ opacity: sorted ? 1 : 0, transition: `opacity 0.5s ${sorted ? 700 + gi * 120 : 0}ms` }}>
              <text x={40 + gi * 230} y={604} className="svg-label strong" style={{ fontSize: 30, fontWeight: 300, fill: C.ink }}>{counts[g.key]}</text>
              <text x={40 + gi * 230} y={630} className="svg-label">{g.label}</text>
            </g>
          ))}
        </svg>
      }
    >
      <div className="d-fill" />
      <AnimatePresence mode="wait" initial={false}>
        <motion.div key={sorted ? 'traps' : 'key'} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.45, delay: sorted ? 0.6 : 0 }}>
          {sorted ? (
            <>
              <div className="d-big">{traps}</div>
              <div className="d-big-label">kinds of trap, each based on a real outage or on how real storage arrays behave.</div>
            </>
          ) : (
            <div className="d-big-label" style={{ marginTop: 0 }}>Every run starts from exactly the same company. An answer key that the agent never sees marks what was harmed.</div>
          )}
        </motion.div>
      </AnimatePresence>
    </Frame>
  )
}

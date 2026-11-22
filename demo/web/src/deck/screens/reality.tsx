// Chapter 6: reality check, and the close.
import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import { fetchResults, useLoad } from '../../lib/api'
import { C } from '../Graph'
import { Frame, range, type ScreenProps } from '../ui'
import { noGuardRange } from './problem'

// results/physical/REPORT.md: "H1 data loss vs data destroyed/rolled back | 866 | 0 | 0 | 10054 | 100.0 | 1.00"
const AGREE = { both: 866, oracleOnly: 0, physicalOnly: 0 }
const N = 866

export function Real({ build }: ScreenProps) {
  const { data: res } = useLoad(fetchResults, [])
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    const t = setTimeout(() => setArmed(true), 350)
    return () => clearTimeout(t)
  }, [])
  const z = res?.zfs
  const phys = res?.physical || []
  const b0 = phys.find((r: any) => r.config === 'B0 No guard')
  const b4 = phys.find((r: any) => r.config === 'B4 StorageGuard')
  const tb = b0 ? Math.round(b0.tb_destroyed) : 105
  const zfsOn = z ? Math.round(z.agree_pct) : 96

  const place = (i: number) => {
    if (build === 0) return { x: 100 + (i % 40) * 19, y: 100 + Math.floor(i / 40) * 19, k: 14, on: true, fill: armed ? C.safe : C.node }
    if (build === 1) {
      if (i >= 100) return { x: 360 + (i % 10) * 24, y: 320 + Math.floor((i % 100) / 10) * 24, k: 0, on: false, fill: C.node }
      return { x: 250 + (i % 10) * 46, y: 90 + Math.floor(i / 10) * 46, k: 38, on: true, fill: i < zfsOn ? C.safe : C.node2 }
    }
    if (i >= tb) return { x: 600 + (i % 10) * 24, y: 320, k: 0, on: false, fill: C.harm }
    const row = Math.floor(i / 10)
    return { x: 90 + (i % 10) * 30, y: 520 - row * 30, k: 24, on: true, fill: C.harm }
  }
  const heads = ['We replayed every run on real files.', 'Then again on real storage software.', 'With StorageGuard, no data was destroyed.']
  const supports = [
    'One file per volume, snapshot and backup, in a throwaway folder. Deletes really delete.',
    'OpenZFS, a real file system with snapshots and replication, inside a throwaway virtual machine.',
    `Data destroyed or rolled back across ${b0?.episodes ?? 936} replayed runs per method, one square per terabyte.`,
  ]
  return (
    <Frame
      head={heads[build]}
      support={supports[build]}
      visual={
        <svg viewBox="0 0 960 660" preserveAspectRatio="xMidYMid meet">
          {Array.from({ length: N }, (_, i) => {
            const p = place(i)
            const d = build === 0 ? i * 1.3 : build === 1 ? (i % 10 + Math.floor(i / 10)) * 18 : i * 7
            return (
              <rect key={i} width={1} height={1} rx={0.18}
                style={{
                  fill: p.fill, opacity: p.on ? 1 : 0,
                  transform: `translate(${p.x}px, ${p.y}px) scale(${p.k})`,
                  transition: `transform 0.9s cubic-bezier(.2,.8,.2,1) ${build === 0 ? 0 : d}ms, fill 0.5s ${d}ms, opacity 0.5s ${p.on ? d : 0}ms`,
                }} />
            )
          })}
          <g style={{ opacity: build === 2 ? 1 : 0, transition: 'opacity 0.6s 0.8s' }}>
            <rect x={560} y={220} width={300} height={324} rx={10} fill="none" stroke={C.guard} strokeDasharray="4 6" />
            <text x={710} y={398} textAnchor="middle" className="svg-label" style={{ fill: C.guard, fontSize: 44, fontWeight: 300 }}>0 TB</text>
            <text x={90} y={584} className="svg-label strong">No guard</text>
            <text x={560} y={584} className="svg-label strong" style={{ fill: C.guard }}>StorageGuard</text>
          </g>
        </svg>
      }
    >
      <div className="d-fill" />
      <AnimatePresence mode="wait" initial={false}>
        <motion.div key={build} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.45, delay: 0.2 }}>
          {build === 0 && (
            <>
              <div className="d-big safe-ink">{AGREE.both} of {AGREE.both + AGREE.oracleOnly + AGREE.physicalOnly}</div>
              <div className="d-big-label">times our answer key said data was lost, real deleted files agreed. It never claimed a loss the files did not show.</div>
            </>
          )}
          {build === 1 && (
            <>
              <div className="d-big safe-ink">{z?.agree_pct ?? 95.7}%</div>
              <div className="d-big-label">of {z?.comparable?.toLocaleString() ?? '2,593'} calls behaved the same as in the simulation. Every delete removed exactly the same things.</div>
            </>
          )}
          {build === 2 && (
            <>
              <div className="d-big guard-ink">{b4?.tb_destroyed ?? 0} TB</div>
              <div className="d-big-label">with StorageGuard, against {b0?.tb_destroyed ?? 104.9} TB with no guard.</div>
            </>
          )}
        </motion.div>
      </AnimatePresence>
    </Frame>
  )
}

// ---------------------------------------------------------------- the close
export function End({ build }: ScreenProps) {
  const { data: res } = useLoad(fetchResults, [])
  const sg = res ? range([...res.main_table.filter((r: any) => r.config === 'B4 StorageGuard').map((r: any) => r.uar), ...res.scale.map((r: any) => r.B4_uar)]) : '0–2'
  const items = [
    { big: `${res ? noGuardRange(res) : '40–53'}%`, cls: 'harm-ink', text: 'of tickets went wrong when AI agents ran storage with no guard, at every model size. The danger hid in links the ticket never mentioned.' },
    { big: `${sg}%`, cls: 'guard-ink', text: 'with StorageGuard trying each change on a copy first. And more tickets got done, not fewer.' },
    { big: `${AGREE.both} / ${AGREE.both}`, cls: '', text: 'times the answer key’s “data lost” matched real deleted files.' },
  ]
  return (
    <div className="d-screen">
      <div className="d-end">
        <h1 className="d-head short" style={{ minHeight: 0 }}>{build >= 3 ? 'Questions?' : 'Three things to remember.'}</h1>
        <div className="d-end-rows">
          {items.map((it, i) => (
            <motion.div key={i} className="d-end-row" initial={false} animate={{ opacity: build >= i ? 1 : 0.0, y: build >= i ? 0 : 14 }} transition={{ duration: 0.55, ease: 'easeOut' }}>
              <div className={`d-big ${it.cls}`}>{it.big}</div>
              <div className="d-end-text">{it.text}</div>
            </motion.div>
          ))}
        </div>
      </div>
    </div>
  )
}

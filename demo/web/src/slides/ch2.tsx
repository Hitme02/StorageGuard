// Chapter 2: the benchmark (StorageAgentBench).
import { motion } from 'framer-motion'
import { useState } from 'react'
import { Bays, HBars, type BayState } from '../components/charts'
import { Eng, Loading, SlideHead, Term } from '../components/ui'
import { TRAP, TRAPS } from '../content/text'
import { fetchManifest, fetchResults, fetchTasks, useLoad } from '../lib/api'
import { HARM } from '../lib/plain'
import { setPicked } from '../lib/store'
import type { SlideProps } from './types'

const reveal = (on: boolean, delay = 0) => ({
  initial: false as const,
  animate: { opacity: on ? 1 : 0, y: on ? 0 : 10 },
  transition: { duration: 0.4, delay: on ? delay : 0 },
})

// Inventory of the estate behind the hook ticket (task-854ce268), read from the twin's public API.
const INVENTORY: [string, number, string][] = [
  ['Data centres', 2, 'a main site and a disaster-recovery site'],
  ['Storage arrays', 2, 'one per site'],
  ['Pools', 6, 'groups of disks, with spare disks'],
  ['Disks', 68, ''],
  ['Apps', 7, 'each with a tier, 1 to 3'],
  ['Servers', 10, 'in 3 clusters, for 3 tenants'],
  ['Volumes', 23, ''],
  ['Snapshots', 57, 'some locked, some scanned for malware'],
  ['Replication links', 5, ''],
  ['Backups', 27, 'on a backup volume and in an offsite vault'],
  ['Fibre Channel zones', 20, 'which server can reach which array'],
]

// ---------------------------------------------------------------- 2.1 a pretend company
export function Twin({ build }: SlideProps) {
  return (
    <>
      <SlideHead title="We built a pretend company to test on." lede={<>You cannot let an AI delete real data to see what happens. So every ticket runs on a simulated storage estate, a <Term id="twin">digital twin</Term>, that behaves like the real thing.</>} />
      <div className="row grow" style={{ gap: 26 }}>
        <div className="panel" style={{ width: 560 }}>
          <div className="panel-title">The estate behind the ticket you saw</div>
          {INVENTORY.map(([k, v, note]) => (
            <div key={k} style={{ display: 'grid', gridTemplateColumns: '64px 1fr', gap: 14, padding: '3px 0', borderTop: '1px solid var(--line)', alignItems: 'baseline' }}>
              <span style={{ fontFamily: 'var(--cond)', fontSize: 26, fontWeight: 600, textAlign: 'right' }}>{v}</span>
              <span style={{ fontSize: 18 }}>{k}{note && <span className="muted small">, {note}</span>}</span>
            </div>
          ))}
        </div>
        <div className="col grow" style={{ gap: 16 }}>
          <motion.div className="panel" {...reveal(build >= 0)}>
            <div style={{ fontFamily: 'var(--cond)', fontSize: 30, fontWeight: 600 }}>It behaves like an array</div>
            <div className="ink2" style={{ fontSize: 19 }}>42 tools, the same ones an admin would use. The same refusals too: it will not delete a volume that is still replicating unless you force it.</div>
          </motion.div>
          <motion.div className="panel" {...reveal(build >= 1)}>
            <div style={{ fontFamily: 'var(--cond)', fontSize: 30, fontWeight: 600 }}>Same start, every time</div>
            <div className="ink2" style={{ fontSize: 19 }}>Each ticket rebuilds its estate from a fixed seed and checks a fingerprint, so every AI, and every safety method, faces exactly the same situation.</div>
          </motion.div>
          <motion.div className="panel" {...reveal(build >= 2)} style={{ borderColor: 'var(--brand)' }}>
            <div style={{ fontFamily: 'var(--cond)', fontSize: 30, fontWeight: 600 }}>An <Term id="oracle">answer key</Term> the agent never sees</div>
            <div className="ink2" style={{ fontSize: 19 }}>It knows the hidden truth, such as which snapshots are really clean after a ransomware attack, and judges what actually changed when the agent is done.</div>
          </motion.div>
          <Eng>
            Twin: <span className="mono">src/storageguard/twin/</span> (pydantic state, 42 operations, operation log, <span className="mono">clone()</span>).
            Answer key: <span className="mono">eval/harm_oracle.py</span>, import-isolated from the guard, reads ground truth such as <span className="mono">true_clean</span>.
          </Eng>
        </div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------- 2.2 150 tickets, 12 traps
const CATS: { key: string; label: string; n: number; led: BayState; cols: number; plain: string }[] = [
  { key: 'routine', label: 'Routine', n: 45, led: 'white', cols: 15, plain: 'ordinary requests, no catch' },
  { key: 'ambiguous', label: 'Ambiguous', n: 25, led: 'white', cols: 9, plain: 'unclear; a good admin would ask' },
  { key: 'trap', label: 'Trap', n: 65, led: 'amber', cols: 22, plain: 'a hidden dependency makes the obvious plan harmful' },
  { key: 'injection', label: 'Planted instructions', n: 15, led: 'red', cols: 5, plain: 'the data itself tries to steer the agent' },
]

export function Tickets() {
  const { data: tasks } = useLoad(fetchTasks, [])
  const { data: manifest } = useLoad(fetchManifest, [])
  const { data: res } = useLoad(fetchResults, [])
  const [sel, setSel] = useState('T1')
  if (!tasks || !manifest || !res) return <Loading />
  const info = TRAP[sel]
  const example = tasks.find((t) => t.trap === sel && manifest.showcase.includes(t.id)) || tasks.find((t) => t.trap === sel)
  const heat = res.heatmap.find((r: any) => r.trap === sel)
  return (
    <>
      <SlideHead title="150 tickets. 65 of them hide a trap." lede={<>Each <Term id="trap">trap</Term> hides a dependency drawn from real array behaviour or a real outage. Fifteen more carry <Term id="injection">planted instructions</Term>.</>} />
      <div className="row" style={{ gap: 14, alignItems: 'flex-end' }}>
        {CATS.map((c) => (
          <div key={c.key} style={{ flex: Math.max(c.cols, 9) }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 6 }}>
              <span style={{ fontFamily: 'var(--cond)', fontSize: 26, fontWeight: 600 }}>{c.n}</span>
              <span style={{ fontSize: 17 }}>{c.label}</span>
            </div>
            <Bays states={Array(c.n).fill(c.led)} cols={c.cols} bayHeight={16} label={`${c.n} ${c.label} tickets`} />
            <div className="tiny muted" style={{ marginTop: 4 }}>{c.plain}</div>
          </div>
        ))}
      </div>
      <div className="row grow" style={{ gap: 20 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, width: 900, alignContent: 'start' }}>
          {TRAPS.map((t) => (
            <button key={t.id} onClick={() => setSel(t.id)} className="panel" style={{
              textAlign: 'left', padding: '8px 12px', cursor: 'pointer', display: 'grid', gridTemplateColumns: '38px 1fr', gap: 8, alignItems: 'center',
              borderColor: sel === t.id ? 'var(--brand)' : undefined, background: sel === t.id ? 'var(--brand-wash)' : undefined,
            }}>
              <span style={{ fontFamily: 'var(--cond)', fontWeight: 600, fontSize: 18, color: t.id.startsWith('P') ? '#f07b7f' : 'var(--rewrite)' }}>{t.id}</span>
              <span style={{ fontSize: 15, lineHeight: 1.25 }}>{t.name}</span>
            </button>
          ))}
        </div>
        <div className="panel col grow" style={{ gap: 12 }}>
          <div style={{ fontFamily: 'var(--cond)', fontSize: 28, fontWeight: 600, lineHeight: 1.1 }}>{info.id}: {info.name}</div>
          <div style={{ fontSize: 18 }}>{info.danger}</div>
          {example && (
            <div className="ticket" style={{ fontSize: 16, padding: '10px 14px' }}>
              <div className="meta"><span>Example ticket</span></div>
              {example.instruction.split('\n\n').pop()}
            </div>
          )}
          <div className="small ink2"><span className="muted">Based on: </span>{info.source}</div>
          <div style={{ display: 'flex', gap: 22, alignItems: 'baseline', marginTop: 'auto' }}>
            {heat && <div><span style={{ fontFamily: 'var(--cond)', fontSize: 36, fontWeight: 600, color: '#f07b7f' }}>{heat.mean.toFixed(0)}%</span> <span className="small ink2">of runs unsafe without a guard (mean of 7 models)</span></div>}
            {example && <button className="btn sm" onClick={() => setPicked(example.id)}>Use this ticket in the control room</button>}
          </div>
        </div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------- 2.3 how we judge harm (detour)
export function HarmTypes() {
  const { data: res } = useLoad(fetchResults, [])
  if (!res) return <Loading />
  const ht = res.harm_types
  const order = ['H3', 'H1', 'H2', 'H5', 'H4', 'H6']
  return (
    <>
      <SlideHead title="The answer key counts six kinds of harm." lede={<>It compares the estate before and after, using the hidden truth. These are the <Term id="harm">harm types</Term>, with how often each appeared when agents worked without a guard.</>} />
      <div className="row grow" style={{ gap: 30 }}>
        <div className="col" style={{ width: 640, gap: 10 }}>
          {order.map((h) => (
            <div key={h} className="panel" style={{ padding: '10px 16px', display: 'grid', gridTemplateColumns: '48px 1fr', gap: 12, alignItems: 'baseline' }}>
              <span style={{ fontFamily: 'var(--cond)', fontSize: 22, fontWeight: 600, color: 'var(--ink-3)' }}>{h}</span>
              <span><b style={{ fontSize: 19 }}>{HARM[h].label}</b><span className="ink2">: {HARM[h].plain}</span></span>
            </div>
          ))}
        </div>
        <div className="col grow">
          <div className="panel">
            <div className="panel-title">Share of unsafe no-guard runs with each harm (a run can have several)</div>
            <HBars data={order.map((h) => ({ label: HARM[h].label, value: ht[h], color: 'var(--harm)' }))} width={760} max={60} digits={0} labelW={170} title="Harm types" />
            <div className="small ink2" style={{ marginTop: 8 }}>Median data at stake in an unsafe run: <b style={{ color: 'var(--ink)' }}>{ht.median_gb} GB</b>.</div>
          </div>
          <div className="panel" style={{ fontSize: 19 }}>
            Every ticket is checked before use: its correct plan finishes without harm, and its careless plan causes harm. So a trap really is a trap.
          </div>
        </div>
      </div>
    </>
  )
}

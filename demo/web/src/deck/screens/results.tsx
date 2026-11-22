// Chapter 5: results. All numbers from public/data/results.json (results/REPORT.md, aggregates.csv).
import { AnimatePresence, motion } from 'framer-motion'
import { useState } from 'react'
import { fetchResults, useLoad } from '../../lib/api'
import { MAIN_MODELS, MODELS } from '../../lib/plain'
import { C } from '../Graph'
import { Chips, Count, Frame, range, type ScreenProps } from '../ui'

const row = (res: any, m: string, c: string) => res.main_table.find((r: any) => r.model === m && r.config === c)

function ModelPick({ model, setModel }: { model: string; setModel: (m: string) => void }) {
  return (
    <div style={{ marginTop: 28 }}>
      <Chips value={model} onChange={setModel} options={MAIN_MODELS.map((m) => ({ v: m, label: MODELS[m].label }))} />
    </div>
  )
}

// ---------------------------------------------------------------- 8. out of 100 tickets
export function Hundred({ build }: ScreenProps) {
  const { data: res } = useLoad(fetchResults, [])
  const [model, setModel] = useState('llama3.1-8b')
  if (!res) return <Frame head="Out of 100 tickets, how many went wrong?" />
  const r0 = row(res, model, 'B0 No guard')
  const r4 = row(res, model, 'B4 StorageGuard')
  const r = build >= 1 ? r4 : r0
  const harm = Math.round(r.uar)
  const done = Math.round(r.tsr)
  const all = (c: string, k: string) => MAIN_MODELS.map((m) => row(res, m, c)[k])
  const heads = [`With no guard, ${Math.round(r0.uar)} in 100 tickets did harm.`, `With StorageGuard, ${Math.round(r4.uar)} did.`, 'Safer, and more tickets get done.']
  const supports = [
    'Each dot is a test ticket. Coral: the agent did harm. Mint: it finished the job safely. Grey: safe, but not finished.',
    `And ${Math.round(r4.tsr)} finished safely, up from ${Math.round(r0.tsr)}. The guard explains each refusal, so the agent finds a safe way through.`,
    'The same holds for all three AI models we tested in full.',
  ]
  return (
    <Frame
      head={heads[build]}
      support={supports[build]}
      visual={
        <svg viewBox="0 0 960 660" preserveAspectRatio="xMidYMid meet">
          {Array.from({ length: 100 }, (_, i) => {
            const fill = i < harm ? C.harm : i < harm + done ? C.safe : C.node
            const cx = 246 + (i % 10) * 52
            const cy = 96 + Math.floor(i / 10) * 52
            return <circle key={i} className="dot" cx={cx} cy={cy} r={17} style={{ fill, transition: `fill 0.6s ${((i % 10) + Math.floor(i / 10)) * 28}ms`, animation: `dot-in 0.6s ${i * 6}ms backwards` }} />
          })}
        </svg>
      }
    >
      {build < 2 && <ModelPick model={model} setModel={setModel} />}
      <div className="d-fill" />
      <AnimatePresence mode="wait" initial={false}>
        {build < 2 ? (
          <motion.div key="pair" className="d-pair" exit={{ opacity: 0, y: -8 }}>
            <div>
              <div className="d-big harm-ink"><Count to={harm} /></div>
              <div className="d-big-label">did harm</div>
            </div>
            <div>
              <div className="d-big safe-ink"><Count to={done} /></div>
              <div className="d-big-label">finished safely</div>
            </div>
          </motion.div>
        ) : (
          <motion.div key="ranges" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }} style={{ display: 'flex', flexDirection: 'column', gap: 26 }}>
            <div>
              <div className="d-big guard-ink" style={{ fontSize: 84 }}>{range(all('B4 StorageGuard', 'uar'))}%</div>
              <div className="d-big-label">did harm with StorageGuard, down from {range(all('B0 No guard', 'uar'))}% with no guard.</div>
            </div>
            <div>
              <div className="d-big safe-ink" style={{ fontSize: 84 }}>{range(all('B4 StorageGuard', 'tsr'))}%</div>
              <div className="d-big-label">finished safely, up from {range(all('B0 No guard', 'tsr'))}%.</div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </Frame>
  )
}

// ---------------------------------------------------------------- 9. against the alternatives
const METHODS = [
  { c: 'B0 No guard', label: 'No guard', b: 0 },
  { c: 'B1 Prompt rules', label: 'Rules in the prompt', b: 0 },
  { c: 'B3 LLM judge', label: 'AI checker', b: 1 },
  { c: 'B2 RBAC approval', label: 'Ask a person before every delete', b: 1 },
  { c: 'B4 StorageGuard', label: 'StorageGuard', b: 2 },
]
const PX = (a: number) => 110 + (a / 1.5) * 760
const PY = (u: number) => 560 - (u / 55) * 480

export function Versus({ build }: ScreenProps) {
  const { data: res } = useLoad(fetchResults, [])
  const [model, setModel] = useState('llama3.1-8b')
  if (!res) return <Frame head="Two costs: harm, and interrupting people." />
  const pts = METHODS.filter((m) => m.b <= build).map((m) => {
    const r = row(res, model, m.c)
    return { ...m, x: PX(r.esc), y: PY(r.uar), uar: r.uar, esc: r.esc, ly: PY(r.uar) + 5 }
  })
  // keep labels of nearby points apart
  const sorted = [...pts].sort((a, b) => a.ly - b.ly)
  for (let i = 1; i < sorted.length; i++) {
    for (let j = 0; j < i; j++) {
      if (Math.abs(sorted[i].x - sorted[j].x) < 220 && sorted[i].ly - sorted[j].ly < 24) sorted[i].ly = sorted[j].ly + 24
    }
  }
  const sg = row(res, model, 'B4 StorageGuard')
  const rb = row(res, model, 'B2 RBAC approval')
  const stamp = res.sensitivity.find((r: any) => r.config === 'B2 RBAC approval (human=rubber_stamp)')
  const heads = ['Two costs: harm, and interrupting people.', 'Asking a person first is safer, but slow.', 'StorageGuard is the safest, and rarely asks.']
  const supports = [
    'Higher up means more tickets with harm. Further right means a person is asked more often.',
    `And it only works if that person always knows the answer. With one who just says yes, ${stamp ? `${stamp.uar}%` : 'half'} of tickets did harm.`,
    `${MODELS[model].label}: ${sg.uar}% harm and ${sg.esc} asks per ticket, against ${rb.uar}% and ${rb.esc} when asking before every delete.`,
  ]
  return (
    <Frame
      head={heads[build]}
      support={supports[build]}
      visual={
        <svg viewBox="0 0 960 660" preserveAspectRatio="xMidYMid meet">
          <line x1={110} y1={560} x2={880} y2={560} stroke={C.line} strokeWidth={1.5} />
          <line x1={110} y1={560} x2={110} y2={70} stroke={C.line} strokeWidth={1.5} />
          {[0, 0.5, 1, 1.5].map((v) => <text key={v} x={PX(v)} y={588} textAnchor="middle" className="svg-sub">{v}</text>)}
          {[0, 25, 50].map((v) => <text key={v} x={96} y={PY(v) + 5} textAnchor="end" className="svg-sub">{v}%</text>)}
          <text x={880} y={622} textAnchor="end" className="svg-label">times a person is asked, per ticket</text>
          <text x={110} y={46} className="svg-label">tickets with harm</text>
          {pts.map((p) => {
            const isSg = p.c === 'B4 StorageGuard'
            return (
              <g key={p.c}>
                <motion.circle r={isSg ? 13 : 10} fill={isSg ? C.guard : C.ink2}
                  initial={{ cx: p.x, cy: p.y - 60, opacity: 0 }} animate={{ cx: p.x, cy: p.y, opacity: 1 }}
                  transition={{ type: 'spring', stiffness: 90, damping: 12, delay: isSg ? 0.3 : 0.1 }} />
                {isSg && <motion.circle fill="none" stroke={C.guard} initial={{ cx: p.x, cy: p.y, r: 13, opacity: 0 }} animate={{ cx: p.x, cy: p.y, r: [13, 34], opacity: [0.7, 0] }} transition={{ duration: 1.4, delay: 0.7 }} />}
                <motion.text className={`svg-label${isSg ? ' strong' : ''}`} style={{ fill: isSg ? C.guard : C.ink }}
                  initial={{ x: p.x + 22, y: p.ly, opacity: 0 }} animate={{ x: p.x + 22, y: p.ly, opacity: 1 }} transition={{ type: 'spring', stiffness: 90, damping: 16, delay: 0.3 }}>
                  {p.label} <tspan style={{ fill: C.ink3, fontWeight: 400 }}>{p.uar}%</tspan>
                </motion.text>
              </g>
            )
          })}
        </svg>
      }
    >
      <ModelPick model={model} setModel={setModel} />
    </Frame>
  )
}

// ---------------------------------------------------------------- 10. bigger models
const SX = (i: number) => 140 + i * 220
const SY = (u: number) => 540 - (u / 60) * 440

export function Scale({ build }: ScreenProps) {
  const { data: res } = useLoad(fetchResults, [])
  if (!res) return <Frame head="Bigger AI models don’t fix it." />
  const rows = res.scale.filter((r: any) => r.model !== 'qwen2.5-7b-awq')
  const trap = res.scale_category.filter((r: any) => r.model !== 'qwen2.5-7b-awq').map((r: any) => r.trap_UAR)
  const series = [
    { k: 'B0_uar', color: C.ink2, on: true, delay: 0.3 },
    { k: 'B4_uar', color: C.guard, on: build >= 1, delay: 0.2 },
  ]
  const heads = ['Bigger AI models don’t fix it.', 'StorageGuard works at every size.']
  const supports = [
    `With no guard, ${range(rows.map((r: any) => r.B0_uar))}% of tickets went wrong at every size we tried, from 7 to 72 billion parameters.`,
    `${range(rows.map((r: any) => r.B4_uar))}% of tickets with harm, from the smallest model to the largest.`,
  ]
  return (
    <Frame
      head={heads[build]}
      support={supports[build]}
      visual={
        <svg viewBox="0 0 960 660" preserveAspectRatio="xMidYMid meet">
          <line x1={90} y1={540} x2={900} y2={540} stroke={C.line} strokeWidth={1.5} />
          {[0, 25, 50].map((v) => <g key={v}><line x1={90} x2={900} y1={SY(v)} y2={SY(v)} stroke={C.line} strokeOpacity={v ? 0.4 : 0} /><text x={76} y={SY(v) + 5} textAnchor="end" className="svg-sub">{v}%</text></g>)}
          {rows.map((r: any, i: number) => <text key={r.model} x={SX(i)} y={574} textAnchor="middle" className="svg-label">{r.size}</text>)}
          <text x={900} y={616} textAnchor="end" className="svg-sub">model size, in billions of parameters (Qwen2.5)</text>
          {series.map((s) => s.on && (
            <g key={s.k}>
              <motion.path d={rows.map((r: any, i: number) => `${i ? 'L' : 'M'} ${SX(i)} ${SY(r[s.k])}`).join(' ')} fill="none" stroke={s.color} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round"
                initial={{ pathLength: 0 } as any} animate={{ pathLength: 1 } as any} transition={{ duration: 1.4, delay: s.delay, ease: 'easeInOut' }} />
              {rows.map((r: any, i: number) => (
                <motion.g key={i} initial={{ opacity: 0, scale: 0.3 }} animate={{ opacity: 1, scale: 1 }} transition={{ delay: s.delay + 0.3 + i * 0.35 }}>
                  <circle cx={SX(i)} cy={SY(r[s.k])} r={8} fill={s.color} />
                  <text x={SX(i)} y={SY(r[s.k]) - 20} textAnchor="middle" className="svg-label" style={{ fill: s.color }}>{r[s.k]}%</text>
                </motion.g>
              ))}
              <motion.text x={SX(3) + 22} y={SY(rows[3][s.k]) + 5} className="svg-label strong" style={{ fill: s.color }} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: s.delay + 1.6 }}>
                {s.k === 'B0_uar' ? 'no guard' : 'StorageGuard'}
              </motion.text>
            </g>
          ))}
        </svg>
      }
    >
      <div className="d-fill" />
      <AnimatePresence mode="wait" initial={false}>
        {build === 0 && (
          <motion.div key="trap" exit={{ opacity: 0 }} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.8 }}>
            <div className="d-big harm-ink">{range(trap)}%</div>
            <div className="d-big-label">of trap tickets went wrong with no guard. Bigger models handle unclear tickets better, but not hidden links.</div>
          </motion.div>
        )}
      </AnimatePresence>
    </Frame>
  )
}

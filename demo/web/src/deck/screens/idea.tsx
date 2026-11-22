// Chapter 2: the idea. Try every change on a copy, then answer with a reason.
import { motion } from 'framer-motion'
import { fetchLiveRun, fetchResults, useLoad } from '../../lib/api'
import { nameLookup, plainCall } from '../../lib/plain'
import type { Decision, Run, Step } from '../../lib/types'
import { C, Layer, type NState } from '../Graph'
import { HOOK, HOOK_CHAIN, HOOK_POS, hookEdges, hookNodes, onChain } from '../hook'
import { Frame, Reveal, type ScreenProps } from '../ui'

const AGENT = { x: 90, y: 170 }
const GUARD = { x: 430, y: 170 }
const REAL = { dx: 620, dy: 40, k: 0.33 }
const COPY = { dx: 250, dy: 370, k: 0.33 }

// ---------------------------------------------------------------- 4. try it on a copy
export function Copy({ build }: ScreenProps) {
  const { data: run } = useLoad(() => fetchLiveRun(HOOK, 'scripted:naive', 'storageguard'), [])
  const { data: res } = useLoad(fetchResults, [])
  const map = run?.map
  const at = (o: typeof REAL) => (id: string) => {
    const n = hookNodes(map, () => 'idle').find((x) => x.id === id)!
    return { x: o.dx + n.x * o.k, y: o.dy + n.y * o.k, label: undefined, sub: undefined, scale: 0.7 }
  }
  const real = hookNodes(map, () => 'idle', { place: at(REAL) })
  const copyOn = build >= 1
  const copyState = (id: string): NState => (!copyOn ? 'hidden' : id === 'vol-5' ? 'gone' : id in HOOK_CHAIN ? 'harm' : 'ghost')
  const copy = hookNodes(map, copyState, {
    place: copyOn ? at(COPY) : at(REAL),
    delay: (id) => (copyOn && (id === 'vol-5' || id in HOOK_CHAIN) ? 2.1 + (HOOK_CHAIN[id] ?? 0) * 0.35 : 0),
  })
  const copyEdges = hookEdges(map, (a, b) => (!copyOn ? 'hidden' : onChain(a, b) ? 'harm' : 'ghost'), (a, b) => (onChain(a, b) ? 2.2 + Math.max(HOOK_CHAIN[a] ?? 0, HOOK_CHAIN[b] ?? 0) * 0.35 : 0.4))
  const target = { x: COPY.dx + HOOK_POS['vol-5'].x * COPY.k, y: COPY.dy + HOOK_POS['vol-5'].y * COPY.k }
  const ms = res?.latency?.median ?? 5.2
  const fb = run?.steps.find((s) => s.guard?.decision === 'BLOCK')?.result?.data
  const told = fb ? `Refused: ${String(fb.reasons?.[0] ?? '').replace(/vol-\d+ \(([^)]+)\)/g, '$1')}.` : ''

  const heads = [
    'StorageGuard sits between agent and storage.',
    'It runs each change on a copy first.',
    'Then it tells the agent what would break.',
  ]
  const supports = [
    'Every change the agent asks for has to pass through it.',
    'Like a dress rehearsal. The real storage is not touched.',
    'With a reason and a safer way to do it, so the agent can change its plan.',
  ]
  return (
    <Frame
      head={heads[build]}
      support={supports[build]}
      visual={
        <svg viewBox="0 0 960 660" preserveAspectRatio="xMidYMid meet">
          {/* wires */}
          <line x1={AGENT.x + 34} y1={AGENT.y} x2={GUARD.x - 44} y2={GUARD.y} stroke={C.line} strokeWidth={1.5} />
          <motion.line x1={GUARD.x + 44} y1={GUARD.y} x2={REAL.dx + 20} y2={GUARD.y} stroke={C.line} strokeWidth={1.5} strokeDasharray="3 6" />
          <motion.path d={`M ${GUARD.x} ${GUARD.y + 44} C ${GUARD.x} 300, ${target.x - 120} 300, ${COPY.dx + 140} 380`} fill="none" stroke={C.ink3} strokeWidth={1.2} strokeDasharray="3 5"
            initial={false} animate={{ opacity: copyOn ? 1 : 0 }} transition={{ duration: 0.6, delay: copyOn ? 0.8 : 0 }} />
          <text x={(AGENT.x + GUARD.x) / 2} y={GUARD.y - 22} textAnchor="middle" className="svg-label mono" style={{ fontSize: 14 }}>delete arch-fad9</text>
          <motion.text x={(GUARD.x + REAL.dx) / 2 + 20} y={GUARD.y - 22} textAnchor="middle" className="svg-sub" initial={false} animate={{ opacity: build >= 2 ? 1 : 0 }}>only if it is safe</motion.text>

          {/* agent and guard */}
          <circle cx={AGENT.x} cy={AGENT.y} r={34} fill={C.node2} />
          <text x={AGENT.x} y={AGENT.y + 62} textAnchor="middle" className="svg-label strong">AI agent</text>
          <motion.circle cx={GUARD.x} cy={GUARD.y} r={44} fill="#1d1929" stroke={C.guard} strokeWidth={2.5}
            initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ type: 'spring', stiffness: 80, damping: 12 }} />
          <circle cx={GUARD.x} cy={GUARD.y} r={58} fill="none" stroke={C.guard} strokeOpacity={0.25} />
          <text x={GUARD.x} y={GUARD.y + 84} textAnchor="middle" className="svg-label strong" style={{ fill: C.guard }}>StorageGuard</text>

          {/* the estates */}
          <Layer nodes={real} edges={hookEdges(map)} />
          <text x={REAL.dx + 150} y={300} textAnchor="middle" className="svg-label strong">Real storage</text>
          <motion.text x={REAL.dx + 150} y={322} textAnchor="middle" className="svg-sub" initial={false} animate={{ opacity: copyOn ? 1 : 0 }} transition={{ delay: copyOn ? 2.4 : 0 }}>untouched</motion.text>
          <Layer nodes={copy} edges={copyEdges} />
          <motion.text x={COPY.dx + 150} y={636} textAnchor="middle" className="svg-label strong" initial={false} animate={{ opacity: copyOn ? 1 : 0 }} transition={{ delay: copyOn ? 0.9 : 0 }}>A copy of it</motion.text>

          {/* the call travelling */}
          {build === 0 && (
            <motion.circle key="p0" r={7} fill={C.ink} initial={{ cx: AGENT.x + 34, cy: AGENT.y, opacity: 0 }}
              animate={{ cx: GUARD.x - 50, opacity: [0, 1, 1] }} transition={{ duration: 1.3, delay: 0.5, ease: 'easeInOut' }} />
          )}
          {build === 1 && (
            <motion.circle key="p1" r={7} fill={C.ink}
              initial={{ cx: GUARD.x, cy: GUARD.y + 44, opacity: 0 }}
              animate={{ cx: [GUARD.x, GUARD.x, target.x - 60, target.x], cy: [GUARD.y + 44, 290, 330, target.y], opacity: [0, 1, 1, 0] }}
              transition={{ duration: 1.2, delay: 0.9, ease: 'easeInOut' }} />
          )}
          {build === 2 && (
            <motion.circle key="p2" r={8} fill={C.guard} initial={{ cx: GUARD.x - 50, cy: GUARD.y, opacity: 0 }}
              animate={{ cx: AGENT.x + 40, opacity: [0, 1, 1, 0.9] }} transition={{ duration: 1.2, delay: 0.4, ease: 'easeInOut' }} />
          )}
          <motion.text x={(AGENT.x + GUARD.x) / 2} y={GUARD.y + 34} textAnchor="middle" className="svg-label" style={{ fill: C.guard }}
            initial={false} animate={{ opacity: build >= 2 ? 1 : 0 }} transition={{ delay: build >= 2 ? 1.2 : 0 }}>refused, with a reason</motion.text>
        </svg>
      }
    >
      <div className="d-fill" />
      <Reveal on={build >= 2} delay={1.4}>
        <div className="d-quote">
          <div className="from">What the guard told the agent</div>
          {told}
        </div>
      </Reveal>
      <Reveal on={build >= 2} delay={1.8} style={{ marginTop: 30 }}>
        <div className="d-pair" style={{ alignItems: 'baseline' }}>
          <div className="d-big" style={{ fontSize: 72, whiteSpace: 'nowrap', flex: 'none' }}>{ms} ms</div>
          <div className="d-big-label" style={{ marginTop: 0 }}>median decision time. Plain code, no AI model inside.</div>
        </div>
      </Reveal>
    </Frame>
  )
}

// ---------------------------------------------------------------- 5. six checks, four answers
const STAGES = ['Dry run', 'Follow the links', 'Impact checks', 'Risk score', 'Hard rules', 'Decision']
const SX = (i: number) => 60 + i * 168
const RX = (r: number) => 60 + Math.min(1, r) * 840
const DCOL: Record<Decision, string> = { ALLOW: C.safe, REWRITE: C.rewrite, ESCALATE: C.ask, BLOCK: C.harm }
const DWORD: Record<Decision, string> = { ALLOW: 'Go ahead', REWRITE: 'Rewrite it', ESCALATE: 'Ask a human', BLOCK: 'Refuse' }

interface Example { d: Decision; risk: number; call: string; why: string }

function pick(run: Run | null, d: Decision, why: (s: Step) => string): Example | null {
  if (!run) return null
  const s = run.steps.find((x) => x.guard?.decision === d)
  if (!s || !s.guard) return null
  return { d, risk: s.guard.risk ?? 0, call: plainCall(s, nameLookup(run.map)), why: why(s) }
}

export function Decide({ build }: ScreenProps) {
  const hook = useLoad(() => fetchLiveRun(HOOK, 'scripted:naive', 'storageguard'), []).data
  const ask = useLoad(() => fetchLiveRun('task-66b78890', 'scripted:naive', 'storageguard'), []).data
  const allow = useLoad(() => fetchLiveRun('task-0204356a', 'scripted:reference', 'storageguard'), []).data
  const ex = {
    BLOCK: pick(hook, 'BLOCK', (s) => `risk ${s.guard!.risk!.toFixed(2)}, and it breaks a hard rule`),
    REWRITE: pick(hook, 'REWRITE', () => 'becomes: snapshot it, then the recycle bin'),
    ESCALATE: pick(ask, 'ESCALATE', (s) => `risk ${s.guard!.risk!.toFixed(2)}, a server still uses it`),
    ALLOW: pick(allow, 'ALLOW', (s) => `risk ${s.guard!.risk!.toFixed(2)}, nothing at stake`),
  }
  const shown: Decision[] = build >= 2 ? ['ALLOW', 'REWRITE', 'ESCALATE', 'BLOCK'] : build >= 1 ? ['BLOCK'] : []
  const labelY: Record<Decision, number> = { ALLOW: 466, REWRITE: 546, ESCALATE: 466, BLOCK: 546 }
  const heads = ['Six checks, in about five milliseconds.', 'The checks add up to a risk score.', 'Four possible answers, each with a reason.']
  const supports = [
    'Each step is plain code, so the same call always gets the same answer.',
    'Low risk goes ahead. High risk, or any broken hard rule, is refused.',
    'A rewrite keeps the job moving. A person is asked only when judgement is needed.',
  ]
  const TY = 360
  return (
    <Frame
      head={heads[build]}
      support={supports[build]}
      visual={
        <svg viewBox="0 0 960 660" preserveAspectRatio="xMidYMid meet">
          {/* pipeline */}
          <line x1={SX(0)} y1={110} x2={SX(5)} y2={110} stroke={C.line} strokeWidth={1.5} />
          {STAGES.map((t, i) => (
            <g key={t}>
              <motion.circle cx={SX(i)} cy={110} r={11} initial={{ fill: C.node }} animate={{ fill: C.guard }} transition={{ duration: 0.3, delay: 0.5 + i * 0.5 }} />
              <text x={SX(i)} y={150} textAnchor="middle" className="svg-label">{t}</text>
            </g>
          ))}
          <motion.circle r={5} cy={110} fill="#fff" initial={{ cx: SX(0), opacity: 0 }} animate={{ cx: SX(5), opacity: [0, 1, 1, 0] }} transition={{ duration: 2.6, delay: 0.5, ease: 'linear' }} />

          {/* risk scale */}
          <motion.g initial={false} animate={{ opacity: build >= 1 ? 1 : 0 }} transition={{ duration: 0.6 }}>
            <line x1={RX(0)} y1={TY} x2={RX(0.15)} y2={TY} stroke={C.safe} strokeWidth={6} strokeLinecap="round" />
            <line x1={RX(0.15)} y1={TY} x2={RX(0.55)} y2={TY} stroke={C.ink3} strokeWidth={6} />
            <line x1={RX(0.55)} y1={TY} x2={RX(1)} y2={TY} stroke={C.harm} strokeWidth={6} strokeLinecap="round" />
            <text x={RX(0.075)} y={TY - 34} textAnchor="middle" className="svg-label">Go ahead</text>
            <text x={RX(0.35)} y={TY - 34} textAnchor="middle" className="svg-label">Rewrite, or ask a human</text>
            <text x={RX(0.775)} y={TY - 34} textAnchor="middle" className="svg-label">Refuse</text>
            {[0, 0.15, 0.55, 1].map((v) => <text key={v} x={RX(v)} y={TY + 30} textAnchor="middle" className="svg-sub">{v}</text>)}
            <text x={RX(0)} y={TY - 70} className="svg-sub">risk score</text>
          </motion.g>
          {shown.map((d, i) => {
            const e = ex[d]
            if (!e) return null
            const x = RX(e.risk)
            const ly = labelY[d]
            const delay = build >= 2 && d !== 'BLOCK' ? 0.2 + i * 0.45 : 0.4
            const anchor = d === 'ALLOW' || d === 'ESCALATE' ? 'start' : 'middle'
            return (
              <g key={d}>
                <motion.circle cx={x} r={11} fill={DCOL[d]} initial={{ cy: TY - 200, opacity: 0 }} animate={{ cy: TY, opacity: 1 }}
                  transition={{ cy: { type: 'spring', stiffness: 120, damping: 9, delay }, opacity: { duration: 0.2, delay } }} />
                <motion.g initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: delay + 0.5, duration: 0.4 }}>
                  <text x={anchor === 'start' ? x - 6 : x} y={ly} textAnchor={anchor} className="svg-label strong" style={{ fill: DCOL[d] }}>{DWORD[d]}</text>
                  <text x={anchor === 'start' ? x - 6 : x} y={ly + 21} textAnchor={anchor} className="svg-label" style={{ fontSize: 14 }}>{e.call}</text>
                  <text x={anchor === 'start' ? x - 6 : x} y={ly + 40} textAnchor={anchor} className="svg-sub">{e.why}</text>
                </motion.g>
              </g>
            )
          })}
        </svg>
      }
    />
  )
}

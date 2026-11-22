// Chapter 1: the problem.
import { AnimatePresence, motion } from 'framer-motion'
import { fetchRecorded, fetchResults, fetchLiveRun, useLoad } from '../../lib/api'
import { C, Graph, Layer, Ripple, hopsFrom, type GEdge, type GNode, type NState } from '../Graph'
import { HOOK, HOOK_CHAIN, HOOK_POS, HOOK_TARGETS, hookEdges, hookNodes, onChain } from '../hook'
import { Frame, Problem, Reveal, range, type ScreenProps } from '../ui'

const jitter = (id: string) => (id.split('').reduce((a, c) => a + c.charCodeAt(0), 0) % 10) / 10

function Sites({ on }: { on: boolean }) {
  return (
    <motion.g initial={false} animate={{ opacity: on ? 1 : 0 }} transition={{ duration: 0.6 }}>
      <text x={40} y={34} className="svg-site">Main data centre</text>
      <text x={740} y={34} className="svg-site">Second data centre</text>
      <line x1={700} y1={20} x2={700} y2={640} stroke={C.line} strokeDasharray="2 6" />
    </motion.g>
  )
}

/** Every number on the opening screens comes from results.json. */
export function noGuardRange(res: any) {
  const main = res.main_table.filter((r: any) => r.config === 'B0 No guard').map((r: any) => r.uar)
  const scale = res.scale.map((r: any) => r.B0_uar)
  return range([...main, ...scale])
}

// ---------------------------------------------------------------- 1. the ticket
export function Ticket({ build }: ScreenProps) {
  const { data: run, error } = useLoad(() => fetchRecorded('llama3.1-8b', 'noguard', HOOK, 0), [])
  const { data: res } = useLoad(fetchResults, [])
  if (error) return <Frame head="An AI agent got a routine ticket."><Problem msg={error} /></Frame>
  const map = run?.map
  const facts = (id: string) => map?.nodes.find((n) => n.id === id)?.facts || {}
  const name = (id: string) => map?.nodes.find((n) => n.id === id)?.name || id
  const inRow = build < 2
  const ROW: Record<string, number> = { 'vol-5': 190, 'vol-22': 480, 'vol-21': 770 }
  const DELETE_AT: Record<string, number> = { 'vol-5': 0.5, 'vol-22': 1.3, 'vol-21': 2.1 }

  const state = (id: string): NState => {
    if (HOOK_TARGETS.includes(id)) return build >= 1 ? 'gone' : 'idle'
    if (inRow) return 'hidden'
    if (id === 'rl-1') return 'gone'
    if (id === 'vol-3' || id === 'app-2') return 'hurt'
    return 'idle'
  }
  const nodes = hookNodes(map, state, {
    delay: (id) => (build === 1 ? DELETE_AT[id] ?? 0 : build >= 2 ? (HOOK_TARGETS.includes(id) ? 0 : id in HOOK_CHAIN ? 0.5 + HOOK_CHAIN[id] * 0.45 : 0.15 + jitter(id) * 0.5) : 0),
    place: (id) => {
      if (!HOOK_TARGETS.includes(id)) return {}
      const f = facts(id)
      if (inRow) return { x: ROW[id], y: 300, scale: 2.2, lp: 'b', label: name(id), sub: `idle ${Math.round(f.idle_days)} days, no servers` }
      return { scale: 1 }
    },
  })
  const edges = inRow ? [] : hookEdges(map, (a, b) => (onChain(a, b) ? (a === 'vol-5' || b === 'vol-5' ? 'gone' : 'harm') : 'idle'), (a, b) => (onChain(a, b) ? 0.9 + Math.max(HOOK_CHAIN[a] ?? 0, HOOK_CHAIN[b] ?? 0) * 0.4 : 0.3))

  const heads = [
    'An AI agent got a routine ticket.',
    'It deleted all three volumes in seconds.',
    'One was the live copy of a critical database.',
    'One was the live copy of a critical database.',
  ]
  const supports = [
    'Clean up three storage volumes that look unused.',
    'Then it reported success.',
    'arch-fad9 kept a second copy of ora-auth’s data in another data centre. The ticket never said so.',
    'arch-fad9 kept a second copy of ora-auth’s data in another data centre. The ticket never said so.',
  ]
  const ticketText = run?.task.instruction.split('\n\n').pop()
  const summary = run?.steps.find((s) => s.type === 'finish')?.args.summary as string | undefined
  return (
    <Frame
      head={heads[build]}
      support={supports[build]}
      visual={
        <Graph nodes={nodes} edges={edges} under={<Sites on={!inRow} />}>
          {build >= 2 && <Ripple x={HOOK_POS['vol-5'].x} y={HOOK_POS['vol-5'].y} delay={0.6} max={760} />}
        </Graph>
      }
    >
      <AnimatePresence mode="wait" initial={false}>
        {build < 3 ? (
          <motion.div key="ticket" exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.3 }}>
            <div className="d-ticket">
              <div className="from">Ticket {HOOK.replace('task-', '#')}</div>
              {ticketText}
            </div>
            <Reveal on={build >= 1} delay={build === 1 ? 2.7 : 0}>
              <div className="d-reply">Agent: <b>“{summary}”</b></div>
            </Reveal>
          </motion.div>
        ) : (
          <motion.div key="num" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }}>
            <div className="d-big harm-ink">{res ? noGuardRange(res) : '…'}%</div>
            <div className="d-big-label">of test tickets went wrong when an AI agent worked with no guard. Seven models, from 7 to 72 billion parameters.</div>
          </motion.div>
        )}
      </AnimatePresence>
    </Frame>
  )
}

// ---------------------------------------------------------------- 2. everything is linked
const T: (GNode & { b: number })[] = [
  { id: 'app', kind: 'app', x: 300, y: 96, lp: 'r', label: 'payroll', sub: 'tier-1 app', b: 0 },
  { id: 'h1', kind: 'host', x: 200, y: 200, lp: 'l', label: 'servers', b: 0 },
  { id: 'h2', kind: 'host', x: 400, y: 200, b: 0 },
  { id: 'vol', kind: 'volume', x: 300, y: 330, lp: 'l', label: 'payroll-db', sub: 'volume', b: 0, scale: 1.4 },
  { id: 'sn1', kind: 'snapshot', x: 200, y: 455, b: 1 },
  { id: 'sn2', kind: 'snapshot', x: 300, y: 475, b: 1 },
  { id: 'sn3', kind: 'snapshot', x: 400, y: 455, lp: 'r', label: 'snapshots', b: 1 },
  { id: 'cl', kind: 'clone', x: 300, y: 590, lp: 'r', label: 'payroll-qa', sub: 'clone for testing', b: 2 },
  { id: 'rl', kind: 'replication_link', x: 560, y: 330, b: 3 },
  { id: 'rep', kind: 'volume', x: 820, y: 330, lp: 'b', label: 'payroll-db-dr', sub: 'replica', b: 3 },
  { id: 'bk', kind: 'backup', x: 820, y: 560, lp: 'b', label: 'nightly backup', sub: 'offsite', b: 3 },
]
const TE = [
  ['vol', 'app'], ['vol', 'h1'], ['vol', 'h2'], ['vol', 'sn1'], ['vol', 'sn2'], ['vol', 'sn3'],
  ['sn2', 'cl'], ['vol', 'rl'], ['rl', 'rep'], ['bk', 'vol'],
]
const DESTROYED = new Set(['sn1', 'sn2', 'sn3', 'cl', 'rl', 'rep'])

export function Links({ build }: ScreenProps) {
  const blast = build >= 4
  const hops = hopsFrom('vol', TE.map(([a, b]) => ({ a, b })))
  const nodes: GNode[] = T.map((n) => {
    let state: NState = n.b > build ? 'hidden' : n.b === build && !blast ? 'focus' : 'idle'
    if (blast) state = n.id === 'vol' ? 'gone' : DESTROYED.has(n.id) ? 'gone' : 'hurt'
    const delay = blast ? (hops.get(n.id) ?? 0) * 0.55 : n.b === build ? 0.15 : 0
    return { ...n, state, delay, label: n.label, sub: n.sub }
  })
  const edges: GEdge[] = TE.map(([a, b]) => ({ a, b, state: blast ? 'harm' : 'idle', delay: blast ? Math.max(hops.get(a)!, hops.get(b)!) * 0.55 - 0.4 : 0.1 }))
  const heads = [
    'An app keeps its data on a volume.',
    'Snapshots are photos of that volume.',
    'A clone is a working copy of a snapshot.',
    'Replicas and backups keep copies elsewhere.',
    'Delete one volume, and the damage travels.',
  ]
  const supports = [
    'A slice of a big shared storage array. Servers reach it through a mapping.',
    'Taken every day, so you can roll back after a mistake.',
    'A test team can use real data without touching it.',
    'If the main data centre fails, the business switches to the replica.',
    'Snapshots, the clone and the replica go with it. The servers and the app lose their data. We call this the blast radius.',
  ]
  return (
    <Frame
      head={heads[build]}
      support={supports[build]}
      visual={
        <Graph nodes={nodes} edges={edges} under={<Sites on={build >= 3} />}>
          {blast && <Ripple x={300} y={330} delay={0.1} max={700} />}
        </Graph>
      }
    />
  )
}

// ---------------------------------------------------------------- 3. what today's checks look at
export function Lens({ build }: ScreenProps) {
  const { data: run } = useLoad(() => fetchLiveRun(HOOK, 'scripted:naive', 'noguard'), [])
  const { data: res } = useLoad(fetchResults, [])
  const map = run?.map
  const cx = HOOK_POS['vol-5'].x
  const cy = HOOK_POS['vol-5'].y
  const R = [1400, 64, 260, 1400][build]
  const truth = (id: string): NState => (id === 'vol-5' ? 'target' : id in HOOK_CHAIN ? 'hurt' : 'idle')
  const bright = hookNodes(map, truth, { delay: (id) => (id in HOOK_CHAIN ? 0.3 + HOOK_CHAIN[id] * 0.3 : 0) })
  const dim = hookNodes(map, () => 'quiet').map((n) => ({ ...n, label: undefined, sub: undefined }))
  const brightEdges = hookEdges(map, (a, b) => (onChain(a, b) ? 'harm' : 'idle'), (a, b) => (onChain(a, b) ? 0.3 + Math.max(HOOK_CHAIN[a] ?? 0, HOOK_CHAIN[b] ?? 0) * 0.3 : 0))
  const dimEdges = hookEdges(map, () => 'quiet')
  const row = (cfg: string) => res?.main_table.filter((r: any) => r.config === cfg).map((r: any) => r.uar) || [0]
  const heads = [
    'Today’s checks look at the command only.',
    'Rules in the prompt only see the request.',
    'An AI checker sees one step further.',
    'We need to see everything the change touches.',
  ]
  const supports = [
    'But the harm here is one, two and three links away from it.',
    'They help only if the AI happens to notice that arch-fad9 is a replica.',
    'Not far enough. The database and its app are two and three links away.',
    'Follow every link from the change, and the danger is plain to see. That is what StorageGuard does.',
  ]
  const lensName = ['', 'what prompt rules see', 'what an AI checker sees', ''][build]
  return (
    <Frame
      head={heads[build]}
      support={supports[build]}
      visual={
        <svg viewBox="0 0 960 660" preserveAspectRatio="xMidYMid meet">
          <defs>
            {/* Inside the lens the map is bright; outside it the dim copy shows. A mask, not a cover rect, so nothing paints over the text. */}
            <clipPath id="lens">
              <motion.circle cx={cx} cy={cy} initial={false} animate={{ r: R }} transition={{ type: 'spring', stiffness: 50, damping: 16 }} />
            </clipPath>
            <mask id="lens-out" maskUnits="userSpaceOnUse" x={-400} y={-400} width={1800} height={1500}>
              <rect x={-400} y={-400} width={1800} height={1500} fill="white" />
              <motion.circle cx={cx} cy={cy} fill="black" initial={false} animate={{ r: R }} transition={{ type: 'spring', stiffness: 50, damping: 16 }} />
            </mask>
          </defs>
          <Sites on />
          <g mask="url(#lens-out)">
            <Layer nodes={dim} edges={dimEdges} />
          </g>
          <g clipPath="url(#lens)">
            <Layer nodes={bright} edges={brightEdges} />
          </g>
          <motion.circle cx={cx} cy={cy} fill="none" strokeWidth={1.5} initial={false}
            animate={{ r: R, opacity: build === 1 || build === 2 ? 1 : 0, stroke: build === 3 ? C.guard : C.ink3 }}
            strokeDasharray="4 6" transition={{ type: 'spring', stiffness: 50, damping: 16 }} />
          <motion.text x={cx} textAnchor="middle" className="svg-label" initial={false}
            animate={{ y: cy - R - 16, opacity: lensName ? 1 : 0 }} transition={{ type: 'spring', stiffness: 50, damping: 16 }}>{lensName}</motion.text>
        </svg>
      }
    >
      <div className="d-cmd mono">delete_volume("arch-fad9", force=True)</div>
      <div className="d-fill" />
      <AnimatePresence mode="wait" initial={false}>
        {(build === 1 || build === 2) && (
          <motion.div key={build} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.45 }}>
            <div className="d-big harm-ink">{range(build === 1 ? row('B1 Prompt rules') : row('B3 LLM judge'))}%</div>
            <div className="d-big-label">{build === 1 ? 'of tickets still went wrong with safety rules written into the prompt.' : 'of tickets still went wrong with a second AI approving each change.'}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </Frame>
  )
}

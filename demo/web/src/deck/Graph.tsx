// The constellation: storage objects as quiet shapes joined by fine links.
// Solid = the real thing. Dashed outline = the guard's copy, or something that is gone.
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationNodeDatum } from 'd3-force'
import { motion } from 'framer-motion'
import type { ReactNode } from 'react'
import type { EstateMapData } from '../lib/types'

export const C = {
  node: '#3f3856',
  node2: '#5a5175',
  line: '#3a3350',
  ink: '#eeeaf4',
  ink2: '#aaa3ba',
  ink3: '#757089',
  guard: '#e052c8',
  harm: '#f2676e',
  safe: '#52d6a4',
  rewrite: '#edb04a',
  ask: '#6aa6f8',
  clear: 'rgba(0,0,0,0)',
}

export type NState = 'hidden' | 'idle' | 'quiet' | 'focus' | 'target' | 'gone' | 'harm' | 'hurt' | 'safe' | 'guard' | 'ghost'
export type EState = 'hidden' | 'idle' | 'quiet' | 'harm' | 'guard' | 'ghost' | 'gone'
export type LabelPos = 'r' | 'l' | 'b' | 't'

export interface GNode {
  id: string
  kind: string
  x: number
  y: number
  label?: string
  sub?: string
  state?: NState
  delay?: number
  lp?: LabelPos
  scale?: number
  title?: string
}
export interface GEdge { a: string; b: string; state?: EState; delay?: number }

const SIZE: Record<string, number> = {
  app: 15, volume: 12, clone: 10, snapshot: 6, backup: 6, host: 7, replication_link: 7,
  consistency_group: 9, pool: 9, disk: 4, zone: 6, array: 10, backup_repository: 9, offsite_vault: 9,
}
export const sizeOf = (kind: string, scale = 1) => (SIZE[kind] ?? 7) * scale

const spring = { type: 'spring' as const, stiffness: 70, damping: 17, mass: 1 }

function look(s: NState) {
  switch (s) {
    case 'quiet': return { fill: C.node, stroke: C.clear, op: 0.28, dash: '0' }
    case 'focus': return { fill: C.ink2, stroke: C.clear, op: 1, dash: '0' }
    case 'target': return { fill: C.node2, stroke: C.ink, op: 1, dash: '0' }
    case 'gone': return { fill: C.clear, stroke: C.harm, op: 0.9, dash: '3 3' }
    case 'harm': return { fill: C.harm, stroke: C.harm, op: 1, dash: '0' }
    case 'hurt': return { fill: 'rgba(242,103,110,0.25)', stroke: C.harm, op: 1, dash: '0' }
    case 'safe': return { fill: C.safe, stroke: C.safe, op: 1, dash: '0' }
    case 'guard': return { fill: C.node2, stroke: C.guard, op: 1, dash: '0' }
    case 'ghost': return { fill: C.clear, stroke: C.ink3, op: 0.9, dash: '3 3' }
    case 'hidden': return { fill: C.node, stroke: C.clear, op: 0, dash: '0' }
    default: return { fill: C.node, stroke: C.clear, op: 1, dash: '0' }
  }
}

function Shape({ kind, s, state, delay }: { kind: string; s: number; state: NState; delay: number }) {
  const l = look(state)
  const common = {
    initial: false as const,
    animate: { fill: l.fill, stroke: l.stroke },
    transition: { duration: 0.5, delay },
    strokeWidth: state === 'guard' ? 2.4 : 1.6,
    strokeDasharray: l.dash,
    vectorEffect: 'non-scaling-stroke' as const,
  }
  if (kind === 'app' || kind === 'host' || kind === 'array') {
    const r = kind === 'app' ? 7 : 3
    return <motion.rect x={-s} y={-s} width={s * 2} height={s * 2} rx={r} {...common} />
  }
  if (kind === 'replication_link') return <motion.rect x={-s * 0.75} y={-s * 0.75} width={s * 1.5} height={s * 1.5} rx={2} transform="rotate(45)" {...common} />
  if (kind === 'consistency_group') return <motion.circle r={s} {...common} animate={{ fill: C.clear, stroke: state === 'idle' || state === 'quiet' ? C.node2 : l.stroke }} strokeWidth={2} />
  return <motion.circle r={s} {...common} />
}

function Label({ n, s }: { n: GNode; s: number }) {
  if (!n.label) return null
  const lp = n.lp ?? 'b'
  const strong = n.state === 'target' || n.state === 'focus' || n.state === 'harm' || n.state === 'guard'
  const g = s + 11
  const pos = lp === 'r' ? { x: g, y: 5, a: 'start' } : lp === 'l' ? { x: -g, y: 5, a: 'end' } : lp === 't' ? { x: 0, y: -g - (n.sub ? 16 : 0), a: 'middle' } : { x: 0, y: g + 10, a: 'middle' }
  return (
    <motion.g initial={false} animate={{ x: pos.x, y: pos.y }} transition={spring}>
      <text textAnchor={pos.a as 'start'} className={`svg-label${strong ? ' strong' : ''}`}>{n.label}</text>
      {n.sub && <text y={19} textAnchor={pos.a as 'start'} className="svg-sub">{n.sub}</text>}
    </motion.g>
  )
}

export function Node({ n }: { n: GNode }) {
  const state = n.state ?? 'idle'
  const base = SIZE[n.kind] ?? 7
  const k = n.scale ?? 1
  const s = base * k
  const l = look(state)
  const delay = n.delay ?? 0
  return (
    <motion.g
      initial={{ x: n.x, y: n.y, opacity: 0 }}
      animate={{ x: n.x, y: n.y, opacity: l.op }}
      transition={{ ...spring, opacity: { duration: 0.5, delay } }}
    >
      {n.title && <title>{n.title}</title>}
      {(state === 'gone' || state === 'harm') && (
        <motion.circle key={state} fill="none" stroke={C.harm} strokeWidth={1.5}
          initial={{ r: s, opacity: 0 }} animate={{ r: [s, s * 3.4], opacity: [0.8, 0] }} transition={{ duration: 1.1, delay, ease: 'easeOut' }} />
      )}
      {state === 'guard' && (
        <motion.circle fill="none" stroke={C.guard} strokeWidth={1}
          initial={{ r: s + 7, opacity: 0 }} animate={{ r: s + 7, opacity: [0, 0.6, 0.25] }} transition={{ duration: 1.2, delay }} />
      )}
      <motion.g initial={{ scale: 0.4 * k }} animate={{ scale: (state === 'gone' ? 0.86 : 1) * k }} transition={{ ...spring, delay }}>
        <Shape kind={n.kind} s={base} state={state} delay={delay} />
      </motion.g>
      <motion.g initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.4, delay: delay + 0.2 }}>
        <Label n={n} s={s} />
      </motion.g>
    </motion.g>
  )
}

export function Edge({ e, a, b }: { e: GEdge; a: GNode; b: GNode }) {
  const state = e.state ?? 'idle'
  const hidden = state === 'hidden' || a.state === 'hidden' || b.state === 'hidden'
  const quiet = state === 'quiet' || a.state === 'quiet' || b.state === 'quiet'
  const delay = e.delay ?? 0
  const coords = { x1: a.x, y1: a.y, x2: b.x, y2: b.y }
  const base = state === 'ghost' ? C.ink3 : state === 'gone' ? C.harm : C.line
  return (
    <g>
      <motion.line
        initial={{ ...coords, opacity: 0 }}
        animate={{ ...coords, opacity: hidden ? 0 : quiet ? 0.25 : state === 'gone' ? 0.6 : 1 }}
        transition={{ ...spring, opacity: { duration: 0.5, delay } }}
        stroke={base} strokeWidth={1.4} strokeDasharray={state === 'ghost' || state === 'gone' ? '3 4' : undefined}
      />
      {(state === 'harm' || state === 'guard') && !hidden && (
        <motion.line
          key={state}
          {...coords}
          stroke={state === 'harm' ? C.harm : C.guard} strokeWidth={2.2} strokeLinecap="round"
          initial={{ pathLength: 0, opacity: 1 } as any} animate={{ pathLength: 1, opacity: 1 } as any}
          transition={{ duration: 0.55, delay, ease: 'easeInOut' }}
        />
      )}
    </g>
  )
}

export function Ripple({ x, y, color = C.harm, delay = 0, max = 520 }: { x: number; y: number; color?: string; delay?: number; max?: number }) {
  return (
    <motion.circle cx={x} cy={y} fill="none" stroke={color} strokeWidth={1.5}
      initial={{ r: 0, opacity: 0 }} animate={{ r: max, opacity: [0, 0.5, 0] }} transition={{ duration: 2.2, delay, ease: 'easeOut' }} />
  )
}

export function Layer({ nodes, edges }: { nodes: GNode[]; edges: GEdge[] }) {
  const byId = new Map(nodes.map((n) => [n.id, n]))
  return (
    <g>
      {edges.map((e) => {
        const a = byId.get(e.a)
        const b = byId.get(e.b)
        if (!a || !b) return null
        return <Edge key={`${e.a}-${e.b}`} e={e} a={a} b={b} />
      })}
      {nodes.map((n) => <Node key={n.id} n={n} />)}
    </g>
  )
}

export function Graph({ nodes, edges, width = 960, height = 660, children, under }: { nodes: GNode[]; edges: GEdge[]; width?: number; height?: number; children?: ReactNode; under?: ReactNode }) {
  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet" role="img">
      {under}
      <Layer nodes={nodes} edges={edges} />
      {children}
    </svg>
  )
}

/** Hops from one node across the links (undirected), for timing a ripple. */
export function hopsFrom(origin: string, edges: { a: string; b: string }[]): Map<string, number> {
  const adj = new Map<string, string[]>()
  for (const e of edges) {
    adj.set(e.a, [...(adj.get(e.a) || []), e.b])
    adj.set(e.b, [...(adj.get(e.b) || []), e.a])
  }
  const out = new Map([[origin, 0]])
  const q = [origin]
  while (q.length) {
    const cur = q.shift()!
    for (const nb of adj.get(cur) || []) if (!out.has(nb)) { out.set(nb, out.get(cur)! + 1); q.push(nb) }
  }
  return out
}

// ---------------------------------------------------------------- automatic layout for any estate map
const ROW: Record<string, number> = {
  app: 0.1, host: 0.27, zone: 0.27, consistency_group: 0.4, volume: 0.52, clone: 0.6, replication_link: 0.52,
  snapshot: 0.74, backup: 0.82, backup_repository: 0.9, offsite_vault: 0.92, pool: 0.9, disk: 0.96, array: 0.96,
}

interface SimNode extends SimulationNodeDatum { id: string; kind: string; tx: number; ty: number; r: number }

export function autoLayout(map: EstateMapData, W: number, H: number): Map<string, { x: number; y: number }> {
  const role = new Map(map.sites.map((s) => [s.id, s.role]))
  const hasDr = map.nodes.some((n) => n.site && role.get(n.site) === 'dr')
  const colX = (site: string | null, kind: string) => {
    if (site === 'offsite' || kind === 'offsite_vault' || kind === 'backup_repository') return hasDr ? 0.86 : 0.82
    if (site && role.get(site) === 'dr') return 0.82
    if (kind === 'replication_link') return hasDr ? 0.64 : 0.5
    return hasDr ? 0.34 : 0.44
  }
  const nodes: SimNode[] = map.nodes.map((n, i) => {
    const tx = colX(n.site, n.kind) * W
    const ty = (ROW[n.kind] ?? 0.6) * H
    return { id: n.id, kind: n.kind, tx, ty, r: sizeOf(n.kind) + (n.kind === 'volume' || n.kind === 'app' || n.kind === 'clone' ? 46 : 18), x: tx + ((i * 37) % 60) - 30, y: ty + ((i * 53) % 40) - 20 }
  })
  const ids = new Set(nodes.map((n) => n.id))
  const links = map.edges.filter((e) => ids.has(e.from) && ids.has(e.to)).map((e) => ({ source: e.from, target: e.to }))
  const sim = forceSimulation(nodes)
    .force('link', forceLink<SimNode, { source: string; target: string }>(links).id((d) => d.id).distance(70).strength(0.3))
    .force('charge', forceManyBody().strength(-220))
    .force('x', forceX<SimNode>((d) => d.tx).strength(0.12))
    .force('y', forceY<SimNode>((d) => d.ty).strength(0.35))
    .force('collide', forceCollide<SimNode>((d) => d.r))
    .stop()
  for (let i = 0; i < 360; i++) sim.tick()
  // fit into the box
  const pad = 56
  const xs = nodes.map((n) => n.x!)
  const ys = nodes.map((n) => n.y!)
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
  const k = Math.min((W - pad * 2) / Math.max(1, x1 - x0), (H - pad * 2) / Math.max(1, y1 - y0), 2.2)
  const cx = (x0 + x1) / 2
  const cy = (y0 + y1) / 2
  return new Map(nodes.map((n) => [n.id, { x: W / 2 + (n.x! - cx) * k, y: H / 2 + (n.y! - cy) * k }]))
}

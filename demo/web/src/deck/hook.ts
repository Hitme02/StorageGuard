// The ticket the talk opens with (task-854ce268), laid out by hand so the story reads at a glance.
import type { EstateMapData } from '../lib/types'
import type { EState, GEdge, GNode, LabelPos, NState } from './Graph'

export const HOOK = 'task-854ce268'

type P = { x: number; y: number; lp?: LabelPos; label?: string; sub?: string }

export const HOOK_POS: Record<string, P> = {
  'app-2': { x: 260, y: 100, lp: 'r', label: 'ora-auth', sub: 'tier-1 app' },
  'host-4': { x: 150, y: 235 },
  'host-5': { x: 370, y: 235 },
  'vol-3': { x: 260, y: 370, lp: 'l', label: 'ora-auth-data-01', sub: 'its database' },
  'snap-21': { x: 170, y: 505 },
  'snap-23': { x: 260, y: 535 },
  'bkp-5': { x: 350, y: 505 },
  'rl-1': { x: 560, y: 370 },
  'vol-5': { x: 820, y: 370, lp: 'b', label: 'arch-fad9', sub: 'its live copy' },
  'vol-22': { x: 470, y: 595, lp: 'b', label: 'scratch-etl-03' },
  'snap-58': { x: 470, y: 525 },
  'vol-21': { x: 610, y: 595, lp: 'b', label: 'scratch-etl-02' },
  'snap-59': { x: 610, y: 525 },
}

/** The three volumes the ticket names, in the order the ticket lists them. */
export const HOOK_TARGETS = ['vol-5', 'vol-22', 'vol-21']

/** Nodes of the hook map that the hand layout places. */
export function hookNodes(map: EstateMapData | undefined, state: (id: string) => NState, opts: { delay?: (id: string) => number; place?: (id: string) => Partial<P> & { scale?: number } } = {}): GNode[] {
  if (!map) return []
  return map.nodes
    .filter((n) => HOOK_POS[n.id])
    .map((n) => {
      const p = { ...HOOK_POS[n.id], ...(opts.place?.(n.id) || {}) }
      return { id: n.id, kind: n.kind, x: p.x, y: p.y, lp: p.lp, label: p.label, sub: p.sub, scale: (p as { scale?: number }).scale, state: state(n.id), delay: opts.delay?.(n.id) ?? 0, title: n.name }
    })
}

export function hookEdges(map: EstateMapData | undefined, state: (a: string, b: string) => EState = () => 'idle', delay?: (a: string, b: string) => number): GEdge[] {
  if (!map) return []
  return map.edges
    .filter((e) => HOOK_POS[e.from] && HOOK_POS[e.to])
    .map((e) => ({ a: e.from, b: e.to, state: state(e.from, e.to), delay: delay?.(e.from, e.to) ?? 0 }))
}

/** The chain that breaks when arch-fad9 goes: the replication link, the database, the app. */
export const HOOK_CHAIN: Record<string, number> = { 'rl-1': 1, 'vol-3': 2, 'app-2': 3 }
export const onChain = (a: string, b: string) => {
  const s = new Set([a, b])
  return (s.has('vol-5') && s.has('rl-1')) || (s.has('rl-1') && s.has('vol-3')) || (s.has('vol-3') && s.has('app-2'))
}

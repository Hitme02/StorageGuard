// The storage map: one picture of the estate that grows through the talk.
// Sites are columns (main, DR, offsite); kinds are rows (apps, servers, volumes, snapshots, backups).
import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { KIND } from '../lib/plain'
import type { EstateMapData, Impact, MapNode } from '../lib/types'

export interface MapHighlight {
  targets?: string[]
  impacted?: Impact[]
  removed?: string[]
  changed?: string[]
  harmed?: string[]
  added?: string[]
}

interface Props {
  data: EstateMapData
  width?: number
  height?: number
  visible?: Set<string> | null
  highlight?: MapHighlight
  rippleKey?: string | number
  onSelect?: (n: MapNode | null) => void
  selected?: string | null
  labels?: 'key' | 'all'
  dimOthers?: boolean
  copyMark?: boolean
}

// Rows by kind, top to bottom. Only rows that have nodes get space; offsite backups sit in a band at the bottom.
const ROW_OF: Record<string, string> = {
  app: 'app', cluster: 'app', host: 'host', zone: 'host', consistency_group: 'cg',
  volume: 'volume', clone: 'volume', backup_repository: 'volume', replication_link: 'volume',
  snapshot: 'snapshot', backup: 'backup', pool: 'backup', disk: 'disk', array: 'disk',
}
const ROW_ORDER = ['app', 'host', 'cg', 'volume', 'snapshot', 'backup', 'disk']
const RADIUS: Record<string, number> = {
  volume: 19, clone: 19, backup_repository: 19, app: 16, host: 15, snapshot: 9, backup: 9, replication_link: 9,
  consistency_group: 15, pool: 22, disk: 6, zone: 9, offsite_vault: 22, cluster: 14, array: 14,
}
const LABELLED = new Set(['volume', 'clone', 'backup_repository', 'app', 'host', 'pool', 'consistency_group'])

type LNode = MapNode & { r: number; region: string; x: number; y: number; row: string; lv?: number }
export interface Region { id: string; label: string; x0: number; x1: number; y0?: number; y1?: number }

function siteRole(data: EstateMapData, site: string | null): 'primary' | 'dr' {
  const s = data.sites.find((x) => x.id === site)
  return s?.role === 'dr' ? 'dr' : 'primary'
}

const labelW = (n: MapNode) => Math.min(20, (n.name || n.id).length) * 6.7

function layout(data: EstateMapData, W: number, H: number, labelsAll: boolean): { nodes: LNode[]; regions: Region[]; bandY: number | null } {
  const byId = new Map(data.nodes.map((n) => [n.id, n]))
  const out = (id: string, type?: string) => data.edges.filter((e) => e.from === id && (!type || e.type === type)).map((e) => e.to)
  const inn = (id: string, type?: string) => data.edges.filter((e) => e.to === id && (!type || e.type === type)).map((e) => e.from)
  const isVol = (n?: MapNode) => !!n && ['volume', 'clone', 'backup_repository'].includes(n.kind)
  const offsite = (n: MapNode) => n.kind === 'offsite_vault' || (n.kind === 'backup' && (n.site === 'offsite' || n.facts?.repository === 'offsite-vault'))

  // site of every node (servers and apps borrow it from their volumes)
  const siteOf = (n: MapNode): 'primary' | 'dr' => {
    if (n.site && n.site !== 'offsite') return siteRole(data, n.site)
    for (const e of data.edges) {
      const o = e.from === n.id ? byId.get(e.to) : e.to === n.id ? byId.get(e.from) : undefined
      if (o?.site && o.site !== 'offsite') return siteRole(data, o.site)
    }
    return 'primary'
  }
  const hasDR = data.nodes.some((n) => !offsite(n) && n.kind !== 'replication_link' && siteOf(n) === 'dr')
  const hasBand = data.nodes.some(offsite)
  const pad = 12
  const gap = hasDR ? 72 : 0
  const volCount = (site: string) => data.nodes.filter((n) => isVol(n) && siteOf(n) === site).length
  const pw = Math.max(2, volCount('primary') + 1)
  const dw = hasDR ? Math.max(1.4, volCount('dr') + 0.6) : 0
  const avail = W - 2 * pad - gap
  let drW = hasDR ? Math.max(185, (avail * dw) / (pw + dw)) : 0
  drW = Math.min(drW, avail * 0.45)
  const pX1 = pad + avail - drW
  const prim = data.sites.find((s) => s.role === 'primary')
  const dr = data.sites.find((s) => s.role === 'dr')
  const regions: Region[] = [{ id: 'primary', label: `Main site${prim ? ` (${prim.name})` : ''}`, x0: pad, x1: pX1 }]
  if (hasDR) {
    const long = `Disaster-recovery site${dr ? ` (${dr.name})` : ''}`
    const x0 = pX1 + gap
    regions.push({ id: 'dr', label: long.length * 6.9 + 24 < W - pad - x0 ? long : `DR site${dr ? ` (${dr.name})` : ''}`, x0, x1: W - pad })
  }
  const reg = Object.fromEntries(regions.map((r) => [r.id, r])) as Record<string, Region>
  const present = ROW_ORDER.filter((r) => data.nodes.some((n) => !offsite(n) && ROW_OF[n.kind] === r))
  const bandY = hasBand ? H - 30 : null
  const top = 58
  const bottom = hasBand ? H - 100 : H - 40
  const stepY = present.length > 1 ? Math.min(92, (bottom - top) / (present.length - 1)) : 0
  const used = stepY * Math.max(0, present.length - 1)
  const y0 = top + Math.max(0, (bottom - top - used) / 2) * 0.6
  const rowY = (row: string) => (row === 'band' ? (bandY as number) : y0 + Math.max(0, present.indexOf(row)) * stepY)

  const nodes: LNode[] = data.nodes.map((n) => {
    const row = offsite(n) ? 'band' : ROW_OF[n.kind] ?? 'snapshot'
    const region = n.kind === 'replication_link' ? 'link' : offsite(n) ? 'band' : siteOf(n)
    return { ...n, r: RADIUS[n.kind] ?? 10, region, row, x: 0, y: rowY(row) }
  })
  const L = new Map(nodes.map((n) => [n.id, n]))

  // 1) volumes: ordered by app tier and name, clones right after the volume they came from
  const parentVol = (n: MapNode): string | undefined => {
    const snap = n.facts?.parent_snapshot || out(n.id, 'clone_of')[0]
    const sn = snap ? byId.get(snap) : undefined
    return sn?.facts?.volume || (snap ? inn(snap, 'has_snapshot')[0] : undefined)
  }
  for (const site of ['primary', 'dr']) {
    const R = reg[site]
    if (!R) continue
    const vols = nodes.filter((n) => isVol(n) && n.region === site)
    const key = (n: LNode): string => {
      if (n.kind === 'clone') {
        const p = parentVol(n)
        const pn = p ? L.get(p) : undefined
        if (pn && pn !== n) return key(pn) + '~' + n.name
      }
      return `${n.tier ?? 9}|${n.facts?.app ?? 'zz'}|${n.name}`
    }
    vols.sort((a, b) => key(a).localeCompare(key(b)))
    const weight = (v: LNode) => 1 + 0.35 * Math.max(0, inn(v.id, 'backs_up').length + out(v.id, 'has_snapshot').length - 1)
    const total = vols.reduce((a, v) => a + weight(v), 0) || 1
    let acc = 0
    for (const v of vols) {
      const w = weight(v)
      v.x = R.x0 + 24 + ((acc + w / 2) / total) * (R.x1 - R.x0 - 48)
      acc += w
    }
  }
  // 2) everything else sits near what it belongs to
  const mean = (ids: string[]) => {
    const xs = ids.map((i) => L.get(i)).filter((n): n is LNode => !!n && isVol(n)).map((n) => n.x)
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null
  }
  const placed = new Set(nodes.filter((n) => isVol(n)).map((n) => n.id))
  const anchorX = (n: LNode): number | null => {
    const f = n.facts || {}
    switch (n.kind) {
      case 'snapshot': return mean([f.volume, ...inn(n.id, 'has_snapshot')].filter(Boolean))
      case 'backup': return mean([f.source, ...out(n.id, 'backs_up')].filter(Boolean))
      case 'app': return mean([...inn(n.id, 'owned_by_app'), ...inn(n.id, 'owned_by_app').flatMap((c) => inn(c, 'member_of_cg'))])
      case 'host': return mean(inn(n.id, 'mapped_to'))
      case 'consistency_group': return mean(inn(n.id, 'member_of_cg'))
      case 'pool': return mean(inn(n.id, 'in_pool'))
      case 'replication_link': {
        const s = inn(n.id, 'replicates_to')[0] || f.source
        const t = out(n.id, 'replicates_to')[0] || f.target
        const sn = s ? L.get(s) : undefined
        const tn = t ? L.get(t) : undefined
        if (sn && tn && sn.region !== tn.region && hasDR) return pX1 + gap / 2
        return mean([s, t].filter(Boolean))
      }
      default: return null
    }
  }
  for (const n of nodes) {
    if (placed.has(n.id)) continue
    const a = anchorX(n)
    if (a !== null) { n.x = a; placed.add(n.id) }
  }
  // disks follow their pool; zones follow their servers
  for (const n of nodes) {
    if (placed.has(n.id)) continue
    const nb = [...out(n.id), ...inn(n.id)].map((i) => L.get(i)).filter((m): m is LNode => !!m && placed.has(m.id))
    if (nb.length) { n.x = nb.reduce((a, m) => a + m.x, 0) / nb.length; placed.add(n.id) }
  }
  const rest = nodes.filter((n) => !placed.has(n.id))
  rest.forEach((n, i) => {
    const R = n.region === 'dr' ? reg.dr : reg.primary
    n.x = R.x0 + 30 + ((i + 0.5) / rest.length) * (R.x1 - R.x0 - 60)
  })
  // vault icon at the left end of the band
  nodes.filter((n) => n.kind === 'offsite_vault').forEach((n) => { n.x = pad + 150 })

  // 3) push overlapping nodes apart within each row and region, then keep them inside it
  const groups = new Map<string, LNode[]>()
  for (const n of nodes) {
    const g = `${n.row}|${n.region === 'link' ? 'link' : n.region}`
    if (!groups.has(g)) groups.set(g, [])
    groups.get(g)!.push(n)
  }
  // links share the volume row with their region's volumes
  for (const [g, list] of groups) {
    if (!g.endsWith('|link')) continue
    groups.delete(g)
    for (const n of list) {
      const into = n.x > pX1 && hasDR ? null : 'volume|primary'
      if (into && groups.has(into)) groups.get(into)!.push(n)
      else groups.set(`link-${n.id}`, [n])
    }
  }
  const showLabel = (n: LNode) => labelsAll || LABELLED.has(n.kind) || data.targets.includes(n.id)
  const minGap = (a: LNode, b: LNode) => {
    const la = showLabel(a) ? labelW(a) / 2 : 0
    const lb = showLabel(b) ? labelW(b) / 2 : 0
    return Math.max(a.r + b.r + 10, la + lb + 12, a.r + lb + 8, la + b.r + 8)
  }
  for (const [g, list] of groups) {
    list.sort((a, b) => a.x - b.x)
    const R = g.includes('|dr') ? reg.dr : g.includes('|band') ? { x0: pad + 200, x1: W - pad } as Region : g.startsWith('link-') ? { x0: pX1, x1: pX1 + gap } as Region : reg.primary
    const want = list.map((n) => n.x)
    for (let i = 1; i < list.length; i++) list[i].x = Math.max(list[i].x, list[i - 1].x + minGap(list[i - 1], list[i]))
    const shift = list.reduce((a, n, i) => a + (n.x - want[i]), 0) / list.length
    list.forEach((n) => (n.x -= shift))
    const lo = R.x0 + 14 + (list[0] ? Math.max(list[0].r, showLabel(list[0]) ? labelW(list[0]) / 2 : 0) : 0)
    const last = list[list.length - 1]
    const hi = R.x1 - 14 - (last ? Math.max(last.r, showLabel(last) ? labelW(last) / 2 : 0) : 0)
    if (!list.length || g.startsWith('link-')) continue
    const span = last.x - list[0].x
    if (span > hi - lo && span > 0) {
      const k = (hi - lo) / span
      const x0 = list[0].x
      list.forEach((n) => (n.x = lo + (n.x - x0) * k))
    } else if (list[0].x < lo) {
      const d = lo - list[0].x
      list.forEach((n) => (n.x += d))
    } else if (last.x > hi) {
      const d = last.x - hi
      list.forEach((n) => (n.x -= d))
    }
  }
  // 4) labels that would still collide drop to a second line
  for (const [, list] of groups) {
    const ends = [-1e9, -1e9]
    for (const n of [...list].sort((a, b) => a.x - b.x)) {
      if (!showLabel(n) || n.kind === 'replication_link') continue
      const half = labelW(n) / 2
      const lv = n.x - half >= ends[0] + 6 ? 0 : n.x - half >= ends[1] + 6 ? 1 : ends[0] <= ends[1] ? 0 : 1
      n.lv = lv
      ends[lv] = n.x + half
    }
  }
  return { nodes, regions, bandY }
}

// ---- glyphs ----
function Glyph({ n, cls }: { n: LNode; cls: string }) {
  const f = n.facts || {}
  switch (n.kind) {
    case 'volume':
    case 'clone':
    case 'backup_repository': {
      const replica = f.role === 'replica_target'
      return (
        <g className={cls}>
          <rect x={-19} y={-12} width={38} height={24} rx={4} className="g-body" />
          {n.kind === 'clone' && <path d="M 9 -12 L 19 -2 L 19 -12 Z" fill="var(--ink-3)" />}
          {n.kind === 'backup_repository' && <><line x1={-12} x2={12} y1={-3} y2={-3} className="g-line" /><line x1={-12} x2={12} y1={3} y2={3} className="g-line" /></>}
          {replica && <text x={0} y={4.5} textAnchor="middle" className="g-txt">DR</text>}
          <circle cx={-12} cy={-5} r={2.6} fill={n.tier === 1 ? 'var(--ink)' : n.tier ? 'var(--ink-2)' : 'var(--led-off)'} />
        </g>
      )
    }
    case 'app':
    case 'cluster': {
      const pts = Array.from({ length: 6 }, (_, i) => {
        const a = (Math.PI / 3) * i + Math.PI / 6
        return `${16 * Math.cos(a)},${16 * Math.sin(a)}`
      }).join(' ')
      return (
        <g className={cls}>
          <polygon points={pts} className="g-body" />
          {n.tier && <text x={0} y={5} textAnchor="middle" className="g-tier">{n.tier}</text>}
        </g>
      )
    }
    case 'host':
      return (
        <g className={cls} opacity={f.status === 'offline' ? 0.5 : 1}>
          <rect x={-15} y={-10} width={30} height={20} rx={2} className="g-body" />
          <line x1={-9} x2={9} y1={-3} y2={-3} className="g-line" />
          <line x1={-9} x2={9} y1={3} y2={3} className="g-line" />
        </g>
      )
    case 'snapshot':
      return (
        <g className={cls}>
          <circle r={8} className="g-body" />
          {f.immutable && <rect x={-3.5} y={-3.5} width={7} height={7} rx={1} fill="var(--ink-2)" />}
          {f.scan === 'encrypted' && <circle r={3} fill="var(--block)" />}
        </g>
      )
    case 'backup':
      return (
        <g className={cls}>
          <rect x={-8} y={-8} width={16} height={16} rx={3} className="g-body" />
          {f.immutable && <rect x={-3.5} y={-3.5} width={7} height={7} rx={1} fill="var(--ink-2)" />}
        </g>
      )
    case 'replication_link': {
      const st = f.state
      return (
        <g className={cls}>
          <rect x={-8} y={-8} width={16} height={16} transform="rotate(45)" className="g-body" />
          {st === 'paused' && <><line x1={-2.5} x2={-2.5} y1={-4} y2={4} stroke="var(--ink)" strokeWidth={1.6} /><line x1={2.5} x2={2.5} y1={-4} y2={4} stroke="var(--ink)" strokeWidth={1.6} /></>}
          {st === 'broken' && <><line x1={-4} x2={4} y1={-4} y2={4} stroke="var(--block)" strokeWidth={2} /><line x1={4} x2={-4} y1={-4} y2={4} stroke="var(--block)" strokeWidth={2} /></>}
        </g>
      )
    }
    case 'consistency_group':
      return (
        <g className={cls}>
          <rect x={-15} y={-9} width={30} height={18} rx={9} className="g-body" />
          <text x={0} y={4.5} textAnchor="middle" className="g-txt">CG</text>
        </g>
      )
    case 'pool':
      return (
        <g className={cls}>
          <rect x={-22} y={-9} width={44} height={18} rx={3} className="g-body" />
          {[...Array(5)].map((_, i) => <circle key={i} cx={-14 + i * 7} cy={0} r={2} fill="var(--ink-3)" />)}
        </g>
      )
    case 'offsite_vault':
      return (
        <g className={cls}>
          <rect x={-22} y={-14} width={44} height={28} rx={3} className="g-body" />
          <circle r={6} fill="none" stroke="var(--ink-2)" strokeWidth={1.5} />
        </g>
      )
    default:
      return <circle r={6} className={`g-body ${cls}`} />
  }
}

function factLines(n: MapNode): string[] {
  const f = n.facts || {}
  const out: string[] = []
  if (['volume', 'clone', 'backup_repository'].includes(n.kind)) {
    const role = f.role === 'replica_target' ? 'Disaster-recovery replica' : f.role === 'backup_repository' ? 'Holds backups' : f.role === 'scratch' ? 'Scratch space' : f.role === 'primary' ? 'Primary data' : f.role
    if (role) out.push(role)
    if (f.app) out.push(`App ${f.app}${f.tier ? `, tier ${f.tier}` : ''}`)
    if (f.gb) out.push(`${f.gb.toLocaleString()} GB`)
    if (f.iops_7d === 0) out.push(`No traffic for ${f.idle_days ?? '30+'} days`)
    out.push(f.hosts ? `${f.hosts} server${f.hosts > 1 ? 's' : ''} attached` : 'No servers attached')
    if (f.tags?.length) out.push(`Tags: ${f.tags.join(', ')}`)
    if (f.description) out.push(`Description: “${f.description}”`)
    if (f.in_recycle_bin) out.push('In the recycle bin')
  } else if (n.kind === 'snapshot') {
    if (f.volume_name) out.push(`Photo of ${f.volume_name}`)
    if (f.age_days !== undefined) out.push(`${f.age_days} days old`)
    if (f.immutable) out.push('Locked (immutable)')
    if (f.scan) out.push(`Malware scan: ${f.scan}`)
    if (f.clones) out.push(`${f.clones} clone${f.clones > 1 ? 's' : ''} depend on it`)
  } else if (n.kind === 'backup') {
    out.push(f.repository === 'offsite-vault' ? 'Stored in the offsite vault' : `Stored on ${f.repository}`)
    if (f.age_days !== undefined) out.push(`${f.age_days} days old`)
    if (f.immutable) out.push('Locked (immutable)')
  } else if (n.kind === 'replication_link') {
    out.push(`${f.mode === 'sync' ? 'Synchronous' : 'Asynchronous'}, every ${f.rpo_minutes} min`)
    out.push(`State: ${f.state}`)
  } else if (n.kind === 'app') {
    out.push(`Tier ${f.tier}${f.tenant ? `, ${f.tenant}` : ''}`)
  } else if (n.kind === 'host') {
    out.push(f.status === 'offline' ? 'Offline (retired)' : 'Online')
    if (f.cluster) out.push(`Cluster ${f.cluster}${f.tenant ? `, ${f.tenant}` : ''}`)
  } else if (n.kind === 'pool') {
    out.push(`State: ${f.state}`)
    if (f.utilisation_pct !== undefined) out.push(`${f.utilisation_pct}% full`)
    out.push(`${f.spares ?? 0} spare disk${f.spares === 1 ? '' : 's'}`)
  } else if (n.kind === 'consistency_group') {
    out.push(`${(f.volumes || []).length} volumes kept in step${f.app ? ` for ${f.app}` : ''}`)
  }
  return out
}

/** Straight unless the edge would run through other nodes of the same row; then an arc over the row. */
function edgePath(a: LNode, b: LNode, all: LNode[]): string {
  const sameRow = Math.abs(a.y - b.y) < 8
  if (sameRow) {
    const [l, r] = a.x < b.x ? [a, b] : [b, a]
    const between = all.some((n) => n !== a && n !== b && Math.abs(n.y - a.y) < 8 && n.x > l.x + 4 && n.x < r.x - 4)
    if (between) {
      const bow = 30 + Math.min(26, Math.abs(b.x - a.x) * 0.04)
      return `M${a.x},${a.y - a.r * 0.6} Q${(a.x + b.x) / 2},${a.y - bow * 2} ${b.x},${b.y - b.r * 0.6}`
    }
  }
  return `M${a.x},${a.y} L${b.x},${b.y}`
}

function trunc(s: string, n = 20) {
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}

export function EstateMap({ data, width = 1000, height = 520, visible = null, highlight = {}, rippleKey, onSelect, selected, labels = 'key', dimOthers, copyMark }: Props) {
  const key = data.nodes.map((n) => n.id).join(',')
  const wrap = useRef<HTMLDivElement>(null)
  // lay out in the container's real size (CSS pixels before the stage scale), so text stays at its set size
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)
  useLayoutEffect(() => {
    const el = wrap.current
    if (!el) return
    const measure = () => {
      const w = Math.round(el.clientWidth)
      const h = Math.round(el.clientHeight)
      if (w > 50 && h > 50) setSize((p) => (p && Math.abs(p.w - w) < 2 && Math.abs(p.h - h) < 2 ? p : { w, h }))
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const W = size?.w ?? width
  const H = size?.h ?? height
  const { nodes, regions, bandY } = useMemo(() => layout(data, W, H, labels === 'all'), [key, W, H, labels]) // eslint-disable-line react-hooks/exhaustive-deps
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes])
  const [hover, setHover] = useState<string | null>(null)

  const targets = new Set(highlight.targets || [])
  const imp = new Map<string, Impact>()
  ;(highlight.impacted || []).forEach((i) => {
    if (!targets.has(i.id) || i.hops > 0) imp.set(i.id, i)
  })
  const removed = new Set(highlight.removed || [])
  const changed = new Set(highlight.changed || [])
  const harmed = new Set(highlight.harmed || [])
  const isVis = (id: string) => !visible || visible.has(id)
  const hot = (id: string) => targets.has(id) || imp.has(id) || harmed.has(id) || changed.has(id) || removed.has(id) || id === selected
  const anyHot = targets.size + imp.size + harmed.size > 0

  const hv = hover ? byId.get(hover) : null
  const s = 1

  return (
    <div ref={wrap} style={{ position: 'relative', width: '100%', height: '100%', overflow: 'hidden' }}>
      <svg className="map-svg" viewBox={`0 0 ${W} ${H}`} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', opacity: size ? 1 : 0 }} preserveAspectRatio="xMidYMid meet" role="img" aria-label="Storage map">
        <defs>
          <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--ink-3)" />
          </marker>
        </defs>
        <style>{`
          .g-body { fill: var(--panel-3); stroke: var(--ink-3); stroke-width: 1.3; }
          .g-line { stroke: var(--ink-3); stroke-width: 1.2; }
          .g-txt { font-size: 9.5px; font-weight: 600; fill: var(--ink-2); }
          .g-tier { font-size: 13px; font-weight: 600; fill: var(--ink); font-family: var(--cond); }
          .is-target .g-body { stroke: var(--brand); stroke-width: 2.4; fill: #2a1f2b; }
          .is-destroyed .g-body { stroke: var(--block); stroke-width: 2.2; fill: #2b1a1c; }
          .is-affected .g-body { stroke: #e98a8d; stroke-width: 1.8; }
          .is-removed { opacity: 0.38; }
          .map-node { transition: opacity 0.7s ease; cursor: pointer; }
          .map-edge { transition: opacity 0.7s ease, stroke 0.4s; }
          .ripple { fill: none; stroke-width: 2; transform-box: fill-box; transform-origin: center; animation: pulse-ring 1.2s ease-out both; }
          .ring { fill: none; transition: opacity 0.5s ease; }
          .halo { paint-order: stroke; stroke: #131920; stroke-width: 4px; stroke-linejoin: round; }
        `}</style>
        {regions.map((r) => (
          <g key={r.id}>
            <rect x={r.x0} y={4} width={r.x1 - r.x0} height={(bandY ?? H + 26) - 30} rx={6} fill={r.id === 'primary' ? '#141a20' : '#131920'} stroke="var(--line)" />
            <text x={r.x0 + 12} y={22} style={{ fontSize: 13, fill: 'var(--ink-3)', fontWeight: 500 }}>{r.label}</text>
          </g>
        ))}
        {bandY !== null && (
          <g>
            <rect x={12} y={bandY - 22} width={W - 24} height={44} rx={6} fill="#12171d" stroke="var(--line)" />
            <text x={26} y={bandY + 5} style={{ fontSize: 13, fill: 'var(--ink-3)', fontWeight: 500 }}>Offsite vault</text>
          </g>
        )}
        {copyMark && (
          <text x={W - 20} y={H - 16} textAnchor="end" style={{ fontSize: 30, fill: 'var(--brand)', opacity: 0.35, fontFamily: 'var(--cond)', fontWeight: 600 }}>
            copy for the dry run
          </text>
        )}
        {data.edges.map((e, i) => {
          const a = byId.get(e.from)
          const b = byId.get(e.to)
          if (!a || !b) return null
          if (e.type === 'stored_in' && b.kind === 'offsite_vault') return null
          const show = isVis(a.id) && isVis(b.id)
          const both = (imp.has(a.id) || targets.has(a.id)) && (imp.has(b.id) || targets.has(b.id))
          const destroyed = both && (imp.get(a.id)?.effect === 'destroyed' || imp.get(b.id)?.effect === 'destroyed')
          const delay = both ? Math.max(imp.get(a.id)?.hops ?? 0, imp.get(b.id)?.hops ?? 0) * 0.35 : 0
          const rep = e.type === 'replicates_to'
          return (
            <path key={i} className="map-edge" fill="none" d={edgePath(a, b, nodes)}
              stroke={both ? (destroyed ? 'var(--block)' : '#e98a8d') : rep ? 'var(--ink-3)' : 'var(--line-2)'}
              strokeWidth={both ? 2 : rep ? 1.6 : 1.1}
              markerEnd={rep && e.to === b.id && b.kind !== 'replication_link' ? 'url(#arr)' : undefined}
              style={{ opacity: show ? (dimOthers && anyHot && !both ? 0.25 : 1) : 0, transitionDelay: `${delay}s` }} />
          )
        })}
        {nodes.map((n) => {
          const show = isVis(n.id)
          const im = imp.get(n.id)
          const cls = [
            targets.has(n.id) ? 'is-target' : '',
            im ? (im.effect === 'destroyed' ? 'is-destroyed' : 'is-affected') : harmed.has(n.id) ? 'is-destroyed' : '',
          ].join(' ')
          const delay = im ? im.hops * 0.35 : 0
          const labelOn = n.kind !== 'offsite_vault' && (labels === 'all' || hot(n.id) || LABELLED.has(n.kind) || n.kind === 'replication_link')
          const above = n.kind === 'replication_link'
          const op = !show ? 0 : dimOthers && anyHot && !hot(n.id) ? 0.3 : removed.has(n.id) ? 0.4 : 1
          return (
            <g key={n.id} className="map-node" transform={`translate(${n.x},${n.y})`} style={{ opacity: op, pointerEvents: show ? 'auto' : 'none' }}
              onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}
              onClick={() => onSelect?.(selected === n.id ? null : n)}>
              {im && (
                <circle key={`r-${rippleKey}`} className="ripple" r={n.r + 6} stroke={im.effect === 'destroyed' ? 'var(--block)' : '#e98a8d'} style={{ animationDelay: `${delay}s` }} />
              )}
              {harmed.has(n.id) && <circle r={n.r + 9} fill="var(--block)" opacity={0.18} />}
              {n.id === selected && <circle r={n.r + 8} className="ring" stroke="var(--ink)" strokeWidth={1.5} />}
              <g style={{ transition: 'opacity 0.4s', transitionDelay: `${delay}s` }}>
                <Glyph n={n} cls={cls} />
              </g>
              {removed.has(n.id) && (
                <g stroke="var(--block)" strokeWidth={2.6} strokeLinecap="round">
                  <line x1={-n.r * 0.8} y1={-n.r * 0.8} x2={n.r * 0.8} y2={n.r * 0.8} />
                  <line x1={n.r * 0.8} y1={-n.r * 0.8} x2={-n.r * 0.8} y2={n.r * 0.8} />
                </g>
              )}
              {changed.has(n.id) && !removed.has(n.id) && <circle r={n.r + 5} fill="none" stroke="var(--ink-2)" strokeWidth={1.4} />}
              {labelOn && (
                <text y={above ? -n.r - 8 : n.r + 15 + (n.lv ?? 0) * 15} textAnchor="middle" className="halo" style={{
                  fontSize: targets.has(n.id) ? 13.5 : above ? 11.5 : 12.5, fontWeight: targets.has(n.id) ? 600 : 400,
                  fill: targets.has(n.id) ? 'var(--brand-ink)' : im?.effect === 'destroyed' ? '#f07b7f' : 'var(--ink-2)',
                }}>
                  {above ? 'replication' : trunc(n.name || n.id, 20)}
                </text>
              )}
              {n.tier === 1 && ['volume', 'clone'].includes(n.kind) && (
                <text x={n.r + 2} y={-n.r + 2} style={{ fontSize: 10.5, fill: 'var(--ink)', fontWeight: 600 }}>T1</text>
              )}
            </g>
          )
        })}
      </svg>
      {hv && isVis(hv.id) && (
        <div className="map-tip" style={{ left: Math.min(hv.x * s + 14, (wrap.current?.clientWidth || 600) - 310), top: Math.max(4, hv.y * s - 10) }}>
          <b>{hv.name || hv.id}</b>
          <div className="muted tiny" style={{ marginBottom: 4 }}>{KIND[hv.kind] || hv.kind} <span className="mono">{hv.id}</span></div>
          {factLines(hv).map((l, i) => <div key={i}>{l}</div>)}
          {imp.get(hv.id) && <div style={{ marginTop: 6, color: '#f07b7f' }}>{imp.get(hv.id)!.effect === 'destroyed' ? 'Would be destroyed' : 'Would lose protection or access'} ({imp.get(hv.id)!.hops} link{imp.get(hv.id)!.hops === 1 ? '' : 's'} away)</div>}
        </div>
      )}
    </div>
  )
}

export function MapLegend({ blast = true }: { blast?: boolean }) {
  return (
    <div className="legend">
      <span><svg width="22" height="14"><rect x="1" y="1" width="20" height="12" rx="3" fill="var(--panel-3)" stroke="var(--ink-3)" /></svg>volume</span>
      <span><svg width="14" height="14"><circle cx="7" cy="7" r="6" fill="var(--panel-3)" stroke="var(--ink-3)" /></svg>snapshot</span>
      <span><svg width="14" height="14"><rect x="2" y="2" width="10" height="10" rx="2" fill="var(--panel-3)" stroke="var(--ink-3)" /></svg>backup</span>
      <span><svg width="16" height="16"><rect x="4" y="4" width="8" height="8" transform="rotate(45 8 8)" fill="var(--panel-3)" stroke="var(--ink-3)" /></svg>replication link</span>
      <span><svg width="18" height="16"><polygon points="9,1 16,5 16,11 9,15 2,11 2,5" fill="var(--panel-3)" stroke="var(--ink-3)" /></svg>app (tier)</span>
      <span><svg width="20" height="14"><rect x="1" y="1" width="18" height="12" rx="2" fill="var(--panel-3)" stroke="var(--ink-3)" /></svg>server</span>
      {blast && <span><svg width="16" height="16"><circle cx="8" cy="8" r="6" fill="none" stroke="var(--brand)" strokeWidth="2.2" /></svg>what the call touches</span>}
      {blast && <span><svg width="16" height="16"><circle cx="8" cy="8" r="6" fill="none" stroke="var(--block)" strokeWidth="2.2" /></svg>would be destroyed</span>}
      {blast && <span><svg width="16" height="16"><circle cx="8" cy="8" r="6" fill="none" stroke="#e98a8d" strokeWidth="1.8" /></svg>would lose protection or access</span>}
    </div>
  )
}

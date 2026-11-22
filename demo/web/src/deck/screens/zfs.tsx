// Chapter 6: one ticket, two safety methods, side by side on OpenZFS.
// Recorded in the throwaway Lima VM (results/zfs, exported by demo/export_zfs.py): every command below is what
// ZFS was asked to do, and every struck-through name is something ZFS itself reported destroying.
import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useMemo, useState } from 'react'
import { fetchZfs, fetchZfsIndex, useLoad } from '../../lib/api'
import { MAIN_MODELS, MODELS, fmtGB, plainCall } from '../../lib/plain'
import type { Decision, ZfsCmd, ZfsRow, ZfsRun, ZfsStep, ZfsTicket } from '../../lib/types'
import { Chips, type ScreenProps } from '../ui'

const METHODS = [
  { v: 'noguard', label: 'No guard' },
  { v: 'prompt_only', label: 'Rules in the prompt' },
  { v: 'llm_judge', label: 'AI checker' },
  { v: 'rbac', label: 'Ask a person before every delete' },
  { v: 'storageguard', label: 'StorageGuard' },
]
const TYPE_MS = 1500
const STEP_MS = 2300

type Fx = 'gone' | 'new' | 'moved' | 'rolled' | 'stopped'
type Phase = 'before' | 'typing' | 'running' | 'done'

/** One shared clock for both panes: type the ticket, then apply one agent step per tick. */
function useClock(on: boolean, key: string, steps: number) {
  const [t, setT] = useState<{ phase: Phase; applied: number }>({ phase: 'before', applied: 0 })
  useEffect(() => {
    if (!on) {
      setT({ phase: 'before', applied: 0 })
      return
    }
    setT({ phase: 'typing', applied: 0 })
    const ids: number[] = []
    for (let i = 1; i <= steps + 1; i++) {
      ids.push(window.setTimeout(() => setT({ phase: i > steps ? 'done' : 'running', applied: i }), TYPE_MS + (i - 1) * STEP_MS))
    }
    return () => ids.forEach(clearTimeout)
  }, [on, key, steps])
  return t
}

function Typed({ text, on }: { text: string; on: boolean }) {
  const [n, setN] = useState(0)
  useEffect(() => {
    setN(0)
    if (!on) return
    const per = Math.max(5, (TYPE_MS - 250) / text.length)
    let i = 0
    const id = window.setInterval(() => {
      i += 1
      setN(i)
      if (i >= text.length) clearInterval(id)
    }, per)
    return () => clearInterval(id)
  }, [on, text])
  if (!on) return <span className="z-placeholder">The ticket goes here</span>
  return <>{text.slice(0, n)}{n < text.length && <span className="z-caret" />}</>
}

/** The tree after `applied` steps: what each row now is, and what changed in the latest step. */
function derive(t: ZfsTicket, run: ZfsRun, applied: number) {
  const rows = new Map<string, ZfsRow>(t.tree.map((r) => [r.id, { ...r }]))
  const fx = new Map<string, Fx>()
  const note = new Map<string, string>()
  const fresh = new Set<string>()
  const n = Math.min(applied, run.steps.length)
  const cur = applied >= 1 && applied <= run.steps.length ? run.steps[applied - 1] : undefined
  for (let j = 0; j < n; j++) {
    const s = run.steps[j]
    const mark = (id: string) => j === applied - 1 && fresh.add(id)
    s.created.forEach((r) => { rows.set(r.id, { ...r }); fx.set(r.id, 'new'); mark(r.id) })
    s.moved.forEach((m) => {
      const r = rows.get(m.id)
      if (r) rows.set(m.id, { ...r, parent: m.parent, zfs: m.zfs })
      fx.set(m.id, 'moved'); mark(m.id)
    })
    s.rolled.forEach((r) => { fx.set(r.id, 'rolled'); note.set(r.id, `rolled back to @${r.to}`); mark(r.id) })
    s.destroyed.forEach((id) => { fx.set(id, 'gone'); mark(id) })
  }
  // whatever lives inside a destroyed dataset went with it
  for (const r of rows.values()) {
    for (let p = r.parent; p; p = rows.get(p)?.parent ?? null) {
      if (fx.get(p) === 'gone' && r.kind !== 'zpool') {
        fx.set(r.id, 'gone')
        if (fresh.has(p)) fresh.add(r.id)
        break
      }
    }
  }
  // a change that was stopped: the thing it aimed at is untouched
  if (cur && (cur.decision === 'BLOCK' || ['blocked', 'denied_by_human', 'pending_approval'].includes(cur.status))) {
    Object.values(cur.args).forEach((v) => {
      if (typeof v === 'string' && rows.has(v) && fx.get(v) !== 'gone') { fx.set(v, 'stopped'); fresh.add(v) }
    })
  }
  return { rows, fx, note, fresh, cur }
}

interface Line { key: string; kind: 'zpool' | 'folder' | 'volume' | 'chips'; depth: number; row?: ZfsRow; chips?: ZfsRow[]; head?: string; inline?: ZfsRow[] }

const list = (xs: string[]) => (xs.length < 2 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`)

/** Flatten to lines: each ZFS pool, its volumes (snapshots and stored backups as chips), its recycle bin; then the vault. */
function linesOf(t: ZfsTicket, rows: Map<string, ZfsRow>, fx: Map<string, Fx>, vols: Set<string>): Line[] {
  const all = [...rows.values()]
  const showVol = (id: string) => vols.has(id) || fx.get(id) === 'new'
  const kids = (p: string, kind: ZfsRow['kind']) => all.filter((r) => r.parent === p && r.kind === kind)
  const num = (id: string) => Number(id.split('-')[1]) || 0
  const out: Line[] = []
  const volume = (v: ZfsRow, depth: number) => {
    const snaps = kids(v.id, 'snap')
    const bk = kids(v.id, 'backup')
    // one or two snapshots ride on the volume's own line; more get a line of their own
    const inline = snaps.length && snaps.length <= 2 && !bk.length ? snaps : undefined
    out.push({ key: v.id, kind: 'volume', depth, row: v, inline })
    if (snaps.length && !inline) out.push({ key: `${v.id}:snaps`, kind: 'chips', depth: depth + 1, chips: snaps, head: 'snapshots' })
    if (bk.length) out.push({ key: `${v.id}:bkps`, kind: 'chips', depth: depth + 1, chips: bk, head: 'backups/' })
  }
  for (const root of t.tree.filter((r) => r.parent === null)) {
    if (root.id === 'vault') {
      const bk = kids('vault', 'backup').filter((b) => fx.get(b.id) === 'new' || (b.src && showVol(b.src)))
      if (!bk.length) continue
      out.push({ key: 'vault', kind: 'zpool', depth: 0, row: root })
      out.push({ key: 'vault:bkps', kind: 'chips', depth: 1, chips: bk, head: '' })
      continue
    }
    const vs = kids(root.id, 'volume').filter((v) => showVol(v.id)).sort((a, b) => num(a.id) - num(b.id))
    const bin = all.find((r) => r.id === `rb:${root.label}`)
    const binVs = bin ? kids(bin.id, 'volume').filter((v) => showVol(v.id)).sort((a, b) => num(a.id) - num(b.id)) : []
    if (!vs.length && !binVs.length) continue
    out.push({ key: root.id, kind: 'zpool', depth: 0, row: root })
    vs.forEach((v) => volume(v, 1))
    if (bin && binVs.length) {
      out.push({ key: bin.id, kind: 'folder', depth: 1, row: bin })
      binVs.forEach((v) => volume(v, 2))
    }
  }
  return out
}

const TAG: Record<Fx, string> = { gone: 'destroyed', new: 'new', moved: 'in the recycle bin', rolled: '', stopped: 'refused, untouched' }

function Glyph({ kind }: { kind: Line['kind'] }) {
  if (kind === 'zpool') {
    return (
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden className="z-glyph">
        <ellipse cx="8" cy="4" rx="6" ry="2.2" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <path d="M2 4v8c0 1.2 2.7 2.2 6 2.2s6-1 6-2.2V4M2 8c0 1.2 2.7 2.2 6 2.2s6-1 6-2.2" fill="none" stroke="currentColor" strokeWidth="1.3" />
      </svg>
    )
  }
  if (kind === 'folder') {
    return (
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden className="z-glyph">
        <path d="M1.5 4.5h4.2l1.4 1.6h7.4v7.4h-13z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      </svg>
    )
  }
  return <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden className="z-glyph"><circle cx="8" cy="8" r="5.5" fill="currentColor" /></svg>
}

function Tree({ t, run, applied, vols }: { t: ZfsTicket; run: ZfsRun; applied: number; vols: Set<string> }) {
  const { rows, fx, note, fresh } = derive(t, run, applied)
  const lines = linesOf(t, rows, fx, vols)
  return (
    <div className="z-tree">
      <AnimatePresence initial={false}>
        {lines.map((l) => {
          const r = l.row
          const f = r ? fx.get(r.id) : undefined
          return (
            <motion.div key={l.key} layout="position" className={`z-line ${l.kind}${f ? ` ${f}` : ''}${r && fresh.has(r.id) ? ' fresh' : ''}`}
              style={{ paddingLeft: 10 + l.depth * 26 }}
              initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}
              transition={{ duration: 0.45, layout: { type: 'spring', stiffness: 120, damping: 20 } }}>
              {l.kind === 'chips' ? (
                <div className="z-chips">
                  {l.head && <span className="z-chips-head">{l.head}</span>}
                  {l.chips!.map((c) => {
                    const cf = fx.get(c.id)
                    return <span key={c.id} title={c.title || c.label} className={`z-chip${cf ? ` ${cf}` : ''}${fresh.has(c.id) ? ' fresh' : ''}${c.locked ? ' locked' : ''}`}>{c.label}</span>
                  })}
                </div>
              ) : (
                <>
                  <Glyph kind={l.kind} />
                  <span className="z-name">{r!.label}</span>
                  {l.kind === 'volume' && <span className="z-path mono">{r!.zfs}</span>}
                  {r!.sub && !l.inline && <span className="z-sub">{r!.sub}</span>}
                  {l.inline?.map((c) => {
                    const cf = fx.get(c.id)
                    return <span key={c.id} title={c.title || c.label} className={`z-chip${cf ? ` ${cf}` : ''}${fresh.has(c.id) ? ' fresh' : ''}`}>{c.label}</span>
                  })}
                  {l.kind === 'volume' && r!.gb ? <span className="z-gb">{fmtGB(r!.gb)}</span> : null}
                  {f && (TAG[f] || note.get(r!.id)) && <span className={`z-tag ${f}`}>{f === 'rolled' ? note.get(r!.id) : TAG[f]}</span>}
                </>
              )}
            </motion.div>
          )
        })}
      </AnimatePresence>
    </div>
  )
}

function decisionTag(s: ZfsStep, config: string): { cls: string; text: string } {
  const plainMethod = config === 'noguard' || config === 'prompt_only'
  const d = s.decision as Decision | null
  if (!d || (d === 'ALLOW' && plainMethod)) return { cls: 'NONE', text: 'went ahead' }
  if (d === 'ESCALATE') return { cls: 'ESCALATE', text: s.human === 'approved' || s.status === 'executed' ? 'asked a person: yes' : 'asked a person: no' }
  return { cls: d, text: { ALLOW: 'allowed', REWRITE: 'rewritten', BLOCK: 'refused' }[d] }
}

function cmdResult(c: ZfsCmd, destroyed: number): { cls: string; text: string } {
  if (!c.twinOk) return { cls: 'muted', text: 'the array refused it' }
  if (c.native === 'refused') return { cls: 'rewrite-ink', text: `ZFS: ${c.said.split('\n')[0]}` }
  if (c.native === 'emulated') return { cls: 'muted', text: 'done (moved, not destroyed)' }
  if (c.native === 'no-data-effect') return { cls: 'muted', text: '' }
  if (c.tool.startsWith('delete') || c.tool === 'purge_recycle_bin') return { cls: 'harm-ink', text: `done, ZFS destroyed ${destroyed}` }
  return { cls: 'safe-ink', text: 'done' }
}

function StepLog({ s, i, n, config, names }: { s: ZfsStep; i: number; n: number; config: string; names: (id: string) => string }) {
  const tag = decisionTag(s, config)
  return (
    <motion.div key={i} className="z-log" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.3 }}>
      <div className="z-call">
        <span className="faint">{i + 1} of {n}</span>
        <span className="z-call-text">{plainCall({ tool: s.tool, args: s.args } as never, names)}</span>
        <span className={`d-tag ${tag.cls}`}>{tag.text}</span>
      </div>
      {s.reasons.slice(0, s.cmds.length > 1 ? 1 : 2).map((r) => <div key={r} className="z-reason">{r}</div>)}
      {s.cmds.length ? s.cmds.map((c, k) => {
        const res = cmdResult(c, s.destroyed.length)
        return (
          <div key={k} className="z-cmd mono">
            <span className="z-dollar">$</span>
            <span className="z-cmd-text">{c.cmd}</span>
            {res.text && <span className={`z-res ${res.cls}`}>{res.text}</span>}
          </div>
        )
      }) : <div className="z-cmd z-none">Nothing was sent to ZFS.</div>}
    </motion.div>
  )
}

function Aftermath({ run, names }: { run: ZfsRun; names: (id: string) => string }) {
  const o = run.outcome
  const hrs = (m: number | null) => (m ? `${Math.round(m / 60)} hours` : 'some hours')
  const lines: string[] = [
    ...o.lost.map((x) => `${x.name}: ${x.gb ? fmtGB(x.gb) : 'its data'} destroyed, with no copy left anywhere.`),
    ...o.rolled.map((x) => `${x.name}: the newest copy left is ${hrs(x.minutes)} old.`),
    ...o.site.map((x) => `${x.name}: its live copy is gone. If the main site fails now, ${hrs(x.minutes)} of data are lost.`),
  ]
  if (o.outage.length) lines.push(`Servers writing to ${list(o.outage.map((x) => x.name))} started failing.`)
  if (!lines.length) {
    // why nothing was lost: what the method did instead
    const made = run.steps.flatMap((s) => s.created.filter((r) => r.kind === 'backup').map((r) => r.label))
    const binned = run.steps.flatMap((s) => s.moved.filter((m) => m.parent.startsWith('rb:')).map((m) => names(m.id)))
    const refused = run.steps.filter((s) => s.decision === 'BLOCK' || ['blocked', 'denied_by_human'].includes(s.status))
      .flatMap((s) => Object.values(s.args).filter((v): v is string => typeof v === 'string' && /^(vol|snap|bkp)-/.test(v)).map(names))
    if (made.length) lines.push(`A backup made first (${made.join(', ')}) still holds the data.`)
    if (binned.length) lines.push(`${list(binned)} went to the recycle bin, where ${binned.length > 1 ? 'they' : 'it'} can be restored.`)
    if (refused.length) lines.push(`Refused to touch ${list([...new Set(refused)])}.`)
    lines.push(o.success ? 'The ticket was done.' : 'The ticket was not finished.')
  }
  const n = run.steps.reduce((a, s) => a + s.destroyed.length, 0)
  const big = o.lostGb > 0 ? `${fmtGB(o.lostGb)} lost` : o.unsafe ? 'Data at risk' : 'Nothing lost'
  return (
    <motion.div className="z-after" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }}>
      <div className={`z-big ${o.unsafe ? 'harm-ink' : 'safe-ink'}`}>{big}</div>
      <div className="z-after-text">
        {lines.slice(0, 3).map((l) => <div key={l}>{l}</div>)}
        <div className="faint">ZFS destroyed {n} {n === 1 ? 'dataset or snapshot' : 'datasets and snapshots'} in this run.</div>
      </div>
    </motion.div>
  )
}

function Pane({ t, run, config, setConfig, model, clock, vols, typedKey }: {
  t: ZfsTicket; run: ZfsRun; config: string; setConfig: (c: string) => void; model: string
  clock: { phase: Phase; applied: number }; vols: Set<string>; typedKey: string
}) {
  const names = useMemo(() => {
    const m = { ...t.names, ...run.names }
    return (id: string) => m[id] || id
  }, [t, run])
  const n = run.steps.length
  const applied = clock.phase === 'before' || clock.phase === 'typing' ? 0 : clock.applied
  const done = clock.phase !== 'before' && clock.phase !== 'typing' && applied > n
  const cur = applied >= 1 && applied <= n ? run.steps[applied - 1] : undefined
  const isGuard = config === 'storageguard'
  return (
    <div className={`z-pane${isGuard ? ' guard' : ''}`}>
      <div className="z-pane-head">
        <select className="d-select z-method" value={config} onChange={(e) => setConfig(e.target.value)} aria-label="Safety method">
          {METHODS.map((m) => <option key={m.v} value={m.v}>{m.label}</option>)}
        </select>
        <span className="z-on">{MODELS[model].label} on OpenZFS</span>
      </div>
      <div className="z-ticket">
        <div className="from">Ticket {t.task.id.replace('task-', '#')}</div>
        <div className="z-ticket-text"><Typed key={typedKey} text={t.task.text} on={clock.phase !== 'before'} /></div>
      </div>
      <Tree t={t} run={run} applied={applied} vols={vols} />
      <div className="z-slot">
        <AnimatePresence mode="wait" initial={false}>
          {done ? <Aftermath key="after" run={run} names={names} />
            : cur ? <StepLog key={`s${applied}`} s={cur} i={applied - 1} n={n} config={config} names={names} />
              : <motion.div key="wait" className="z-wait" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                {clock.phase === 'typing' ? `Sending the ticket to ${MODELS[model].label}…` : clock.phase === 'before' ? 'Before the ticket: the storage as it was.' : 'Finished.'}
              </motion.div>}
        </AnimatePresence>
      </div>
    </div>
  )
}

export function OnZfs({ build }: ScreenProps) {
  const { data: index } = useLoad(fetchZfsIndex, [])
  const [task, setTask] = useState('task-ffaa2f46')
  const [model, setModel] = useState('llama3.1-8b')
  const [left, setLeft] = useState('noguard')
  const [right, setRight] = useState('storageguard')
  const [nonce, setNonce] = useState(0)
  const { data: t, error } = useLoad(() => fetchZfs(task), [task])
  const L = t?.runs[`${model}__${left}`]
  const R = t?.runs[`${model}__${right}`]
  const key = `${task}|${model}|${left}|${right}|${nonce}`
  const steps = Math.max(L?.steps.length ?? 0, R?.steps.length ?? 0)
  const clock = useClock(build >= 1 && !!t && !!L && !!R, key, steps)
  const vols = useMemo(() => new Set([...(L?.vols || []), ...(R?.vols || [])]), [L, R])
  return (
    <div className="d-screen">
      <div className="z-top">
        <div className="z-intro">
          <h1 className="z-head">Watch it on real storage.</h1>
          <p className="z-support">Recorded on {t?.zfs?.[0]?.replace('zfs-', 'OpenZFS ').replace(/-.*$/, '') || 'OpenZFS'} in a throwaway virtual machine. Same AI, same ticket, two safety methods.</p>
        </div>
        <div className="z-controls">
          <div className="z-row"><span className="lbl">AI model</span>
            <Chips value={model} onChange={setModel} options={MAIN_MODELS.map((m) => ({ v: m, label: MODELS[m].label }))} />
          </div>
          <div className="z-row"><span className="lbl">Ticket</span>
            <Chips value={task} onChange={setTask} options={(index || []).map((x) => ({ v: x.id, label: x.short }))} />
          </div>
        </div>
      </div>
      {error ? <div className="z-panes"><div className="d-note">{error}</div></div> : t && L && R ? (
        <div className="z-panes">
          <Pane t={t} run={L} config={left} setConfig={setLeft} model={model} clock={clock} vols={vols} typedKey={key} />
          <Pane t={t} run={R} config={right} setConfig={setRight} model={model} clock={clock} vols={vols} typedKey={key} />
        </div>
      ) : <div className="z-panes"><div className="d-note">Loading…</div></div>}
      {build >= 1 && clock.phase === 'done' && (
        <button className="d-link z-replay" onClick={() => setNonce((x) => x + 1)}>Replay</button>
      )}
    </div>
  )
}

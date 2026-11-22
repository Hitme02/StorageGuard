// Chapter 4: try it. Pick a ticket, who works it and a safety method; the run plays out on the map.
import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useMemo, useRef, useState } from 'react'
import { DEFAULT_KNOBS, fetchLiveRun, fetchRecorded, fetchTasks, useBackend, useLoad } from '../../lib/api'
import { HARM, MAIN_MODELS, MODELS, nameLookup, plainCall } from '../../lib/plain'
import type { Decision, Knobs, Run, Step } from '../../lib/types'
import { C, Graph, autoLayout, type GEdge, type GNode, type NState } from '../Graph'
import { HOOK } from '../hook'
import { Chips, Switch, type ScreenProps } from '../ui'

const PICKS = [
  { id: HOOK, name: 'Delete three idle volumes' },
  { id: 'task-ffaa2f46', name: 'Delete a volume, restore it from backup' },
  { id: 'task-12626ba5', name: 'Remove an old snapshot' },
  { id: 'task-66b78890', name: 'Unmap a volume from a retired server' },
  { id: 'task-77d1d85f', name: 'Roll a volume back after an accident' },
  { id: 'task-e578d8c6', name: 'Move a spare disk between pools' },
  { id: 'task-5cd1a880', name: 'Tidy up a pool (with a planted note)' },
  { id: 'task-0204356a', name: 'Take a snapshot (routine)' },
]

type Who = 'naive' | 'reference' | 'recorded'
const METHODS: { v: string; label: string }[] = [
  { v: 'noguard', label: 'No guard' },
  { v: 'prompt_only', label: 'Prompt rules' },
  { v: 'rbac', label: 'Ask before deletes' },
  { v: 'llm_judge', label: 'AI checker' },
  { v: 'storageguard', label: 'StorageGuard' },
]
const TAU_ALLOW = [0.05, 0.1, 0.15, 0.2, 0.25, 0.35, 0.5]
const TAU_BLOCK = [0.4, 0.55, 0.7, 0.85, 0.95]
const DWORD: Record<Decision, string> = { ALLOW: 'Allowed', REWRITE: 'Rewritten', ESCALATE: 'Asked a human', BLOCK: 'Refused' }
const STATUS: Record<string, string> = {
  executed: 'it went ahead',
  rewritten: 'the safer version ran',
  blocked: 'nothing changed',
  denied_by_human: 'the human said no',
  pending_approval: 'no human available, nothing changed',
  error: 'the storage system rejected it',
}

function useRun(req: { task: string; who: Who; model: string; config: string; knobs: Knobs }) {
  const [s, set] = useState<{ run: Run | null; error: string | null }>({ run: null, error: null })
  const seq = useRef(0)
  const key = JSON.stringify(req)
  useEffect(() => {
    const my = ++seq.current
    set({ run: null, error: null })
    const t = setTimeout(() => {
      const p = req.who === 'recorded'
        ? fetchRecorded(req.model, req.config, req.task, 0)
        : fetchLiveRun(req.task, `scripted:${req.who}` as 'scripted:naive', req.config, req.knobs)
      p.then((run) => my === seq.current && set({ run, error: null }))
        .catch((e) => my === seq.current && set({ run: null, error: String(e.message || e) }))
    }, 150)
    return () => clearTimeout(t)
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  return s
}

function argIds(step: Step, ids: Set<string>): string[] {
  return Object.values(step.args || {}).filter((v): v is string => typeof v === 'string' && ids.has(v))
}

function Player({ run }: { run: Run }) {
  const steps = useMemo(() => run.steps.filter((s) => s.type !== 'read' && s.type !== 'say'), [run])
  const [pos, setPos] = useState(-1)
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    setPos(-1)
    let i = -1
    const tick = () => {
      i += 1
      setPos(i)
      if (i < steps.length - 1) t = window.setTimeout(tick, 1700)
    }
    let t = window.setTimeout(tick, 700)
    return () => clearTimeout(t)
  }, [steps, nonce])

  const W = 952
  const H = 440
  const lay = useMemo(() => autoLayout(run.map, W, H), [run.map])
  const ids = useMemo(() => new Set(run.map.nodes.map((n) => n.id)), [run.map])
  const name = nameLookup(run.map)
  const cur = pos >= 0 ? steps[pos] : undefined
  const g = cur?.guard
  const finished = pos >= steps.length - 1
  const removed = new Set(steps.slice(0, pos + 1).flatMap((s) => s.changes?.removed || []))
  const harmed = new Set(finished ? run.outcome.harms.map((h) => h.entity) : [])
  const targets = new Set(cur ? (g?.targets?.length ? g.targets : argIds(cur, ids)) : [])
  const stopped = g && (g.decision === 'BLOCK' || (g.decision === 'ESCALATE' && g.status !== 'executed') || g.decision === 'REWRITE')
  const impact = new Map((stopped ? g?.impacted || [] : []).map((i) => [i.id, i.hops]))
  const labelled = new Set([...run.map.targets, ...targets, ...harmed, ...run.map.nodes.filter((n) => n.kind === 'app').map((n) => n.id)])

  const state = (id: string): NState => {
    if (harmed.has(id)) return 'harm'
    if (removed.has(id)) return 'gone'
    if (targets.has(id)) return stopped ? 'guard' : 'target'
    if (impact.has(id)) return 'hurt'
    return 'idle'
  }
  const nodes: GNode[] = run.map.nodes.map((n) => {
    const p = lay.get(n.id) || { x: W / 2, y: H / 2 }
    const nm = n.name || n.id
    return {
      id: n.id, kind: n.kind, x: p.x, y: p.y, state: state(n.id), title: nm,
      label: labelled.has(n.id) ? (nm.length > 20 ? nm.slice(0, 19) + '…' : nm) : undefined,
      delay: impact.has(n.id) ? 0.2 + (impact.get(n.id) || 0) * 0.25 : 0,
    }
  })
  const hot = new Set([...impact.keys(), ...targets])
  const edges: GEdge[] = run.map.edges.filter((e) => ids.has(e.from) && ids.has(e.to)).map((e) => {
    let st: GEdge['state'] = 'idle'
    if (removed.has(e.from) || removed.has(e.to)) st = 'gone'
    else if (harmed.has(e.from) || harmed.has(e.to)) st = 'harm'
    else if (stopped && hot.has(e.from) && hot.has(e.to)) st = 'harm'
    return { a: e.from, b: e.to, state: st, delay: 0.15 }
  })

  const o = run.outcome
  const verdict = o.unsafe
    ? { cls: 'harm-ink', text: `Harm: ${o.harms.slice(0, 2).map((h) => `${(HARM[h.type]?.label || h.type).toLowerCase()} (${h.code.replace(/_/g, ' ')}, ${name(h.entity)})`).join('; ')}` }
    : o.success
      ? { cls: 'safe-ink', text: 'No harm, and the ticket is done.' }
      : { cls: 'muted', text: 'No harm, but the ticket was not finished.' }

  return (
    <>
      <div className="d-play-map"><Graph nodes={nodes} edges={edges} width={W} height={H} /></div>
      <div className="d-play-foot">
        <div className="d-caption">
          {cur ? (
            <>
              <span className="faint">{pos + 1} of {steps.length}</span>
              <span className="call">{cur.type === 'finish' ? `Agent: “${String(cur.args.summary || cur.text || 'done')}”` : cur.type === 'ask' ? 'Agent asks the ticket owner a question' : plainCall(cur, name)}</span>
              {g ? <span className={`d-tag ${g.decision}`}>{DWORD[g.decision]}</span> : cur.type === 'mutate' ? <span className="d-tag NONE">went ahead</span> : null}
              {g && <span className="faint">{STATUS[g.status] || ''}</span>}
            </>
          ) : <span className="faint">Starting…</span>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 18, minHeight: 36 }}>
          <AnimatePresence>
            {finished && (
              <motion.div className={`d-verdict ${verdict.cls}`} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ delay: 0.4 }}>
                {verdict.text}
              </motion.div>
            )}
          </AnimatePresence>
          <button className="d-chip" style={{ marginLeft: 'auto' }} onClick={() => setNonce((n) => n + 1)}>Replay</button>
        </div>
      </div>
    </>
  )
}

export function Play(_: ScreenProps) {
  const backend = useBackend()
  const live = backend !== 'offline'
  const { data: tasks } = useLoad(fetchTasks, [])
  const [task, setTask] = useState(HOOK)
  const [who, setWho] = useState<Who>('naive')
  const [model, setModel] = useState('llama3.1-8b')
  const [config, setConfig] = useState('storageguard')
  const [knobs, setKnobs] = useState<Required<Knobs>>({ ...DEFAULT_KNOBS })
  const [open, setOpen] = useState(false)
  const scripted = who !== 'recorded'
  useEffect(() => {
    if (scripted && (config === 'prompt_only' || config === 'llm_judge')) setConfig('storageguard')
  }, [scripted, config])
  const effKnobs: Knobs = !scripted ? {} : config === 'storageguard' ? knobs : config === 'rbac' ? { human: knobs.human } : {}
  const { run, error } = useRun({ task, who, model, config, knobs: effKnobs })
  const t = tasks?.find((x) => x.id === task)
  const set = <K extends keyof Knobs>(k: K, v: Required<Knobs>[K]) => setKnobs((p) => ({ ...p, [k]: v }))
  const changed = (Object.keys(DEFAULT_KNOBS) as (keyof Knobs)[]).some((k) => knobs[k] !== DEFAULT_KNOBS[k])
  const knobsApply = scripted && config === 'storageguard'

  return (
    <div className="d-screen">
      <div className="d-text">
        <h1 className="d-head short">Your turn.</h1>
        <p className="d-support" style={{ minHeight: 0 }}>Pick a ticket, who works it, and a safety method. {live ? 'The real guard decides live.' : 'Recorded runs are shown; start the backend to change the guard settings.'}</p>
        <div className="d-tickets" style={{ marginTop: 26 }} role="radiogroup" aria-label="Ticket">
          {PICKS.map((p) => (
            <button key={p.id} role="radio" aria-checked={task === p.id} className={`d-tk${task === p.id ? ' on' : ''}`} onClick={() => setTask(p.id)}>
              <span style={{ width: 8, height: 8, borderRadius: 4, flex: 'none', alignSelf: 'center', background: task === p.id ? C.guard : C.line }} />
              {p.name}
            </button>
          ))}
        </div>
        {live && tasks && (
          <select className="d-select" style={{ marginTop: 14 }} value={PICKS.some((p) => p.id === task) ? '' : task} onChange={(e) => e.target.value && setTask(e.target.value)} aria-label="Any of the 150 tickets">
            <option value="">Any of the {tasks.length} tickets…</option>
            {tasks.map((x) => <option key={x.id} value={x.id}>{x.trap || x.category}: {x.instruction.split('\n\n').pop()!.slice(0, 70)}</option>)}
          </select>
        )}
      </div>

      <div className="d-visual">
        <div className="d-play-top">
          <div className="d-play-row">
            <span className="lbl">Who works it</span>
            <Chips value={who} onChange={setWho} options={[
              { v: 'naive', label: 'Careless plan', title: 'The obvious plan, which walks into the trap' },
              { v: 'reference', label: 'Careful plan', title: 'What a careful admin would do' },
              { v: 'recorded', label: 'Real AI', title: 'Replay what an AI model actually did' },
            ]} />
            {who === 'recorded' && (
              <select className="d-select" style={{ width: 180 }} value={model} onChange={(e) => setModel(e.target.value)} aria-label="AI model">
                {MAIN_MODELS.map((m) => <option key={m} value={m}>{MODELS[m].label}</option>)}
              </select>
            )}
          </div>
          <div className="d-play-row" style={{ position: 'relative' }}>
            <span className="lbl">Safety method</span>
            <Chips value={config} onChange={setConfig} guard={(v) => v === 'storageguard'} options={METHODS.map((m) => ({
              ...m, disabled: scripted && (m.v === 'prompt_only' || m.v === 'llm_judge'),
              title: scripted && (m.v === 'prompt_only' || m.v === 'llm_judge') ? 'Only changes what an AI does. Pick Real AI.' : undefined,
            }))} />
            <button className="d-link" style={{ marginLeft: 'auto', opacity: knobsApply ? 1 : 0.35 }} disabled={!knobsApply} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
              Guard settings{changed ? ' (changed)' : ''}
            </button>
            <AnimatePresence>
              {open && knobsApply && (
                <motion.div className="d-pop" style={{ right: 0, top: 46 }} initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.2 }}>
                  <div className="d-range">
                    <span>Ask a human from risk <b>{knobs.tau_allow}</b></span>
                    <input type="range" min={0} max={TAU_ALLOW.length - 1} value={TAU_ALLOW.indexOf(knobs.tau_allow)} aria-label="Ask a human from risk"
                      onChange={(e) => { const v = TAU_ALLOW[Number(e.target.value)]; set('tau_allow', v); if (knobs.tau_block <= v) set('tau_block', TAU_BLOCK.find((b) => b > v) ?? 0.95) }} />
                  </div>
                  <div className="d-range">
                    <span>Refuse from risk <b>{knobs.tau_block}</b></span>
                    <input type="range" min={0} max={TAU_BLOCK.length - 1} value={TAU_BLOCK.indexOf(knobs.tau_block)} aria-label="Refuse from risk"
                      onChange={(e) => { const v = TAU_BLOCK[Number(e.target.value)]; if (v > knobs.tau_allow) set('tau_block', v) }} />
                  </div>
                  <Switch on={!knobs.one_hop} onChange={(v) => set('one_hop', !v)}>Follow every link, not just neighbours</Switch>
                  <Switch on={knobs.rewrites} onChange={(v) => set('rewrites', v)}>Offer safer rewrites</Switch>
                  <Switch on={knobs.feedback} onChange={(v) => set('feedback', v)}>Explain decisions to the agent</Switch>
                  <Switch on={knobs.reversibility} onChange={(v) => set('reversibility', v)}>Weigh how hard it is to undo</Switch>
                  <Switch on={knobs.pit} onChange={(v) => set('pit', v)}>Protect the newest copy</Switch>
                  <div className="d-field">
                    <span className="lbl">When a human is asked</span>
                    <Chips value={knobs.human} onChange={(v) => set('human', v)} options={[
                      { v: 'oracle', label: 'Knows the answer' }, { v: 'none', label: 'Nobody' }, { v: 'rubber_stamp', label: 'Says yes' },
                    ]} />
                  </div>
                  <div style={{ display: 'flex', gap: 10, marginTop: 4 }}>
                    <button className="d-chip" onClick={() => setKnobs({ ...DEFAULT_KNOBS })}>Reset</button>
                    <button className="d-chip on" onClick={() => setOpen(false)}>Done</button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
          {t && <div className="d-play-ticket">“{t.instruction.split('\n\n').pop()}”</div>}
        </div>
        {error ? (
          <div className="d-play-map" style={{ display: 'grid', placeItems: 'center' }}>
            <div className="d-note" style={{ maxWidth: 520, textAlign: 'center', fontSize: 17 }}>
              {error.includes('needs the live backend') ? 'This combination needs the live backend. Start it with “make present”, or go back to the default settings.' : error}
            </div>
          </div>
        ) : run ? (
          <Player key={JSON.stringify([task, who, model, config, effKnobs])} run={run} />
        ) : (
          <div className="d-play-map" style={{ display: 'grid', placeItems: 'center' }}><span className="d-note">Running…</span></div>
        )}
      </div>
    </div>
  )
}

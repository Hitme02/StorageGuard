// Chapter 4: try it. The control room (every knob) and the same plan on real files.
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { EstateMap } from '../components/EstateMap'
import { Trace, VerdictLine, highlightFor, usePlayback } from '../components/Run'
import { Eng, ErrorNote, Loading, OriginTag, Seg, SlideHead, Switch, Term, statusText } from '../components/ui'
import { TRAP, ticketKind } from '../content/text'
import { DEFAULT_KNOBS, fetchLiveRun, fetchManifest, fetchRecorded, fetchTasks, useBackend, useLoad } from '../lib/api'
import { CONFIGS, MAIN_MODELS, MODELS, fmtGB, nameLookup, plainCall, shortId } from '../lib/plain'
import { usePicked } from '../lib/store'
import type { Knobs, Run, TaskSummary } from '../lib/types'
import { HOOK_TASK } from './ch1'

const TAU_ALLOW = [0.05, 0.1, 0.15, 0.2, 0.25, 0.35, 0.5]
const TAU_BLOCK = [0.4, 0.55, 0.7, 0.85, 0.95]
const ALL_MODELS = ['llama3.1-8b', 'ministral-3-8b', 'qwen2.5-7b', 'qwen2.5-7b-awq', 'qwen2.5-14b', 'qwen2.5-32b-int8', 'qwen2.5-72b-awq']

type Who = 'naive' | 'reference' | 'recorded'

function ticketLabel(t: TaskSummary) {
  const kind = t.trap ? `${t.trap}: ${TRAP[t.trap]?.name ?? ''}` : t.category === 'routine' ? 'Routine' : 'Ambiguous'
  return `${kind} (${shortId(t.id)})`
}

function TicketSelect({ tasks, value, onChange, available }: { tasks: TaskSummary[]; value: string; onChange: (v: string) => void; available: (t: TaskSummary) => boolean }) {
  const groups: [string, TaskSummary[]][] = [
    ['Traps', tasks.filter((t) => t.category === 'trap').sort((a, b) => Number(a.trap.slice(1)) - Number(b.trap.slice(1)))],
    ['Planted instructions', tasks.filter((t) => t.category === 'injection').sort((a, b) => a.trap.localeCompare(b.trap))],
    ['Routine', tasks.filter((t) => t.category === 'routine')],
    ['Ambiguous', tasks.filter((t) => t.category === 'ambiguous')],
  ]
  return (
    <select className="sel" value={value} onChange={(e) => onChange(e.target.value)}>
      {groups.map(([g, list]) => (
        <optgroup key={g} label={g}>
          {list.map((t) => (
            <option key={t.id} value={t.id} disabled={!available(t)}>
              {ticketLabel(t)}{t.split === 'dev' ? ' (practice set)' : ''}{available(t) ? '' : ' (needs backend)'}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  )
}

function useRunRequest(req: { task: string; who: Who; model: string; runIdx: number; config: string; knobs: Knobs } | null) {
  const [state, set] = useState<{ run: Run | null; error: string | null; loading: boolean }>({ run: null, error: null, loading: false })
  const seq = useRef(0)
  const key = JSON.stringify(req)
  useEffect(() => {
    if (!req) return
    const my = ++seq.current
    set((s) => ({ ...s, loading: true, error: null }))
    const t = setTimeout(() => {
      const p = req.who === 'recorded'
        ? fetchRecorded(req.model, req.config, req.task, req.runIdx)
        : fetchLiveRun(req.task, `scripted:${req.who}` as 'scripted:naive', req.config, req.knobs)
      p.then((run) => my === seq.current && set({ run, error: null, loading: false }))
        .catch((e) => my === seq.current && set({ run: null, error: String(e.message || e), loading: false }))
    }, 120)
    return () => clearTimeout(t)
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  return state
}

function RunColumn({ run, title, compact }: { run: Run; title: ReactNode; compact?: boolean }) {
  const pb = usePlayback(run, { autoplay: true, interval: compact ? 1300 : 1600 })
  const name = nameLookup(run.map)
  const cur = pb.cursor >= 0 ? run.steps[pb.cursor] : null
  const hl = highlightFor(run, pb.cursor)
  const done = pb.pos >= pb.count - 1
  const header = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 'none' }}>
      <div style={{ fontSize: 17, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{title}</div>
      <OriginTag origin={run.origin} />
      <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, flex: 'none' }}>
        <button className="btn sm" onClick={() => pb.step(-1)} aria-label="Previous step">‹</button>
        <button className="btn sm" onClick={pb.playing ? pb.pause : pb.play}>{pb.playing ? 'Pause' : 'Play'}</button>
        <button className="btn sm" onClick={() => pb.step(1)} aria-label="Next step">›</button>
        <span className="small muted" style={{ alignSelf: 'center', minWidth: 70, textAlign: 'right' }}>step {pb.pos + 1} of {pb.count}</span>
      </div>
    </div>
  )
  const map = <EstateMap data={run.map} highlight={hl} rippleKey={pb.cursor} dimOthers={!!cur?.guard} />
  if (!compact) {
    return (
      <div className="col grow" style={{ gap: 8, minWidth: 0, minHeight: 0 }}>
        {header}
        <div className="row grow" style={{ gap: 12, minHeight: 0 }}>
          <div className="panel flush grow" style={{ position: 'relative', minWidth: 0 }}>{map}</div>
          <div className="col" style={{ width: 420, flex: 'none', gap: 10, minHeight: 0 }}>
            <VerdictLine run={run} ready={done} stacked />
            <div className="panel grow" style={{ padding: 8, display: 'flex', minHeight: 0 }}>
              <Trace run={run} cursor={pb.cursor} onPick={pb.jumpTo} />
            </div>
          </div>
        </div>
      </div>
    )
  }
  return (
    <div className="col grow" style={{ gap: 8, minWidth: 0, minHeight: 0 }}>
      {header}
      <div className="panel flush" style={{ position: 'relative', height: 260, flex: 'none' }}>{map}</div>
      <VerdictLine run={run} ready={done} />
      <div className="small" style={{ minHeight: 20, display: 'flex', gap: 10, alignItems: 'center', whiteSpace: 'nowrap', overflow: 'hidden' }}>
        {cur ? (
          <>
            <span className="ink2">{cur.type === 'finish' ? 'Finished.' : plainCall(cur, name)}</span>
            {cur.guard && <span className="muted">{statusText(cur.guard.status)}</span>}
          </>
        ) : <span className="muted">Press Play.</span>}
      </div>
      <div className="panel grow" style={{ padding: 8, display: 'flex', minHeight: 0 }}>
        <Trace run={run} cursor={pb.cursor} onPick={pb.jumpTo} />
      </div>
    </div>
  )
}

export function ControlRoom() {
  const { data: tasks } = useLoad(fetchTasks, [])
  const { data: manifest } = useLoad(fetchManifest, [])
  const backend = useBackend()
  const picked = usePicked()
  const [task, setTask] = useState(HOOK_TASK)
  const [who, setWho] = useState<Who>('naive')
  const [model, setModel] = useState('llama3.1-8b')
  const [runIdx, setRunIdx] = useState(0)
  const [config, setConfig] = useState('storageguard')
  const [knobs, setKnobs] = useState<Required<Knobs>>({ ...DEFAULT_KNOBS })
  const [compare, setCompare] = useState(false)
  useEffect(() => { if (picked) setTask(picked) }, [picked])

  const live = backend !== 'offline'
  const scripted = who !== 'recorded'
  // keep combinations valid
  useEffect(() => {
    if (scripted && (config === 'llm_judge' || config === 'prompt_only')) setConfig('storageguard')
  }, [scripted, config])
  useEffect(() => {
    if (!live) { setRunIdx(0); if (!MAIN_MODELS.includes(model)) setModel('llama3.1-8b') }
  }, [live, model])

  const available = (t: TaskSummary) => live || !!manifest?.showcase.includes(t.id)
  const knobsApply = scripted && (config === 'storageguard' || config === 'rbac')
  const sgKnobs = scripted && config === 'storageguard'
  const effKnobs: Knobs = !scripted ? {} : config === 'storageguard' ? knobs : config === 'rbac' ? { human: knobs.human } : {}
  const req = tasks ? { task, who, model, runIdx, config, knobs: effKnobs } : null
  const main = useRunRequest(req)
  const base = useRunRequest(compare && req ? { ...req, config: 'noguard', knobs: {} } : null)
  const t = tasks?.find((x) => x.id === task)
  const set = <K extends keyof Knobs>(k: K, v: Required<Knobs>[K]) => setKnobs((p) => ({ ...p, [k]: v }))
  const whoLabel = who === 'recorded' ? MODELS[model].label : who === 'naive' ? 'Careless plan' : 'Correct plan'

  return (
    <>
      <SlideHead title="The control room." />
      <div className="row grow" style={{ gap: 18, marginTop: -6 }}>
        {/* ---- controls ---- */}
        <div className="panel col scroll" style={{ width: 330, gap: 10, flex: 'none', padding: '12px 16px' }}>
          <div className="field">
            <label>Ticket</label>
            {tasks ? <TicketSelect tasks={tasks} value={task} onChange={setTask} available={available} /> : <Loading />}
          </div>
          <div className="field">
            <span className="lbl">Who works the ticket</span>
            <Seg value={who} onChange={setWho} options={[
              { v: 'naive', label: 'Careless plan', title: 'The obvious plan, which walks into the trap' },
              { v: 'reference', label: 'Correct plan', title: 'What a careful admin would do' },
              { v: 'recorded', label: 'Real AI', title: 'Replay a recorded run of an AI model' },
            ]} />
            {who === 'recorded' && (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 86px', gap: 8 }}>
                <select className="sel" value={model} onChange={(e) => setModel(e.target.value)}>
                  {ALL_MODELS.map((m) => <option key={m} value={m} disabled={!live && !MAIN_MODELS.includes(m)}>{MODELS[m].label}</option>)}
                </select>
                <select className="sel" value={runIdx} onChange={(e) => setRunIdx(Number(e.target.value))}>
                  {[0, 1, 2].map((r) => <option key={r} value={r} disabled={!live && r > 0}>Run {r + 1}</option>)}
                </select>
              </div>
            )}
          </div>
          <div className="field">
            <span className="lbl">Safety method</span>
            <div className="col" style={{ gap: 1 }}>
              {['noguard', 'prompt_only', 'rbac', 'llm_judge', 'storageguard'].map((c) => {
                const dis = scripted && (c === 'llm_judge' || c === 'prompt_only')
                return (
                  <label key={c} className="small" style={{ display: 'flex', gap: 8, alignItems: 'center', opacity: dis ? 0.4 : 1, cursor: dis ? 'default' : 'pointer', color: config === c ? 'var(--ink)' : 'var(--ink-2)' }}
                    title={dis ? 'Only changes what an AI model does: pick “Real AI”' : CONFIGS[c].plain}>
                    <input type="radio" name="cfg" checked={config === c} disabled={dis} onChange={() => setConfig(c)} style={{ accentColor: 'var(--brand)' }} />
                    {CONFIGS[c].label}
                  </label>
                )
              })}
            </div>
          </div>
          <div className="hr" />
          <div className="col" style={{ gap: 10, opacity: knobsApply ? 1 : 0.4, pointerEvents: knobsApply ? 'auto' : 'none' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span className="small ink2" style={{ fontWeight: 600 }}>Guard settings{knobsApply ? '' : ', for the scripted plans'}</span>
              <button className="btn sm" onClick={() => setKnobs({ ...DEFAULT_KNOBS })} disabled={!knobsApply}>Reset</button>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, opacity: sgKnobs ? 1 : 0.4, pointerEvents: sgKnobs ? 'auto' : 'none' }}>
              <div className="field" style={{ gap: 2 }}>
                <label title="Below this risk the call goes ahead">Ask a human from <b className="num" style={{ color: 'var(--ink)' }}>{knobs.tau_allow}</b></label>
                <input className="slider" type="range" min={0} max={TAU_ALLOW.length - 1} step={1} value={TAU_ALLOW.indexOf(knobs.tau_allow)} aria-label="Ask a human from risk"
                  onChange={(e) => { const v = TAU_ALLOW[Number(e.target.value)]; set('tau_allow', v); if (knobs.tau_block <= v) set('tau_block', TAU_BLOCK.find((b) => b > v) ?? 0.95) }} />
              </div>
              <div className="field" style={{ gap: 2 }}>
                <label title="At or above this risk the call is refused">Refuse from <b className="num" style={{ color: 'var(--ink)' }}>{knobs.tau_block}</b></label>
                <input className="slider" type="range" min={0} max={TAU_BLOCK.length - 1} step={1} value={TAU_BLOCK.indexOf(knobs.tau_block)} aria-label="Refuse from risk"
                  onChange={(e) => { const v = TAU_BLOCK[Number(e.target.value)]; if (v > knobs.tau_allow) set('tau_block', v) }} />
              </div>
            </div>
            <div className="col" style={{ gap: 7, opacity: sgKnobs ? 1 : 0.4, pointerEvents: sgKnobs ? 'auto' : 'none' }}>
              <Switch on={!knobs.one_hop} onChange={(v) => set('one_hop', !v)}>Follow the links all the way</Switch>
              <Switch on={knobs.rewrites} onChange={(v) => set('rewrites', v)}>Offer safer rewrites</Switch>
              <Switch on={knobs.feedback} onChange={(v) => set('feedback', v)}>Explain decisions to the agent</Switch>
              <Switch on={knobs.reversibility} onChange={(v) => set('reversibility', v)}>Weigh how undoable it is</Switch>
              <Switch on={knobs.pit} onChange={(v) => set('pit', v)}>Newest-copy check (added later)</Switch>
            </div>
            <div className="field">
              <span className="lbl">Human approver</span>
              <Seg value={knobs.human} onChange={(v) => set('human', v)} options={[{ v: 'oracle', label: 'Knows the ticket' }, { v: 'none', label: 'Nobody' }, { v: 'rubber_stamp', label: 'Says yes' }]} />
            </div>
          </div>
          {!scripted && <div className="tiny muted">Recorded runs replay what the model actually did, so the guard settings do not apply.</div>}
        </div>

        {/* ---- results ---- */}
        <div className="col grow" style={{ gap: 10, minWidth: 0 }}>
          {t && (
            <div className="panel" style={{ padding: '10px 16px', display: 'flex', gap: 14, alignItems: 'flex-start' }}>
              <span className="chip" style={{ flex: 'none', marginTop: 3 }}>{ticketKind(t.category, t.trap).split(':')[0]}</span>
              <div className="grow" style={{ fontSize: 17, lineHeight: 1.35 }}>{t.instruction.split('\n\n').pop()}</div>
              <div style={{ flex: 'none', alignSelf: 'center' }}><Switch on={compare} onChange={setCompare}>Compare with no guard</Switch></div>
            </div>
          )}
          {main.error && <ErrorNote msg={main.error} />}
          {!main.run && !main.error && <Loading text="Running" />}
          {main.run && !compare && (
            <RunColumn key={JSON.stringify(req)} run={main.run} title={<>{whoLabel} with {CONFIGS[config].label}</>} />
          )}
          {main.run && compare && (
            <div className="row grow" style={{ gap: 16, minHeight: 0 }}>
              {base.run ? <RunColumn key={`b${JSON.stringify(req)}`} run={base.run} title={<>{whoLabel}, no guard</>} compact /> : base.error ? <ErrorNote msg={base.error} /> : <Loading />}
              <RunColumn key={`m${JSON.stringify(req)}`} run={main.run} title={<>{whoLabel} with {CONFIGS[config].label}</>} compact />
            </div>
          )}
          <Eng>
            Live runs call <span className="mono">POST /api/run</span>: the scripted plan goes through the real middleware with
            {' '}<span className="mono">GuardConfig</span> overrides {JSON.stringify(effKnobs)}. Recorded runs replay
            {' '}<span className="mono">results/runs/main/&lt;model&gt;/&lt;config&gt;/{task}__r{runIdx}.jsonl</span>.
          </Eng>
        </div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------- 4.2 real files
function fileName(p: string) {
  const parts = p.split('/')
  return parts[parts.length - 1].replace('.img', '')
}
function folderOf(p: string) {
  if (p.startsWith('vault/')) return 'offsite-vault'
  const m = p.match(/(vol-\d+)/)
  return m ? m[1] : 'other'
}

function drillLines(list: any[], name: (id: string) => string) {
  return list.map((x, i) => (
    <li key={i}>
      {name(x.owner)}: {x.kind === 'longer_loss_window'
        ? `would lose ${Math.round(x.window / 60)} h of writes instead of ${Math.round(x.base_window / 60)} h`
        : 'no readable copy left'}
    </li>
  ))
}

function FilesColumn({ run, label }: { run: Run; label: string }) {
  const p = run.physical!
  const name = nameLookup(run.map)
  const deleted = new Set(p.deletions.flatMap((d) => d.files.map((f) => f.path)))
  const [shown, setShown] = useState(0)
  useEffect(() => {
    setShown(0)
    const t = setTimeout(() => setShown(1), 700)
    return () => clearTimeout(t)
  }, [run])
  const touched = new Set([...deleted].map(folderOf))
  const folders = new Map<string, string[]>()
  for (const f of p.start_file_list) {
    const k = folderOf(f.path)
    if (!folders.has(k)) folders.set(k, [])
    folders.get(k)!.push(f.path)
  }
  const order = [...folders.keys()].sort((a, b) => Number(touched.has(b)) - Number(touched.has(a)) || a.localeCompare(b, undefined, { numeric: true }))
  const harmed = !!p.phys_unsafe
  return (
    <div className="col grow" style={{ gap: 10, minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: 18, fontWeight: 600 }}>{label}</span>
        <OriginTag origin={run.origin} />
        <span className={`verdict ${harmed ? 'harm' : 'safe'}`} style={{ fontSize: 20, marginLeft: 'auto' }}>{harmed ? '✕ Real harm' : '✓ No real harm'}</span>
      </div>
      <div className="panel" style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
        <div><div className="muted tiny">Files in the sandbox</div><div style={{ fontFamily: 'var(--cond)', fontSize: 28, fontWeight: 600 }}>{p.start_files}</div></div>
        <div><div className="muted tiny">Really deleted or overwritten</div><div style={{ fontFamily: 'var(--cond)', fontSize: 28, fontWeight: 600, color: deleted.size ? '#f07b7f' : undefined }}>{p.files_deleted + p.files_overwritten}</div></div>
        <div><div className="muted tiny">Data destroyed or rolled back</div><div style={{ fontFamily: 'var(--cond)', fontSize: 28, fontWeight: 600, color: p.destroyed.length + p.rolled_back.length ? '#f07b7f' : undefined }}>{p.destroyed.length + p.rolled_back.length ? fmtGB(p.destroyed.reduce((a, b) => a + b.gb, 0) + p.rolled_back.reduce((a, b) => a + (b.gb || 0), 0)) : '0'}</div></div>
      </div>
      <div className="panel scroll grow" style={{ padding: 12 }}>
        {order.slice(0, 14).map((k) => (
          <div key={k} style={{ display: 'grid', gridTemplateColumns: '170px 1fr', gap: 8, padding: '4px 0', borderTop: '1px solid var(--line)', alignItems: 'center' }}>
            <span className="small" style={{ color: touched.has(k) ? 'var(--ink)' : 'var(--ink-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{k === 'offsite-vault' ? 'offsite vault' : name(k)}</span>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
              {folders.get(k)!.map((f) => {
                const gone = deleted.has(f) && shown > 0
                return (
                  <span key={f} title={f} style={{
                    fontFamily: 'var(--mono)', fontSize: 10.5, padding: '1px 5px', borderRadius: 3,
                    border: '1px solid', borderColor: gone ? 'var(--block)' : 'var(--line-2)', color: gone ? '#f07b7f' : 'var(--ink-3)',
                    background: gone ? 'var(--block-wash)' : 'transparent', textDecoration: gone ? 'line-through' : 'none', transition: 'all 0.5s',
                  }}>{fileName(f)}</span>
                )
              })}
            </div>
          </div>
        ))}
        {order.length > 14 && <div className="tiny muted" style={{ paddingTop: 6 }}>{order.length - 14} untouched folders not shown</div>}
      </div>
      <div className="panel" style={{ padding: '10px 14px' }}>
        <div className="small ink2" style={{ fontWeight: 600, marginBottom: 4 }}>Drills on a copy of the files afterwards</div>
        <ul className="findings">
          <li><b>Main site destroyed:</b> {p.site_loss.length ? <ul className="findings">{drillLines(p.site_loss, name)}</ul> : 'nothing lost beyond what the ticket allowed'}</li>
          <li><b>Ransomware:</b> {p.ransomware.length ? <ul className="findings">{drillLines(p.ransomware, name)}</ul> : 'nothing lost beyond what the ticket allowed'}</li>
          <li><b>Ransomware with admin rights:</b> {p.ransomware_admin.length ? <ul className="findings">{drillLines(p.ransomware_admin, name)}</ul> : 'nothing lost beyond what the ticket allowed'}</li>
        </ul>
      </div>
    </div>
  )
}

export function RealFiles() {
  const { data: tasks } = useLoad(fetchTasks, [])
  const { data: manifest } = useLoad(fetchManifest, [])
  const backend = useBackend()
  const [task, setTask] = useState('task-ffaa2f46')
  const [nonce, setNonce] = useState(0)
  const live = backend !== 'offline'
  const a = useLoad(() => fetchLiveRun(task, 'scripted:naive', 'noguard', {}, true), [task, nonce])
  const b = useLoad(() => fetchLiveRun(task, 'scripted:naive', 'storageguard', {}, true), [task, nonce])
  const options = useMemo(() => (tasks || []).filter((t) => (live ? t.category === 'trap' || t.category === 'injection' : manifest?.showcase.includes(t.id))), [tasks, manifest, live])
  const t = tasks?.find((x) => x.id === task)
  return (
    <>
      <SlideHead title="The same careless plan, on real files." lede={<>A fresh temporary folder, a <Term id="sandbox">sandbox</Term>, gets one file per volume, snapshot and backup. Deletes really delete; locked copies get the operating system’s immutable flag. The folder is removed afterwards.</>} />
      <div className="row" style={{ gap: 12, alignItems: 'center' }}>
        <select className="sel" style={{ width: 560 }} value={task} onChange={(e) => setTask(e.target.value)}>
          {options.map((x) => <option key={x.id} value={x.id}>{ticketLabel(x)}</option>)}
        </select>
        <button className="btn" onClick={() => setNonce((n) => n + 1)} disabled={!live}>Run again on fresh files</button>
        {t && <span className="small ink2" style={{ flex: 1 }}>{t.instruction.split('\n\n').pop()}</span>}
      </div>
      <div className="row grow" style={{ gap: 18, minHeight: 0 }}>
        {a.data?.physical ? <FilesColumn run={a.data} label="No guard" /> : a.error ? <ErrorNote msg={a.error} /> : <Loading text="Creating the sandbox" />}
        {b.data?.physical ? <FilesColumn run={b.data} label="StorageGuard" /> : b.error ? <ErrorNote msg={b.error} /> : <Loading text="Creating the sandbox" />}
      </div>
      <Eng>
        <span className="mono">POST /api/run {'{'}physical: true{'}'}</span> uses <span className="mono">storageguard.physical.PhysicalSession</span>: a
        {' '}<span className="mono">sg-sandbox-*</span> temp directory with a marker file; every write resolves the real path and refuses anything outside it. The session is closed (directory removed) after each run.
      </Eng>
    </>
  )
}


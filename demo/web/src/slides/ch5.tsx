// Chapter 5: results. Numbers from results/REPORT.md and aggregates.csv (exported to public/data/results.json).
import { motion } from 'framer-motion'
import { useEffect, useMemo, useState } from 'react'
import { Bays, HBars, Heatmap, Lines, Scatter, rateBays } from '../components/charts'
import { Eng, Loading, OriginTag, Seg, SlideHead, Term } from '../components/ui'
import { TRAP } from '../content/text'
import { fetchManifest, fetchResults, fetchSweep, useLoad } from '../lib/api'
import { CONFIGS, MAIN_MODELS, MODELS, REPORT_CONFIG } from '../lib/plain'
import type { Sweep } from '../lib/types'
import type { SlideProps } from './types'

const reveal = (on: boolean, delay = 0) => ({
  initial: false as const,
  animate: { opacity: on ? 1 : 0, y: on ? 0 : 10 },
  transition: { duration: 0.4, delay: on ? delay : 0 },
})

function mainRow(res: any, model: string, config: string) {
  return res.main_table.find((r: any) => r.model === model && REPORT_CONFIG[r.config] === config)
}

function ModelSeg({ model, setModel }: { model: string; setModel: (m: string) => void }) {
  return <Seg value={model} onChange={setModel} options={MAIN_MODELS.map((m) => ({ v: m, label: MODELS[m].label }))} />
}

// ---------------------------------------------------------------- 5.1 headline
export function Headline({ build }: SlideProps) {
  const { data: res } = useLoad(fetchResults, [])
  const [model, setModel] = useState('llama3.1-8b')
  if (!res) return <Loading />
  const b0 = mainRow(res, model, 'noguard')
  const b4 = mainRow(res, model, 'storageguard')
  const shelf = (r: any, label: string, on: boolean, brand = false) => (
    <motion.div className="col" style={{ gap: 10, flex: 1 }} {...reveal(on)}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 14 }}>
        <span style={{ fontFamily: 'var(--cond)', fontSize: 30, fontWeight: 600, color: brand ? 'var(--brand-ink)' : 'var(--ink)' }}>{label}</span>
      </div>
      <Bays states={rateBays(r.uar, r.tsr)} cols={20} bayHeight={30} animateKey={on ? `${model}-${label}` : undefined} label={`${label}: ${r.uar}% unsafe, ${r.tsr}% completed`} />
      <div className="row" style={{ gap: 30 }}>
        <div><span style={{ fontFamily: 'var(--cond)', fontSize: 46, fontWeight: 600, color: '#f07b7f' }}>{Math.round(r.uar)}</span> <span className="ink2">did harm</span></div>
        <div><span style={{ fontFamily: 'var(--cond)', fontSize: 46, fontWeight: 600, color: '#5fd1a0' }}>{Math.round(r.tsr)}</span> <span className="ink2">completed safely</span></div>
      </div>
    </motion.div>
  )
  return (
    <>
      <SlideHead title="Out of 100 tickets, how many went wrong?" lede={<>Each bay is one ticket from the test set. Red: the agent did harm (the <Term id="uar">unsafe action rate</Term>). Green: the ticket was done correctly with no harm (<Term id="tsr">task success</Term>). Dark: safe, but not completed.</>} />
      <div className="row" style={{ alignItems: 'center', gap: 16 }}>
        <span className="small muted">Agent</span>
        <ModelSeg model={model} setModel={setModel} />
      </div>
      <div className="row grow" style={{ gap: 40 }}>
        {shelf(b0, 'No guard', build >= 0)}
        {shelf(b4, 'StorageGuard', build >= 1, true)}
      </div>
      <motion.div {...reveal(build >= 2)} className="panel" style={{ display: 'flex', gap: 40, alignItems: 'center' }}>
        <div style={{ fontSize: 21, maxWidth: 820 }}>
          Across all three agents, harm fell from <b>40–51%</b> of tickets to <b>0–2%</b>, and completed tickets rose from <b>34–48%</b> to <b>56–92%</b>. Safer, and more useful: the guard explains itself, so agents find a safe way to finish.
        </div>
        <table className="tbl" style={{ width: 460 }}>
          <thead><tr><th>Agent</th><th>Harm, no guard → guard</th><th>Completed</th></tr></thead>
          <tbody>
            {MAIN_MODELS.map((m) => {
              const a = mainRow(res, m, 'noguard'); const b = mainRow(res, m, 'storageguard')
              return <tr key={m} className={m === model ? 'hl' : ''}><td>{MODELS[m].label}</td><td>{a.uar}% → {b.uar}%</td><td>{a.tsr}% → {b.tsr}%</td></tr>
            })}
          </tbody>
        </table>
      </motion.div>
      <Eng>Test split, 104 tickets × 3 runs = 312 episodes per agent and method; 95% task-clustered bootstrap intervals for {MODELS[model].label}: no guard {b0.uar_ci?.join('–')}%, StorageGuard {b4.uar_ci?.join('–')}% unsafe. Paired bootstrap against no guard p &lt; 0.0001.</Eng>
    </>
  )
}

// ---------------------------------------------------------------- 5.2 against the alternatives (+ live thresholds)
const CFG_ORDER = ['noguard', 'prompt_only', 'llm_judge', 'rbac', 'storageguard']
const ALLOWS = [0.05, 0.1, 0.15, 0.2, 0.25, 0.35, 0.5]
const BLOCKS = [0.4, 0.55, 0.7, 0.85, 0.95]

export function Alternatives() {
  const { data: res } = useLoad(fetchResults, [])
  const { data: manifest } = useLoad(fetchManifest, [])
  const [model, setModel] = useState('llama3.1-8b')
  const [ta, setTa] = useState(0.15)
  const [tb, setTb] = useState(0.55)
  const [cur, setCur] = useState<Sweep | null>(null)
  const [busy, setBusy] = useState(false)
  const [grid, setGrid] = useState<Sweep[]>([])
  const [refs, setRefs] = useState<{ noguard?: Sweep; rbac?: Sweep }>({})
  useEffect(() => {
    if (!manifest) return
    Promise.all(manifest.sweep.filter((k) => k.startsWith('storageguard__0')).map((k) => fetch(`/data/sweep/${k}.json`).then((r) => r.json()))).then(setGrid)
    Promise.all(['noguard', 'rbac'].map((k) => fetch(`/data/sweep/${k}.json`).then((r) => r.json()))).then(([n, r]) => setRefs({ noguard: n, rbac: r }))
  }, [manifest])
  useEffect(() => {
    let alive = true
    setBusy(true)
    // show the recorded result at once, then replace it with the live computation (about 3 s)
    fetch(`/data/sweep/storageguard__${ta}__${tb}.json`).then((r) => (r.ok ? r.json() : null)).then((d) => {
      if (alive && d) setCur((c) => (c && c.origin === 'live' && c.tau_allow === ta && c.tau_block === tb ? c : { ...d, origin: 'recorded' }))
    }).catch(() => {})
    fetchSweep('storageguard', { tau_allow: ta, tau_block: tb }).then((s) => alive && (setCur(s), setBusy(false))).catch(() => alive && setBusy(false))
    return () => { alive = false }
  }, [ta, tb])
  const pts = useMemoPts(grid, refs, cur)
  if (!res) return <Loading />
  const rows = CFG_ORDER.map((c) => ({ c, r: mainRow(res, model, c) }))
  return (
    <>
      <SlideHead title="Safer than human approval, with a fraction of the asks." />
      <div className="row grow" style={{ gap: 20 }}>
        <div className="panel col" style={{ width: 760, gap: 10 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div className="panel-title" style={{ margin: 0 }}>Real AI runs, recorded</div>
            <ModelSeg model={model} setModel={setModel} />
          </div>
          <div className="small muted">Tickets where the agent did harm</div>
          <HBars data={rows.map(({ c, r }) => ({ label: CONFIGS[c].label, value: r.uar, ci: r.uar_ci, hl: c === 'storageguard' }))} width={700} max={60} rowH={34} labelW={250} title="Unsafe tickets" />
          <div className="small muted">How often a human is asked, per ticket</div>
          <HBars data={rows.map(({ c, r }) => ({ label: CONFIGS[c].label, value: r.esc, hl: c === 'storageguard' }))} width={700} max={1.5} unit="" digits={2} rowH={34} labelW={250} title="Human asks per ticket" />
          <div className="small ink2">Asking a human about every delete is the closest competitor, and it only works because our simulated approver is perfect. With an approver who says yes to everything, it is as unsafe as no guard at all (50% for Qwen2.5 7B); StorageGuard stays at 12.5%.</div>
        </div>
        <div className="panel col grow" style={{ gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div className="panel-title" style={{ margin: 0 }}>Move the <Term id="threshold">thresholds</Term></div>
            <OriginTag origin={cur?.origin} />
            {busy && <span className="spin" />}
          </div>
          <div className="small ink2">Re-runs the careless and the correct plan of all 104 test tickets through the guard.</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <div className="field">
              <label>Ask a human from risk <b style={{ color: 'var(--ink)' }}>{ta}</b></label>
              <input className="slider" type="range" min={0} max={ALLOWS.length - 1} value={ALLOWS.indexOf(ta)} onChange={(e) => { const v = ALLOWS[Number(e.target.value)]; setTa(v); if (tb <= v) setTb(BLOCKS.find((b) => b > v)!) }} />
            </div>
            <div className="field">
              <label>Refuse from risk <b style={{ color: 'var(--ink)' }}>{tb}</b></label>
              <input className="slider" type="range" min={0} max={BLOCKS.length - 1} value={BLOCKS.indexOf(tb)} onChange={(e) => { const v = BLOCKS[Number(e.target.value)]; if (v > ta) setTb(v) }} />
            </div>
          </div>
          {cur && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
              <Tile value={`${(cur.naive.harm_rate * 100).toFixed(1)}%`} label="careless plans of trap tickets still cause harm" bad={cur.naive.harm_rate > 0} />
              <Tile value={`${(cur.reference.success_rate * 100).toFixed(0)}%`} label="correct plans still go through" />
              <Tile value={((cur.naive.asks_per_task + cur.reference.asks_per_task) / 2).toFixed(2)} label="human asks per ticket (average of both plans)" />
            </div>
          )}
          <div className="grow" style={{ minHeight: 0 }}>
            <Scatter data={pts} width={640} height={300} xMax={1.5} yMax={100} xLabel="Human asks per ticket (careless plans)" yLabel="Careless plans causing harm (%)" title="Threshold trade-off" />
          </div>
          <div className="tiny muted">Grey dots: the other threshold settings. The default (0.15, 0.55) was tuned on the separate practice tickets only.</div>
        </div>
      </div>
    </>
  )
}

function useMemoPts(grid: Sweep[], refs: { noguard?: Sweep; rbac?: Sweep }, cur: Sweep | null) {
  return useMemo(() => {
    const out = grid
      .filter((g) => !cur || g.tau_allow !== cur.tau_allow || g.tau_block !== cur.tau_block)
      .map((g) => ({ label: '', x: g.naive.asks_per_task, y: g.naive.harm_rate * 100, dim: true }))
    if (refs.rbac) out.push({ label: 'Ask a human before any delete', x: refs.rbac.naive.asks_per_task, y: refs.rbac.naive.harm_rate * 100, dim: false, dx: -10, anchor: 'end' } as any)
    if (refs.noguard) out.push({ label: 'No guard', x: refs.noguard.naive.asks_per_task, y: refs.noguard.naive.harm_rate * 100, dim: false, dy: 16 } as any)
    if (cur) out.push({ label: `StorageGuard (${cur.tau_allow}, ${cur.tau_block})`, x: cur.naive.asks_per_task, y: cur.naive.harm_rate * 100, hl: true, dy: -22, dx: 6 } as any)
    return out
  }, [grid, refs, cur])
}

function Tile({ value, label, bad }: { value: string; label: string; bad?: boolean }) {
  return (
    <div style={{ borderTop: '1px solid var(--line-2)', paddingTop: 8 }}>
      <div style={{ fontFamily: 'var(--cond)', fontSize: 40, fontWeight: 600, lineHeight: 1, color: bad ? '#f07b7f' : 'var(--ink)' }}>{value}</div>
      <div className="small ink2">{label}</div>
    </div>
  )
}

// ---------------------------------------------------------------- 5.3 deep dives (detour)
const TABS = ['What matters most', 'Bigger AIs', 'Planted instructions', 'Does explaining help', 'Speed', 'Trap by trap']

export function DeepDives() {
  const { data: res } = useLoad(fetchResults, [])
  const [tab, setTab] = useState(0)
  if (!res) return <Loading />
  return (
    <>
      <SlideHead title="Deeper cuts through the results." />
      <div className="tabs">{TABS.map((t, i) => <button key={t} className={i === tab ? 'on' : ''} onClick={() => setTab(i)}>{t}</button>)}</div>
      <div className="grow" style={{ minHeight: 0 }}>
        {tab === 0 && <Ablations res={res} />}
        {tab === 1 && <Scale res={res} />}
        {tab === 2 && <Injection res={res} />}
        {tab === 3 && <Feedback res={res} />}
        {tab === 4 && <Speed res={res} />}
        {tab === 5 && <TrapHeat res={res} />}
      </div>
    </>
  )
}

const ABL_LABEL: Record<string, string> = {
  'B4 StorageGuard': 'Full StorageGuard',
  'A1 one-hop': 'Sees only direct neighbours',
  'A2 no reversibility': 'Ignores how undoable a change is',
  'A3 no rewrites': 'Never suggests a safer version',
  'A4 no feedback': 'Says only “denied”',
}

function Ablations({ res }: { res: any }) {
  const rows = res.ablations
  return (
    <div className="row" style={{ gap: 22, paddingTop: 16, alignItems: 'flex-start' }}>
      <div className="panel">
        <div className="panel-title">Unsafe tickets when one part is removed (Qwen2.5 7B)</div>
        <HBars data={rows.map((r: any) => ({ label: ABL_LABEL[r.config] || r.config, value: r.uar, hl: r.config.startsWith('B4') }))} width={520} max={30} labelW={240} rowH={40} />
      </div>
      <div className="panel">
        <div className="panel-title">Tickets completed</div>
        <HBars data={rows.map((r: any) => ({ label: ABL_LABEL[r.config] || r.config, value: r.tsr, hl: r.config.startsWith('B4') }))} width={520} max={80} labelW={240} rowH={40} />
      </div>
      <div className="col" style={{ gap: 12, width: 330, flex: 'none' }}>
        <div style={{ fontSize: 20 }}>Following the links all the way is what matters. Limited to direct neighbours, the context an AI checker gets, harm jumps from <b>1%</b> to <b>24%</b>.</div>
        <div className="ink2">Rewrites trade friction for success: without them, more asks and fewer completed tickets.</div>
      </div>
    </div>
  )
}

function Scale({ res }: { res: any }) {
  const rows = res.scale.filter((r: any) => r.model !== 'qwen2.5-7b-awq')
  const xs = rows.map((r: any) => r.size)
  return (
    <div className="row" style={{ gap: 22, paddingTop: 16, alignItems: 'flex-start' }}>
      <div className="panel">
        <div className="panel-title">Unsafe tickets by model size: <Term id="params">Qwen2.5, 7B to 72B</Term></div>
        <Lines xs={xs} width={540} height={330} yMax={60} series={[
          { label: 'No guard', values: rows.map((r: any) => r.B0_uar), color: 'var(--ink-3)' },
          { label: 'AI checker', values: rows.map((r: any) => r.B3_uar), color: 'var(--ink-2)', hollow: true },
          { label: 'StorageGuard', values: rows.map((r: any) => r.B4_uar), color: 'var(--brand)' },
        ]} />
      </div>
      <div className="panel">
        <div className="panel-title">Tickets completed</div>
        <Lines xs={xs} width={540} height={330} yMax={100} series={[
          { label: 'No guard', values: rows.map((r: any) => r.B0_tsr), color: 'var(--ink-3)' },
          { label: 'AI checker', values: rows.map((r: any) => r.B3_tsr), color: 'var(--ink-2)', hollow: true },
          { label: 'StorageGuard', values: rows.map((r: any) => r.B4_tsr), color: 'var(--brand)' },
        ]} />
      </div>
      <div className="col" style={{ width: 300, gap: 10, fontSize: 18, flex: 'none' }}>
        <div>Bigger models get better at unclear tickets, but not at hidden dependencies. Without a guard, trap tickets stay <b>73–87%</b> unsafe at every size.</div>
        <div className="ink2">The AI checker improves sharply with size; StorageGuard still finishes more tickets at every size.</div>
      </div>
    </div>
  )
}

function Injection({ res }: { res: any }) {
  return (
    <div className="row" style={{ gap: 16, paddingTop: 16, alignItems: 'flex-start' }}>
      {MAIN_MODELS.map((m) => (
        <div key={m} className="panel">
          <div className="panel-title">{MODELS[m].label}</div>
          <HBars data={CFG_ORDER.map((c) => {
            const r = res.injection.find((x: any) => x.model === m && REPORT_CONFIG[x.config] === c)
            return { label: CONFIGS[c].short, value: r.uar, hl: c === 'storageguard' }
          })} width={330} max={100} labelW={118} rowH={38} />
        </div>
      ))}
      <div className="col" style={{ gap: 10, fontSize: 18, width: 300, flex: 'none' }}>
        <div className="small muted">Unsafe runs on the 15 planted-instruction tickets.</div>
        <div>Planted instructions fooled every agent in 82% of these tickets without a guard.</div>
        <div className="ink2">StorageGuard does not try to spot the planted text. The action it asks for is harmful, and that is what the dry run shows.</div>
      </div>
    </div>
  )
}

function Feedback({ res }: { res: any }) {
  const b4 = res.ablations.find((r: any) => r.config === 'B4 StorageGuard')
  const a4 = res.ablations.find((r: any) => r.config === 'A4 no feedback')
  const rows: [string, string, string][] = [
    ['Unsafe tickets', `${b4.uar}%`, `${a4.uar}%`],
    ['Tickets completed', `${b4.tsr}%`, `${a4.tsr}%`],
    ['Agent steps per ticket', `${b4.steps}`, `${a4.steps}`],
    ['Tokens per ticket', `${(b4.tokens / 1000).toFixed(1)}k`, `${(a4.tokens / 1000).toFixed(1)}k`],
    ['Human asks per ticket', `${b4.esc}`, `${a4.esc}`],
  ]
  const ORDER = ['llama3.1-8b', 'ministral-3-8b', 'qwen2.5-7b', 'qwen2.5-7b-awq', 'qwen2.5-14b', 'qwen2.5-32b-int8', 'qwen2.5-72b-awq']
  const fb = res.feedback.filter((r: any) => r.config.startsWith('B4')).sort((a: any, b: any) => ORDER.indexOf(a.model) - ORDER.indexOf(b.model))
  return (
    <div className="row" style={{ gap: 30, paddingTop: 16 }}>
      <div className="panel" style={{ width: 640 }}>
        <div className="panel-title">Explanations vs a bare “denied” (Qwen2.5 7B)</div>
        <table className="tbl"><thead><tr><th /><th>With reasons</th><th>Only “denied”</th></tr></thead>
          <tbody>{rows.map(([a, b, c]) => <tr key={a}><td>{a}</td><td>{b}</td><td>{c}</td></tr>)}</tbody></table>
        <div className="small ink2" style={{ marginTop: 10 }}>Safety is the same. Without reasons, the agent retries blindly: more steps, more tokens, more asks.</div>
      </div>
      <div className="panel">
        <div className="panel-title">After a stop, how often the agent still finished safely</div>
        <HBars data={fb.map((r: any) => ({ label: MODELS[r.model]?.label || r.model, value: r.safe_after_block }))} width={560} max={100} labelW={190} rowH={32} />
        <div className="small ink2">Recovering is mostly the model’s ability: 51–59% for the 7–8B models, 87–93% from 14B up.</div>
      </div>
    </div>
  )
}

function Speed({ res }: { res: any }) {
  const l = res.latency
  const j = res.judge_calls
  return (
    <div className="row" style={{ gap: 40, paddingTop: 30 }}>
      {[['median', l.median], ['95th percentile', l.p95], ['99th percentile', l.p99]].map(([k, v]) => (
        <div key={k as string}>
          <div style={{ fontFamily: 'var(--cond)', fontSize: 88, fontWeight: 600, lineHeight: 1 }}>{v} ms</div>
          <div className="ink2">{k} decision time</div>
        </div>
      ))}
      <div className="col" style={{ gap: 10, maxWidth: 420, fontSize: 19 }}>
        <div>Over {l.calls.toLocaleString()} guarded calls, measured inside the experiment with other episodes sharing the CPU. No model call, no GPU.</div>
        <div className="ink2">The AI checker needed {Math.min(...j.map((x: any) => x.calls))}–{Math.max(...j.map((x: any) => x.calls))} extra model calls per ticket.</div>
      </div>
    </div>
  )
}

function TrapHeat({ res }: { res: any }) {
  const models = ['llama3.1-8b', 'ministral-3-8b', 'qwen2.5-7b', 'qwen2.5-7b-awq', 'qwen2.5-14b', 'qwen2.5-32b-int8', 'qwen2.5-72b-awq']
  const rows = res.heatmap.map((r: any) => r.trap)
  return (
    <div className="row" style={{ gap: 20, paddingTop: 10 }}>
      <div className="panel" style={{ padding: '10px 14px' }}>
        <Heatmap rows={rows} cols={models} width={1100} cellH={29} labelW={430}
          value={(r, c) => res.heatmap.find((x: any) => x.trap === r)?.[c] ?? null}
          rowLabel={(r) => { const t = `${r} ${TRAP[r]?.name ?? ''}`; return t.length > 56 ? t.slice(0, 55).trimEnd() + '…' : t }} colLabel={(c) => MODELS[c]?.label.replace('Qwen2.5 ', 'Qwen ').replace(' (4-bit)', ' 4-bit') ?? c} />
      </div>
      <div className="col" style={{ fontSize: 18, gap: 10 }}>
        <div>Unsafe runs without a guard, by trap and model.</div>
        <div className="ink2">Restores (T7, T8) fooled every model every time. Taking a spare from a rebuilding pool (T6) and deleting backups (T3) were close behind.</div>
      </div>
    </div>
  )
}

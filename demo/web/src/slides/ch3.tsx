// Chapter 3: how StorageGuard works.
import { motion } from 'framer-motion'
import { EstateMap, MapLegend } from '../components/EstateMap'
import { Eng, ErrorNote, Loading, RiskMeter, SlideHead, Stamp, Term, statusText } from '../components/ui'
import { ticketKind } from '../content/text'
import { fetchLiveRun, useLoad } from '../lib/api'
import { ANALYZER, INVARIANTS, REVERSIBILITY, nameLookup, plainCall, rawCall } from '../lib/plain'
import type { Run, Step } from '../lib/types'
import { HOOK_TASK } from './ch1'
import type { SlideProps } from './types'

const reveal = (on: boolean, delay = 0) => ({
  initial: false as const,
  animate: { opacity: on ? 1 : 0, y: on ? 0 : 10 },
  transition: { duration: 0.4, delay: on ? delay : 0 },
})

// ---------------------------------------------------------------- 3.1 the idea
export function Idea({ build }: SlideProps) {
  const box = (x: number, y: number, w: number, h: number, title: string, sub: string, on: boolean, brand = false) => (
    <g style={{ opacity: on ? 1 : 0.18, transition: 'opacity 0.5s' }}>
      <rect x={x} y={y} width={w} height={h} rx={8} fill={brand ? '#22162a' : 'var(--panel-2)'} stroke={brand ? 'var(--brand)' : 'var(--line-2)'} strokeWidth={brand ? 2 : 1.2} />
      <text x={x + 20} y={y + 38} style={{ fill: 'var(--ink)', fontSize: 26, fontFamily: 'var(--cond)', fontWeight: 600 }}>{title}</text>
      <text x={x + 20} y={y + 66} style={{ fill: 'var(--ink-2)', fontSize: 17 }}>{sub}</text>
    </g>
  )
  const arrow = (x1: number, y1: number, x2: number, y2: number, on: boolean, label?: string, color = 'var(--ink-2)') => (
    <g style={{ opacity: on ? 1 : 0, transition: 'opacity 0.5s' }}>
      <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={color} strokeWidth={2} markerEnd="url(#a31)" />
      {label && <text x={(x1 + x2) / 2} y={(y1 + y2) / 2 - 10} textAnchor="middle" style={{ fill: 'var(--ink-2)', fontSize: 16 }}>{label}</text>}
    </g>
  )
  return (
    <>
      <SlideHead title="Try it on a copy first." lede={<>StorageGuard is <Term id="middleware">middleware</Term>: every change the agent asks for passes through it. Before anything touches the real storage, it does a <Term id="dryrun">dry run</Term> on a copy, like a dress rehearsal.</>} />
      <div className="panel grow" style={{ position: 'relative' }}>
        <svg viewBox="0 0 1440 520" width="100%" height="100%">
          <defs><marker id="a31" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="var(--ink-2)" /></marker></defs>
          {box(30, 60, 300, 100, 'AI agent', 'proposes delete_volume(arch-fad9)', true)}
          {box(560, 60, 320, 100, 'StorageGuard', 'checks every change', build >= 0, true)}
          {box(1110, 60, 300, 100, 'Real storage', 'untouched until the guard says so', build >= 2)}
          {box(560, 320, 320, 110, 'Copy of the storage', 'the change runs here first', build >= 1)}
          {arrow(330, 110, 556, 110, true, 'the call')}
          {arrow(720, 160, 720, 316, build >= 1, '')}
          <text x={736} y={245} style={{ fill: 'var(--ink-2)', fontSize: 16, opacity: build >= 1 ? 1 : 0, transition: 'opacity .5s' }}>run it on the copy, see what breaks</text>
          {arrow(880, 110, 1106, 110, build >= 2, 'only if it is safe')}
          <g style={{ opacity: build >= 2 ? 1 : 0, transition: 'opacity 0.5s' }}>
            <path d="M 560 120 C 460 200, 420 240, 330 160" fill="none" stroke="var(--brand)" strokeWidth={2} markerEnd="url(#a31)" />
            <text x={360} y={250} style={{ fill: 'var(--brand-ink)', fontSize: 16 }}>or: a reason, and a safer way to do it</text>
          </g>
        </svg>
      </div>
      <motion.div {...reveal(build >= 2)} className="row" style={{ gap: 40, fontSize: 20 }}>
        <div><b>No AI inside the guard.</b> <span className="ink2">The same call always gets the same answer.</span></div>
        <div><b>About 5 milliseconds</b> <span className="ink2">per decision, on a laptop CPU.</span></div>
      </motion.div>
    </>
  )
}

// ---------------------------------------------------------------- 3.2 the pipeline
const STAGES = [
  { n: 1, t: 'Dry run' },
  { n: 2, t: 'Blast radius' },
  { n: 3, t: 'Impact checks' },
  { n: 4, t: 'Risk' },
  { n: 5, t: 'Hard rules' },
  { n: 6, t: 'Decision' },
]

function riskParts(g: NonNullable<Step['guard']>) {
  const sev = Object.values(g.severities || {})
  const survive = sev.reduce((p, s) => p * (1 - Math.min(1, s)), 1)
  const f = REVERSIBILITY[g.rclass ?? 0]?.f ?? 1
  return { noisy: 1 - survive, f }
}

export function Pipeline({ build, setBuild }: SlideProps) {
  const { data: run, error } = useLoad(() => fetchLiveRun(HOOK_TASK, 'scripted:naive', 'storageguard'), [])
  if (error) return <ErrorNote msg={error} />
  if (!run) return <Loading />
  const step = run.steps.find((s) => s.guard?.decision === 'BLOCK') || run.steps.find((s) => s.guard)!
  const g = step.guard!
  const name = nameLookup(run.map)
  const imp = g.impacted || []
  const st = Math.min(build, 5)
  const destroyedIds = imp.filter((i) => i.effect === 'destroyed').map((i) => i.id)
  const highlight =
    st === 0 ? { targets: g.targets, removed: destroyedIds }
      : st === 1 ? { targets: g.targets, impacted: imp }
        : { targets: g.targets, impacted: imp }
  const fb = step.result?.data || {}
  const { noisy, f } = riskParts(g)
  return (
    <>
      <SlideHead title="Six steps, in about 5 milliseconds." lede={<>Following the careless plan’s last call on the ticket you saw: <b style={{ color: 'var(--ink)' }}>{plainCall(step, name)}</b>.</>} />
      <div className="stagebar">
        {STAGES.map((s, i) => (
          <button key={s.n} className={i === st ? 'on' : i < st ? 'done' : ''} onClick={() => setBuild(i)}>
            <span className="n">{s.n}</span><span className="t">{s.t}</span>
          </button>
        ))}
      </div>
      <div className="row grow" style={{ gap: 20 }}>
        <div className="panel flush" style={{ width: 880, position: 'relative' }}>
          <EstateMap data={run.map} width={900} height={540} highlight={highlight} rippleKey={`${st}`} copyMark={st === 0} dimOthers={st >= 1} />
        </div>
        <motion.div key={st} className="panel col grow" style={{ gap: 12 }} initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.25 }}>
          {st === 0 && (
            <>
              <h3 style={{ fontFamily: 'var(--cond)', fontSize: 30, margin: 0 }}>Run it on a copy</h3>
              <div style={{ fontSize: 19 }}>The guard clones the whole estate and runs <b>{plainCall(step, name).toLowerCase()}</b> on the clone. The real storage is not touched.</div>
              <div className="ink2">On the copy, {destroyedIds.length} things disappear: {destroyedIds.map(name).join(', ')}.</div>
              <Eng>Clone and execute: <span className="mono">StorageGuard.assess()</span> calls <span className="mono">twin.clone()</span>, then <span className="mono">trial.execute(tool, args)</span>, and captures the public state before and after.</Eng>
            </>
          )}
          {st === 1 && (
            <>
              <h3 style={{ fontFamily: 'var(--cond)', fontSize: 30, margin: 0 }}>Follow the links</h3>
              <div style={{ fontSize: 19 }}>From what the call touches, the guard walks the dependency map outwards. This is the <Term id="blast">blast radius</Term>.</div>
              <div className="col scroll" style={{ gap: 6 }}>
                {imp.filter((i) => i.hops > 0).slice(0, 9).map((i) => (
                  <div key={i.id} style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 8, fontSize: 16 }}>
                    <span>{name(i.id)} <span className="muted small">{i.kind.replace(/_/g, ' ')}</span></span>
                    <span className="small" style={{ color: i.effect === 'destroyed' ? '#f07b7f' : 'var(--ink-2)' }}>{i.effect === 'destroyed' ? 'destroyed' : 'loses protection'}, {i.hops} away</span>
                  </div>
                ))}
              </div>
              <Eng>{imp.filter((i) => i.hops >= 2).slice(0, 2).map((i) => <div key={i.id} className="mono" style={{ fontSize: 12 }}>{i.path}</div>)}</Eng>
            </>
          )}
          {st === 2 && (
            <>
              <h3 style={{ fontFamily: 'var(--cond)', fontSize: 30, margin: 0 }}>Seven <Term id="analysers">impact checks</Term></h3>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                {Object.entries(ANALYZER).map(([k, label]) => {
                  const v = g.severities[k] || 0
                  return (
                    <div key={k} style={{ border: '1px solid', borderColor: v ? 'var(--block)' : 'var(--line)', borderRadius: 5, padding: '7px 10px', background: v ? 'var(--block-wash)' : 'transparent' }}>
                      <div style={{ fontSize: 16, display: 'flex', justifyContent: 'space-between' }}><span>{label}</span><span className="num" style={{ color: v ? '#f07b7f' : 'var(--ink-3)' }}>{v ? v.toFixed(2) : '–'}</span></div>
                    </div>
                  )
                })}
              </div>
              <ul className="findings">{g.findings.slice(0, 4).map((x, i) => <li key={i} style={{ fontSize: 15 }}>{x.detail}</li>)}</ul>
            </>
          )}
          {st === 3 && (
            <>
              <h3 style={{ fontFamily: 'var(--cond)', fontSize: 30, margin: 0 }}>How bad, and can it be undone?</h3>
              <div className="col" style={{ gap: 4 }}>
                {REVERSIBILITY.map((r, i) => (
                  <div key={r.r} style={{ display: 'grid', gridTemplateColumns: '40px 1fr', gap: 10, padding: '4px 8px', borderRadius: 4, background: i === g.rclass ? 'var(--block-wash)' : 'transparent', color: i === g.rclass ? 'var(--ink)' : 'var(--ink-3)', fontSize: 16 }}>
                    <b>{r.r}</b><span>{r.plain} <span className="small">({r.eg})</span></span>
                  </div>
                ))}
              </div>
              <div style={{ fontSize: 18 }}>This call is <b>{REVERSIBILITY[g.rclass ?? 0].r}</b>. Combined with how bad the damage is and how important the app is, the <Term id="risk">risk score</Term> is <b>{g.risk?.toFixed(2)}</b>.</div>
              <RiskMeter risk={g.risk || 0} />
              <Eng title="Engineer view: the formula">
                <div className="mono" style={{ fontSize: 13 }}>risk = f(R) × (1 − Π<sub>c</sub>(1 − w<sub>c</sub>·sev<sub>c</sub>))</div>
                <div className="mono" style={{ fontSize: 13 }}>= {f.toFixed(2)} × {noisy.toFixed(4)} = {g.risk?.toFixed(4)}</div>
                <div style={{ marginTop: 4 }}>sev<sub>c</sub>: worst finding per check, already weighted by tier (1.0 / 0.6 / 0.3). Thresholds tuned on the separate practice tickets only.</div>
              </Eng>
            </>
          )}
          {st === 4 && (
            <>
              <h3 style={{ fontFamily: 'var(--cond)', fontSize: 30, margin: 0 }}>Seven <Term id="invariant">hard rules</Term></h3>
              <div className="ink2">Never broken, whatever the score.</div>
              <div className="col" style={{ gap: 5 }}>
                {Object.entries(INVARIANTS).map(([k, v]) => {
                  const hit = g.invariants.includes(k)
                  return (
                    <div key={k} style={{ display: 'grid', gridTemplateColumns: '34px 1fr', gap: 8, fontSize: 16, padding: '4px 8px', borderRadius: 4, background: hit ? 'var(--block-wash)' : 'transparent', color: hit ? 'var(--ink)' : 'var(--ink-3)' }}>
                      <b>{k}</b><span>{v}{hit && <b style={{ color: '#f07b7f' }}> Broken by this call.</b>}</span>
                    </div>
                  )
                })}
              </div>
            </>
          )}
          {st === 5 && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                <Stamp d={g.decision} big />
                <span className="muted">decided in {g.latency_ms?.toFixed(1)} ms</span>
              </div>
              <div style={{ fontSize: 18 }}>{statusText(g.status)[0].toUpperCase() + statusText(g.status).slice(1)}. The agent gets the reasons and a safer way forward, so it can re-plan:</div>
              <div className="panel" style={{ background: 'var(--panel-2)' }}>
                <div className="tiny muted">Told to the agent</div>
                <ul className="findings">{(fb.reasons || []).slice(0, 3).map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
                {(fb.suggested_alternatives || []).slice(0, 2).map((a: string, i: number) => <div key={i} className="small" style={{ marginTop: 6, color: 'var(--brand-ink)' }}>Instead: {a}</div>)}
              </div>
              <div className="small ink2">Below 0.15 the guard allows a call. From 0.15 it asks a human or rewrites the call; from 0.55, or when a hard rule breaks, it refuses.</div>
            </>
          )}
          <div style={{ marginTop: 'auto' }}><MapLegend /></div>
        </motion.div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------- 3.3 four answers
const EXAMPLES: { task: string; agent: 'scripted:naive' | 'scripted:reference'; d: 'ALLOW' | 'REWRITE' | 'ESCALATE' | 'BLOCK'; when: string }[] = [
  { task: 'task-0204356a', agent: 'scripted:reference', d: 'ALLOW', when: 'Risk below 0.15: nothing important at stake.' },
  { task: 'task-77d1d85f', agent: 'scripted:naive', d: 'REWRITE', when: 'A safer version does the same job with low risk.' },
  { task: 'task-66b78890', agent: 'scripted:naive', d: 'ESCALATE', when: 'Risky but not forbidden: a person decides.' },
  { task: 'task-ffaa2f46', agent: 'scripted:naive', d: 'BLOCK', when: 'Risk 0.55 or more, or a hard rule broken.' },
]

function ExampleCard({ ex }: { ex: (typeof EXAMPLES)[number] }) {
  const { data: run, error } = useLoad(() => fetchLiveRun(ex.task, ex.agent, 'storageguard'), [ex.task])
  if (error) return <div className="panel"><ErrorNote msg={error} /></div>
  if (!run) return <div className="panel"><Loading /></div>
  const s = run.steps.find((x) => x.guard?.decision === ex.d)!
  return <AnswerCard run={run} s={s} when={ex.when} />
}

function AnswerCard({ run, s, when }: { run: Run; s: Step; when: string }) {
  const g = s.guard!
  const name = nameLookup(run.map)
  const fb = s.result?.data || {}
  const told: string | undefined = g.rewrite ? `Rewritten to: ${g.rewrite.steps.map((x) => plainCall(x, name)).join(', then ')}.` : fb.suggested_alternatives?.[0]
  return (
    <div className="panel col" style={{ flex: 1, gap: 12 }}>
      <Stamp d={g.decision} big />
      <div className="small muted">{when}</div>
      <div className="tiny muted">{ticketKind(run.task.category, run.task.trap)}</div>
      <div>
        <div style={{ fontSize: 19, fontWeight: 600 }}>{plainCall(s, name)}</div>
        <div className="mono muted" style={{ fontSize: 12 }}>{rawCall(s)}</div>
      </div>
      <div className="ink2" style={{ fontSize: 16 }}>{g.findings[0]?.detail || 'No harmful effect found on the copy.'}</div>
      <div style={{ fontSize: 16 }}><span className="muted">Result: </span>{statusText(g.status)}{g.human ? ` (human ${g.human})` : ''}</div>
      {told && <div style={{ marginTop: 'auto', borderTop: '1px solid var(--line)', paddingTop: 10, fontSize: 15, color: 'var(--brand-ink)' }}>{told}</div>}
      <div className="tiny muted">risk {g.risk?.toFixed(2)}{g.invariants.length ? `, hard rule ${g.invariants.join(', ')}` : ''}</div>
    </div>
  )
}

export function FourAnswers() {
  return (
    <>
      <SlideHead title="Four possible answers, each with a reason the agent can use." lede={<>Real decisions from the test set. A <Term id="rewrite">rewrite</Term> keeps the job moving; asking a human is saved for calls that need judgement.</>} />
      <div className="row grow" style={{ gap: 14 }}>
        {EXAMPLES.map((ex) => <ExampleCard key={ex.d} ex={ex} />)}
      </div>
    </>
  )
}

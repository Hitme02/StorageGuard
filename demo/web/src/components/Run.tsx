// Step-by-step view of one episode: what the agent asked, what the guard decided, what happened.
import { useEffect, useMemo, useRef, useState } from 'react'
import { ANALYZER, CONFIGS, MODELS, harmLabel, nameLookup, plainCall, rawCall, type NameLookup } from '../lib/plain'
import type { Run, Step } from '../lib/types'
import type { MapHighlight } from './EstateMap'
import { RiskMeter, Stamp, statusText } from './ui'

const GUARDED = new Set(['storageguard', 'a1_one_hop', 'a2_no_reversibility', 'a3_no_rewrites', 'a4_no_feedback', 'p1_pit_check', 'rbac', 'llm_judge'])

export function isGuarded(config: string) {
  return GUARDED.has(config.split('__')[0])
}

/** Index of the steps worth showing (reads are folded unless asked for). */
export function visibleSteps(run: Run, showReads: boolean): number[] {
  return run.steps.map((s, i) => [s, i] as const).filter(([s]) => showReads || s.type !== 'read').map(([, i]) => i)
}

export function highlightFor(run: Run | null, cursor: number): MapHighlight {
  if (!run) return {}
  const removed: string[] = []
  const changed: string[] = []
  const end = Math.min(cursor, run.steps.length - 1)
  for (let i = 0; i <= end; i++) {
    const c = run.steps[i].changes
    if (!c) continue
    removed.push(...c.removed)
    changed.push(...c.changed.map((x) => x.id), ...c.unmapped.map(([, v]) => v), ...c.mapped.map(([, v]) => v))
  }
  const cur = cursor >= 0 ? run.steps[cursor] : undefined
  const g = cur?.guard
  const finished = cursor >= run.steps.length - 1
  return {
    targets: g?.targets || [],
    impacted: g?.impacted || [],
    removed,
    changed,
    harmed: finished ? run.outcome.harms.map((h) => h.entity) : [],
  }
}

/** Autoplay through the steps that change something. */
export function usePlayback(run: Run | null, opts: { autoplay?: boolean; interval?: number; showReads?: boolean } = {}) {
  const { autoplay = false, interval = 1500, showReads = false } = opts
  const idx = useMemo(() => (run ? visibleSteps(run, showReads) : []), [run, showReads])
  const [pos, setPos] = useState(-1)
  const [playing, setPlaying] = useState(autoplay)
  const timer = useRef<number | null>(null)
  useEffect(() => {
    setPos(autoplay ? 0 : idx.length - 1)
    setPlaying(autoplay)
  }, [run]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!playing) return
    timer.current = window.setTimeout(() => {
      if (pos < idx.length - 1) setPos((p) => p + 1)
      else setPlaying(false)
    }, interval)
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [playing, pos, idx.length, interval])
  const cursor = pos >= 0 && idx.length ? idx[Math.min(pos, idx.length - 1)] : -1
  return {
    cursor, pos, count: idx.length, playing, idx,
    play: () => { if (pos >= idx.length - 1) setPos(0); setPlaying(true) },
    pause: () => setPlaying(false),
    step: (d: number) => { setPlaying(false); setPos((p) => Math.max(0, Math.min(idx.length - 1, p + d))) },
    jumpTo: (stepIndex: number) => { setPlaying(false); const k = idx.indexOf(stepIndex); if (k >= 0) setPos(k) },
    end: () => { setPlaying(false); setPos(idx.length - 1) },
  }
}

function GuardDetail({ s, name, config }: { s: Step; name: NameLookup; config: string }) {
  const g = s.guard!
  const sg = config.startsWith('storageguard') || config.startsWith('a') || config.startsWith('p1')
  return (
    <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {sg && g.risk !== null && g.risk !== undefined && (
        <div style={{ display: 'grid', gridTemplateColumns: '78px 1fr', alignItems: 'center', gap: 10 }}>
          <span className="small muted">Risk <b className="num" style={{ color: 'var(--ink)' }}>{g.risk.toFixed(2)}</b></span>
          <RiskMeter risk={g.risk} short />
        </div>
      )}
      {g.findings.length > 0 && (
        <ul className="findings">
          {g.findings.slice(0, 3).map((f, i) => (
            <li key={i}><span className="ink2">{ANALYZER[f.analyzer] || f.analyzer}:</span> {f.detail}</li>
          ))}
        </ul>
      )}
      {g.invariants.length > 0 && (
        <div className="small"><span className="chip harm">Hard rule {g.invariants.join(', ')}</span></div>
      )}
      {g.rewrite && (
        <div className="small ink2">
          Safer version: {g.rewrite.steps.map((x) => plainCall(x, name)).join(', then ')}
        </div>
      )}
      {g.human && <div className="small ink2">Human approver: {g.human === 'approved' ? 'approved' : g.human === 'denied' ? 'denied, outside the ticket' : 'not available'}</div>}
      {g.would_harm !== undefined && (
        <div className="small" style={{ color: g.would_harm ? '#f07b7f' : 'var(--ink-3)' }}>
          {g.would_harm ? `Had it run: ${g.would_harm_codes?.map(harmLabel).join('; ')}` : 'Had it run: no harm (a needless stop)'}
        </div>
      )}
    </div>
  )
}

export function Trace({ run, cursor, onPick, showReads = false, detail = 'current' }: {
  run: Run; cursor: number; onPick?: (i: number) => void; showReads?: boolean; detail?: 'current' | 'all' | 'none'
}) {
  const name = useMemo(() => nameLookup(run.map), [run])
  const idx = visibleSteps(run, showReads)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current?.querySelector('.tstep.cur') as HTMLElement | null
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [cursor])
  const guarded = isGuarded(run.meta.config)
  const reads = run.steps.filter((s) => s.type === 'read').length
  let n = 0
  return (
    <div className="trace" ref={ref}>
      {!showReads && reads > 0 && <div className="tiny muted" style={{ padding: '0 10px' }}>{reads} look-up call{reads > 1 ? 's' : ''} hidden</div>}
      {idx.map((i) => {
        const s = run.steps[i]
        n += 1
        const isCur = i === cursor
        const future = cursor >= 0 && i > cursor
        const g = s.guard
        let right = null
        if (g) {
          right = guarded && !(g.decision === 'ALLOW' && run.meta.config === 'rbac' && !g.human)
            ? <Stamp d={g.decision} />
            : <Stamp none label={g.status === 'error' ? 'Rejected by storage' : 'Went straight through'} />
        } else if (s.type === 'finish') right = <span className="chip">Done</span>
        else if (s.type === 'ask') right = <span className="chip">Question</span>
        return (
          <div key={i} className={`tstep ${s.type}${isCur ? ' cur' : ''}${future ? ' future' : ''}`} onClick={() => onPick?.(i)}>
            <span className="k">{n}</span>
            <div>
              <div className="what">
                {s.type === 'finish' ? `Finish: “${String(s.args.summary || '').slice(0, 140)}”` : s.type === 'say' ? `Says: “${(s.text || '').slice(0, 140)}”` : plainCall(s, name)}
              </div>
              {s.type !== 'finish' && s.type !== 'say' && <div className="raw">{rawCall(s)}</div>}
              {g && (detail === 'all' || (detail === 'current' && isCur)) && (
                <>
                  {guarded && <div className="small ink2" style={{ marginTop: 4 }}>{statusText(g.status)}</div>}
                  {guarded && <GuardDetail s={s} name={name} config={run.meta.config} />}
                  {!guarded && s.changes && s.changes.removed.length > 0 && (
                    <div className="small" style={{ color: '#f07b7f', marginTop: 4 }}>Removed: {s.changes.removed.map(name).join(', ')}</div>
                  )}
                </>
              )}
              {s.type === 'ask' && isCur && s.result?.data?.answer && <div className="small ink2" style={{ marginTop: 4 }}>Answer: “{String(s.result.data.answer).slice(0, 220)}”</div>}
            </div>
            <div>{right}</div>
          </div>
        )
      })}
    </div>
  )
}

export function Verdict({ run, compact = false }: { run: Run; compact?: boolean }) {
  const o = run.outcome
  const name = nameLookup(run.map)
  const harmed = !!o.unsafe
  return (
    <div className="col" style={{ gap: 10 }}>
      <div className="muted small">Answer key</div>
      <div className={`verdict ${harmed ? 'harm' : 'safe'}`}>
        <span aria-hidden>{harmed ? '✕' : '✓'}</span>
        {harmed ? 'Harm done' : 'No harm'}
      </div>
      {harmed && (
        <ul className="findings">
          {o.harms.slice(0, compact ? 2 : 4).map((h, i) => (
            <li key={i}><span style={{ color: '#f07b7f' }}>{harmLabel(`${h.type}:${h.code}`)}</span>{!compact && <>: {name(h.entity)}</>}</li>
          ))}
        </ul>
      )}
      <div className="hr" />
      <div className="small" style={{ display: 'grid', gridTemplateColumns: '1fr auto', rowGap: 4, columnGap: 12 }}>
        <span className="muted">Ticket completed</span><span>{o.success ? 'Yes' : o.goal_met ? 'Goal met, but harm' : 'No'}</span>
        <span className="muted">Human asked</span><span className="num">{o.n_escalations}</span>
        <span className="muted">Refused</span><span className="num">{o.n_block}</span>
        <span className="muted">Rewritten</span><span className="num">{o.n_rewrite}</span>
        {!compact && <><span className="muted">Agent steps</span><span className="num">{o.steps}</span></>}
      </div>
    </div>
  )
}

/** Answer-key result. Held back (dimmed) until playback reaches the last step. */
export function VerdictLine({ run, ready = true, stacked = false }: { run: Run; ready?: boolean; stacked?: boolean }) {
  const o = run.outcome
  const harmed = !!o.unsafe
  const stats: [string, string | number][] = [
    ['Ticket completed', o.success ? 'Yes' : 'No'],
    ['Human asked', o.n_escalations],
    ['Refused', o.n_block],
    ['Rewritten', o.n_rewrite],
  ]
  return (
    <div className="panel" style={{ padding: stacked ? '12px 16px' : '8px 14px', flex: 'none', position: 'relative' }}>
      <div style={{ opacity: ready ? 1 : 0, transition: 'opacity 0.5s', display: 'flex', flexDirection: stacked ? 'column' : 'row', alignItems: stacked ? 'stretch' : 'center', gap: stacked ? 8 : 16 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, minWidth: 0 }}>
          <span className={`verdict ${harmed ? 'harm' : 'safe'}`} style={{ fontSize: stacked ? 26 : 20, flex: 'none' }}>{harmed ? '✕ Harm done' : '✓ No harm'}</span>
          {harmed && <span className="small" style={{ color: '#f07b7f', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{o.harms.slice(0, 2).map((h) => harmLabel(`${h.type}:${h.code}`)).join('; ')}</span>}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, auto)', columnGap: 16, marginLeft: stacked ? 0 : 'auto', justifyContent: stacked ? 'space-between' : 'end' }}>
          {stats.map(([k, v]) => (
            <div key={k} style={{ display: 'flex', flexDirection: 'column' }}>
              <span className="num" style={{ fontSize: 17, fontWeight: 600 }}>{v}</span>
              <span className="tiny muted" style={{ whiteSpace: 'nowrap' }}>{k}</span>
            </div>
          ))}
        </div>
      </div>
      {!ready && <div className="small muted" style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', padding: '0 16px' }}>The answer key judges the end state, after the last step.</div>}
    </div>
  )
}

export function RunLabel({ run }: { run: Run }) {
  const m = run.meta
  const who = m.source === 'recorded' ? (MODELS[m.model ?? '']?.label ?? m.model ?? '') : m.agent === 'scripted:naive' ? 'Careless plan' : 'Correct plan'
  return <span>{who} with {CONFIGS[m.config.split('__')[0]]?.label || m.label}</span>
}

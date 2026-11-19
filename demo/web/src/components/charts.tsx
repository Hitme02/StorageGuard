// Small SVG charts in the control-room style: thin marks, hairline axes, colour only for meaning.
import { useEffect, useState, type ReactNode } from 'react'

function useTip() {
  const [tip, setTip] = useState<{ x: number; y: number; html: ReactNode } | null>(null)
  const box = tip ? <div className="chart-tip" style={{ left: tip.x + 12, top: tip.y - 8 }}>{tip.html}</div> : null
  return { tip, setTip, box }
}

// ---------------------------------------------------------------------------
// Drive bays: each bay is one ticket; its LED says what happened.
// ---------------------------------------------------------------------------
export type BayState = 'red' | 'green' | 'off' | 'amber' | 'white' | 'brand'

export function Bays({ states, cols = 20, animateKey, bayHeight = 26, label }: { states: BayState[]; cols?: number; animateKey?: string | number; bayHeight?: number; label?: string }) {
  const [lit, setLit] = useState(states.length)
  useEffect(() => {
    if (animateKey === undefined) return
    setLit(0)
    let i = 0
    const t = window.setInterval(() => {
      i += 4
      setLit(Math.min(states.length, i))
      if (i >= states.length) window.clearInterval(t)
    }, 28)
    return () => window.clearInterval(t)
  }, [animateKey]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="shelf" style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }} role="img" aria-label={label}>
      {states.map((s, i) => (
        <div key={i} className={`bay ${i < lit ? s : 'off'}`} style={{ height: bayHeight }}><i /></div>
      ))}
    </div>
  )
}

/** 100 bays from rates: unsafe -> red, success -> green, the rest off. */
export function rateBays(unsafePct: number, successPct: number, total = 100): BayState[] {
  const red = Math.round((unsafePct / 100) * total)
  const green = Math.min(total - red, Math.round((successPct / 100) * total))
  return [...Array(red).fill('red'), ...Array(green).fill('green'), ...Array(total - red - green).fill('off')]
}

// ---------------------------------------------------------------------------
// Horizontal bars, one series. The highlighted bar (StorageGuard) wears the brand colour.
// ---------------------------------------------------------------------------
export interface BarDatum { label: string; value: number; ci?: [number, number] | null; hl?: boolean; color?: string; note?: string }

export function HBars({ data, max = 100, unit = '%', width = 560, rowH = 38, labelW = 210, digits = 1, title }: {
  data: BarDatum[]; max?: number; unit?: string; width?: number; rowH?: number; labelW?: number; digits?: number; title?: string
}) {
  const { setTip, box } = useTip()
  const plotW = width - labelW - 70
  const h = data.length * rowH + 26
  const x = (v: number) => labelW + (Math.max(0, v) / max) * plotW
  const ticks = [0, max / 2, max]
  return (
    <div style={{ position: 'relative' }}>
      <svg className="chart" width={width} height={h} role="img" aria-label={title}>
        {ticks.map((t) => (
          <g key={t} className="grid">
            <line x1={x(t)} x2={x(t)} y1={0} y2={h - 22} />
            <text x={x(t)} y={h - 6} textAnchor="middle">{`${+t.toFixed(2)}${unit === '%' ? '%' : ''}`}</text>
          </g>
        ))}
        {data.map((d, i) => {
          const y = i * rowH + 6
          const bh = Math.min(22, rowH - 12)
          const w = Math.max(2, x(d.value) - labelW)
          return (
            <g key={d.label} onMouseMove={(e) => setTip({ x: e.nativeEvent.offsetX, y: e.nativeEvent.offsetY, html: <><b>{d.label}</b>: {d.value.toFixed(digits)}{unit}{d.ci ? ` (95% CI ${d.ci[0]}–${d.ci[1]})` : ''}{d.note ? <div className="muted">{d.note}</div> : null}</> })}
              onMouseLeave={() => setTip(null)}>
              <rect x={0} y={y - 4} width={width} height={rowH - 2} fill="transparent" />
              <text className="lab" x={labelW - 12} y={y + bh / 2 + 5} textAnchor="end" style={d.hl ? { fill: 'var(--ink)', fontWeight: 600 } : undefined}>{d.label}</text>
              <path d={`M${labelW},${y} h${w - 4} a4,4 0 0 1 4,4 v${bh - 8} a4,4 0 0 1 -4,4 h${-(w - 4)} z`} fill={d.color || (d.hl ? 'var(--brand)' : 'var(--ink-3)')} />
              {d.ci && <line x1={x(d.ci[0])} x2={x(d.ci[1])} y1={y + bh / 2} y2={y + bh / 2} stroke="var(--ink)" strokeWidth={1.2} opacity={0.6} />}
              <text className="val" x={Math.max(labelW + w, d.ci ? x(d.ci[1]) : 0) + 8} y={y + bh / 2 + 5}>{d.value.toFixed(digits)}{unit}</text>
            </g>
          )
        })}
      </svg>
      {box}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Scatter: unsafe rate against human asks per ticket. Direct labels, no legend needed.
// ---------------------------------------------------------------------------
export interface PointDatum { label: string; x: number; y: number; hl?: boolean; dim?: boolean; dx?: number; dy?: number; anchor?: 'start' | 'end' | 'middle' }

export function Scatter({ data, width = 440, height = 300, xMax = 1.5, yMax = 55, xLabel = 'Human asks per ticket', yLabel = 'Unsafe tickets (%)', title, extra }: {
  data: PointDatum[]; width?: number; height?: number; xMax?: number; yMax?: number; xLabel?: string; yLabel?: string; title?: string; extra?: ReactNode
}) {
  const { setTip, box } = useTip()
  const m = { l: 62, r: 20, t: 14, b: 42 }
  const X = (v: number) => m.l + (v / xMax) * (width - m.l - m.r)
  const Y = (v: number) => height - m.b - (v / yMax) * (height - m.t - m.b)
  const xt = [0, xMax / 2, xMax]
  const yt = [0, Math.round(yMax / 2), yMax]
  return (
    <div style={{ position: 'relative' }}>
      <svg className="chart" width={width} height={height} role="img" aria-label={title}>
        <g className="grid">
          {yt.map((t) => <g key={t}><line x1={m.l} x2={width - m.r} y1={Y(t)} y2={Y(t)} /><text x={m.l - 8} y={Y(t) + 4} textAnchor="end">{t}%</text></g>)}
          {xt.map((t) => <text key={t} x={X(t)} y={height - m.b + 18} textAnchor="middle">{+t.toFixed(2)}</text>)}
        </g>
        <text x={(m.l + width - m.r) / 2} y={height - 6} textAnchor="middle">{xLabel}</text>
        <text transform={`translate(12 ${(height - m.b + m.t) / 2}) rotate(-90)`} textAnchor="middle">{yLabel}</text>
        {extra}
        {data.map((d) => (
          <g key={d.label} opacity={d.dim ? 0.45 : 1}
            onMouseMove={(e) => setTip({ x: e.nativeEvent.offsetX, y: e.nativeEvent.offsetY, html: <><b>{d.label}</b><div>{d.y.toFixed(1)}% unsafe, {d.x.toFixed(2)} asks per ticket</div></> })}
            onMouseLeave={() => setTip(null)}>
            <circle cx={X(d.x)} cy={Y(d.y)} r={14} fill="transparent" />
            <circle cx={X(d.x)} cy={Y(d.y)} r={d.hl ? 7 : 5.5} fill={d.hl ? 'var(--brand)' : 'var(--ink-2)'} stroke="var(--panel)" strokeWidth={2} />
            <text x={X(d.x) + (d.dx ?? 10)} y={Y(d.y) + (d.dy ?? 4)} textAnchor={d.anchor || 'start'} className="lab" style={{ paintOrder: 'stroke', stroke: 'var(--panel)', strokeWidth: 5, strokeLinejoin: 'round', ...(d.hl ? { fill: 'var(--brand-ink)', fontWeight: 600 } : {}) }}>{d.label}</text>
          </g>
        ))}
      </svg>
      {box}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Heatmap: one hue (red), dark to bright with the rate. Values printed in each cell.
// ---------------------------------------------------------------------------
function mix(a: [number, number, number], b: [number, number, number], t: number) {
  return `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * t)).join(',')})`
}
export function Heatmap({ rows, cols, value, rowLabel, colLabel, width = 900, cellH = 30, labelW = 330 }: {
  rows: string[]; cols: string[]; value: (r: string, c: string) => number | null; rowLabel: (r: string) => string; colLabel: (c: string) => string; width?: number; cellH?: number; labelW?: number
}) {
  const { setTip, box } = useTip()
  const cw = (width - labelW) / cols.length
  const h = rows.length * cellH + 40
  const lo: [number, number, number] = [30, 38, 47]
  const hi: [number, number, number] = [229, 72, 77]
  return (
    <div style={{ position: 'relative' }}>
      <svg className="chart" width={width} height={h} role="img" aria-label="Unsafe rate by trap and model">
        {cols.map((c, j) => <text key={c} x={labelW + j * cw + cw / 2} y={14} textAnchor="middle" className="lab">{colLabel(c)}</text>)}
        {rows.map((r, i) => (
          <g key={r}>
            <text x={labelW - 10} y={22 + i * cellH + cellH / 2 + 5} textAnchor="end" className="lab">{rowLabel(r)}</text>
            {cols.map((c, j) => {
              const v = value(r, c)
              return (
                <g key={c} onMouseMove={(e) => setTip({ x: e.nativeEvent.offsetX, y: e.nativeEvent.offsetY, html: <><b>{rowLabel(r)}</b><div>{colLabel(c)}: {v === null ? '–' : `${v.toFixed(0)}% unsafe`}</div></> })} onMouseLeave={() => setTip(null)}>
                  <rect x={labelW + j * cw + 1} y={22 + i * cellH + 1} width={cw - 2} height={cellH - 2} rx={3} fill={v === null ? 'var(--panel-2)' : mix(lo, hi, v / 100)} />
                  <text x={labelW + j * cw + cw / 2} y={22 + i * cellH + cellH / 2 + 5} textAnchor="middle" style={{ fill: v !== null && v > 55 ? '#fff' : 'var(--ink-2)', fontSize: 13, fontWeight: 600 }}>{v === null ? '–' : v.toFixed(0)}</text>
                </g>
              )
            })}
          </g>
        ))}
      </svg>
      {box}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Lines over model size. Up to three series, told apart by lightness, marker and a direct end label.
// ---------------------------------------------------------------------------
export interface Series { label: string; values: (number | null)[]; color: string; hollow?: boolean }

export function Lines({ xs, series, width = 560, height = 300, yMax = 100, yLabel, title }: { xs: string[]; series: Series[]; width?: number; height?: number; yMax?: number; yLabel?: string; title?: string }) {
  const { setTip, box } = useTip()
  const m = { l: 48, r: 120, t: 14, b: 34 }
  const X = (i: number) => m.l + (i / (xs.length - 1)) * (width - m.l - m.r)
  const Y = (v: number) => height - m.b - (v / yMax) * (height - m.t - m.b)
  return (
    <div style={{ position: 'relative' }}>
      <svg className="chart" width={width} height={height} role="img" aria-label={title}>
        <g className="grid">
          {[0, yMax / 2, yMax].map((t) => <g key={t}><line x1={m.l} x2={width - m.r} y1={Y(t)} y2={Y(t)} /><text x={m.l - 8} y={Y(t) + 4} textAnchor="end">{t}%</text></g>)}
          {xs.map((x, i) => <text key={x} x={X(i)} y={height - 10} textAnchor="middle">{x}</text>)}
        </g>
        {yLabel && <text transform={`translate(13 ${(height - m.b + m.t) / 2}) rotate(-90)`} textAnchor="middle">{yLabel}</text>}
        {series.map((s) => {
          const pts = s.values.map((v, i) => (v === null ? null : [X(i), Y(v)] as const)).filter(Boolean) as (readonly [number, number])[]
          const last = pts[pts.length - 1]
          return (
            <g key={s.label}>
              <polyline points={pts.map((p) => p.join(',')).join(' ')} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              {s.values.map((v, i) => v === null ? null : (
                <g key={i} onMouseMove={(e) => setTip({ x: e.nativeEvent.offsetX, y: e.nativeEvent.offsetY, html: <><b>{s.label}</b>, {xs[i]}: {v.toFixed(1)}%</> })} onMouseLeave={() => setTip(null)}>
                  <circle cx={X(i)} cy={Y(v)} r={12} fill="transparent" />
                  <circle cx={X(i)} cy={Y(v)} r={5} fill={s.hollow ? 'var(--panel)' : s.color} stroke={s.hollow ? s.color : 'var(--panel)'} strokeWidth={2} />
                </g>
              ))}
              {last && <text x={last[0] + 12} y={last[1] + 4} className="lab">{s.label}</text>}
            </g>
          )
        })}
      </svg>
      {box}
    </div>
  )
}

// Data layer: the live backend first, the pre-recorded copies in /data as the fallback.
import { useEffect, useState, useSyncExternalStore } from 'react'
import type { Knobs, Manifest, Run, Sweep, TaskSummary } from './types'

export type BackendState = 'checking' | 'live' | 'offline'

let state: BackendState = 'checking'
const listeners = new Set<() => void>()
function setState(s: BackendState) {
  if (s !== state) {
    state = s
    listeners.forEach((l) => l())
  }
}

async function ping(): Promise<void> {
  try {
    const ctl = new AbortController()
    const t = setTimeout(() => ctl.abort(), 1500)
    const r = await fetch('/api/health', { signal: ctl.signal, cache: 'no-store' })
    clearTimeout(t)
    setState(r.ok ? 'live' : 'offline')
  } catch {
    setState('offline')
  }
}
ping()
setInterval(ping, 8000)

export function useBackend(): BackendState {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
    () => state,
  )
}

export class NeedsBackend extends Error {
  constructor(what: string) {
    super(
      `${what} needs the live backend. Start it with: venv/bin/python demo/server.py ` +
        `(pre-recorded data covers the showcase tickets with default settings).`,
    )
  }
}

async function getStatic<T>(path: string): Promise<T> {
  const r = await fetch(`/data/${path}`)
  if (!r.ok) throw new Error(`missing pre-recorded file ${path}`)
  return (await r.json()) as T
}

async function callApi<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, body === undefined ? undefined : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await r.json().catch(() => ({ error: `HTTP ${r.status}` }))
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`)
  return data as T
}

/** Try the backend; when it is down (or the call fails to connect) use the recorded file. */
async function liveOrRecorded<T extends object>(api: () => Promise<T>, fallback: string | null, what: string): Promise<T & { origin: 'live' | 'recorded' }> {
  if (state !== 'offline') {
    try {
      const d = await api()
      setState('live')
      return Object.assign(d, { origin: 'live' as const })
    } catch (e) {
      if (!(e instanceof TypeError)) throw e // a real error from the server: show it
      setState('offline')
    }
  }
  if (fallback) {
    try {
      const d = await getStatic<T>(fallback)
      return Object.assign(d, { origin: 'recorded' as const })
    } catch {
      /* fall through */
    }
  }
  throw new NeedsBackend(what)
}

// ---- knobs ----
export const DEFAULT_KNOBS: Required<Knobs> = {
  human: 'oracle', tau_allow: 0.15, tau_block: 0.55, one_hop: false, reversibility: true, rewrites: true, feedback: true, pit: false,
}

function knobSuffix(k: Knobs): string | null {
  const full = { ...DEFAULT_KNOBS, ...k }
  const diff = (Object.keys(DEFAULT_KNOBS) as (keyof Knobs)[]).filter((key) => full[key] !== DEFAULT_KNOBS[key])
  if (diff.length === 0) return ''
  if (diff.length === 1 && diff[0] === 'one_hop') return '__onehop'
  return null
}

/** Only send knobs that differ from the defaults (keeps the backend cache keys stable). */
function compactKnobs(k: Knobs): Knobs {
  const out: Knobs = {}
  for (const key of Object.keys(DEFAULT_KNOBS) as (keyof Knobs)[]) {
    if (k[key] !== undefined && k[key] !== DEFAULT_KNOBS[key]) (out as any)[key] = k[key]
  }
  return out
}

// ---- endpoints ----
export function fetchLiveRun(task: string, agent: 'scripted:naive' | 'scripted:reference', config: string, knobs: Knobs = {}, physical = false) {
  const k = compactKnobs(knobs)
  const suffix = knobSuffix(k)
  const a = agent.replace('scripted:', '')
  let fb: string | null = null
  if (physical) fb = suffix === '' && a === 'naive' ? `physical/${task}__naive__${config}.json` : null
  else fb = suffix === null ? null : `live/${task}__${a}__${config}${suffix}.json`
  return liveOrRecorded<Run>(() => callApi('/api/run', { task, agent, config, knobs: k, physical }), fb, physical ? 'Running this plan on real files' : 'This combination of settings')
}

export function fetchRecorded(model: string, config: string, task: string, run = 0, exp = 'main') {
  const q = new URLSearchParams({ exp, model, config, task, run: String(run) })
  return liveOrRecorded<Run>(() => callApi(`/api/recorded?${q}`), `recorded/${exp}__${model}__${config}__${task}__r${run}.json`, 'This recorded run')
}

export function fetchMap(task: string) {
  return liveOrRecorded<{ task: TaskSummary; map: Run['map'] }>(() => callApi(`/api/map?task=${task}`), `map/${task}.json`, 'This map')
}

export function fetchSweep(config: string, knobs: Knobs = {}) {
  const k = compactKnobs(knobs)
  const full = { ...DEFAULT_KNOBS, ...k }
  let fb: string | null = null
  if (config !== 'storageguard') fb = `sweep/${config}.json`
  else if (knobSuffix(k) === '__onehop') fb = 'sweep/storageguard__onehop.json'
  else {
    const others = { ...k }
    delete others.tau_allow
    delete others.tau_block
    if (Object.keys(others).length === 0) fb = `sweep/storageguard__${full.tau_allow}__${full.tau_block}.json`
  }
  return liveOrRecorded<Sweep>(() => callApi('/api/sweep', { config, knobs: k }), fb, 'This threshold setting')
}

let tasksCache: Promise<TaskSummary[]> | null = null
export function fetchTasks(): Promise<TaskSummary[]> {
  if (!tasksCache) tasksCache = getStatic<TaskSummary[]>('tasks.json')
  return tasksCache
}

let manifestCache: Promise<Manifest> | null = null
export function fetchManifest(): Promise<Manifest> {
  if (!manifestCache) manifestCache = getStatic<Manifest>('manifest.json')
  return manifestCache
}

let resultsCache: Promise<any> | null = null
export function fetchResults(): Promise<any> {
  if (!resultsCache) resultsCache = getStatic<any>('results.json')
  return resultsCache
}

/** Small hook: load once, expose {data, error, loading}. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; error: string | null; loading: boolean } {
  const [s, set] = useState<{ data: T | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: true })
  useEffect(() => {
    let alive = true
    set((p) => ({ ...p, loading: true, error: null }))
    fn().then(
      (data) => alive && set({ data, error: null, loading: false }),
      (e) => alive && set({ data: null, error: String(e?.message || e), loading: false }),
    )
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return s
}

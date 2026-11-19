// Shapes returned by demo/server.py (and the pre-recorded copies in public/data).

export type Decision = 'ALLOW' | 'REWRITE' | 'ESCALATE' | 'BLOCK'

export interface PlanStep { tool: string; args: Record<string, unknown> }

export interface TaskSummary {
  id: string
  category: 'routine' | 'ambiguous' | 'trap' | 'injection'
  trap: string
  split: 'dev' | 'test' | null
  difficulty: string
  expected: string
  instruction: string
  notes: string
  task_type: string
  naive_plan: PlanStep[]
  reference_plan: PlanStep[]
}

export interface Finding {
  analyzer: string
  code: string
  severity: number
  entity: string
  detail: string
  tier: number | null
}

export interface Impact { id: string; kind: string; effect: 'destroyed' | 'affected'; hops: number; path: string }

export interface GuardInfo {
  action: PlanStep
  decision: Decision
  status: string
  executed: PlanStep[]
  human: string | null
  latency_ms: number | null
  risk: number | null
  rclass: number | null
  severities: Record<string, number>
  findings: Finding[]
  invariants: string[]
  rewrite: { rewrite: string; steps: PlanStep[]; intent_preserved: string; note: string } | null
  rewrite_risk: number | null
  impacted?: Impact[]
  targets?: string[]
  would_harm?: boolean
  would_harm_codes?: string[]
}

export interface Changes {
  removed: string[]
  added: string[]
  changed: { id: string; fields: Record<string, [unknown, unknown]> }[]
  unmapped: [string, string][]
  mapped: [string, string][]
}

export interface Step {
  step: number
  type: 'read' | 'mutate' | 'ask' | 'finish' | 'say' | 'unknown'
  tool: string
  args: Record<string, unknown>
  text?: string
  guard?: GuardInfo
  result?: { text: string; data?: any; count?: number; of?: string }
  changes?: Changes
}

export interface Harm { type: string; code: string; entity: string; detail: string; gb: number; tier: number | null }

export interface Outcome {
  unsafe: number
  harm_codes: string
  goal_met: number
  success: number
  finished: number
  termination: string
  steps: number
  n_escalations: number
  n_block: number
  n_rewrite: number
  n_allow: number
  n_approved: number
  n_denied: number
  harm_gb: number
  summary: string
  harms: Harm[]
  guard_latency_ms_median: number | null
}

export interface MapNode {
  id: string
  kind: string
  name: string
  site: string | null
  tier?: number | null
  facts: Record<string, any>
  created_later?: boolean
}
export interface MapEdge { from: string; to: string; type: string }
export interface EstateMapData {
  nodes: MapNode[]
  edges: MapEdge[]
  sites: { id: string; name: string; role: string }[]
  targets: string[]
  time?: string
}

export interface PhysicalFile { path: string; bytes: number; held?: Record<string, number> }
export interface Physical {
  start_files: number
  start_bytes: number
  files_deleted: number
  bytes_deleted: number
  files_overwritten: number
  destroyed: { lineage: string; owner: string; gb: number; tier: number | null; app: string | null }[]
  rolled_back: { lineage?: string; owner: string; gb?: number }[]
  cg_split: unknown
  outage: { volume: string; failed_writes: number }[]
  site_loss: any[]
  ransomware: any[]
  ransomware_admin: any[]
  drills: Record<string, Record<string, number>>
  phys_data: number
  phys_outage: number
  phys_site: number
  phys_ransom: number
  phys_unsafe: number
  destroyed_gb: number
  deletions: { op: string; action: string; files: PhysicalFile[] }[]
  start_file_list: PhysicalFile[]
}

export interface RunMeta {
  source: 'live' | 'recorded'
  agent?: string
  config: string
  label: string
  model?: string
  exp?: string
  run?: number
  knobs?: Knobs
  tau_allow?: number
  tau_block?: number
}

export interface Run {
  task: TaskSummary
  meta: RunMeta
  steps: Step[]
  outcome: Outcome
  map: EstateMapData
  physical?: Physical
  /** set by the client: where the data came from */
  origin?: 'live' | 'recorded'
}

export interface Knobs {
  human?: 'oracle' | 'none' | 'rubber_stamp'
  tau_allow?: number
  tau_block?: number
  one_hop?: boolean
  reversibility?: boolean
  rewrites?: boolean
  feedback?: boolean
  pit?: boolean
}

export interface SweepPlan {
  tasks: number
  harm_rate: number
  success_rate: number
  false_block_rate: number
  asks_per_task: number
  rewrites_per_task: number
  blocks_per_task: number
  harmful_tasks: string[]
}
export interface Sweep {
  config: string
  tau_allow: number
  tau_block: number
  naive: SweepPlan
  reference: SweepPlan
  origin?: 'live' | 'recorded'
}

export interface Manifest {
  showcase: string[]
  live: string[]
  recorded: string[]
  physical: string[]
  maps: string[]
  sweep: string[]
  tau_allow: number[]
  tau_block: number[]
}

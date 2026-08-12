/**
 * Клиент бэкенда. Типы повторяют то, что отдаёт app/api/serialize.py.
 *
 * Всё время приходит строками "ЧЧ:ММ": минуты от полуночи живут только внутри
 * солвера. Для вёрстки Ганта их приходится разбирать обратно — см. toMin().
 */

export type Priority = 'P1' | 'P2' | 'P3' | 'P4'

export interface Kpi {
  jobs_total: number
  jobs_assigned: number
  jobs_unassigned: number
  assign_rate: number
  dropped_by_priority: Record<string, number>
  travel_min: number
  travel_km: number
  work_min: number
  wait_min: number
  lunch_min: number
  sla_violations: number
  sla_late_min: number
  engineers_used: number
  busy_mean_min: number
  busy_spread_min: number
  busy_stdev_min: number
  travel_share: number
  locked_done?: number
  replanned_at?: string
}

export interface Stop {
  kind: 'start' | 'job' | 'end'
  job_id: string | null
  label: string
  lat: number
  lon: number
  arrival: string
  service_start: string
  service_end: string
  wait_min: number
  travel_min: number
  travel_km: number
  sla_late_min: number
  priority?: Priority
  customer?: string
  address?: string
  district?: string
  work_type?: string
  complexity?: number
  window?: [string, string]
  window_hard?: boolean
  sla_deadline?: string
  /** Ломаная [[lon, lat], …] от предыдущей точки маршрута, по дорогам. */
  geometry?: number[][]
  /** Перегон пройден через метро — только для пеших инженеров. */
  via_metro?: boolean
}

export interface Route {
  engineer_id: string
  engineer_name: string
  vehicle_type: 'car' | 'van' | 'walk_transit'
  pickup_warehouse: string | null
  start: string
  end: string
  job_count: number
  travel_min: number
  travel_km: number
  work_min: number
  wait_min: number
  lunch_start: string | null
  lunch_min: number
  stops: Stop[]
}

export interface Job {
  id: string
  external_id: string
  customer: string
  address: string
  district: string
  lat: number
  lon: number
  work_type: string
  work_type_id: string
  specialization: string
  specialization_name: string
  min_level: number
  complexity: number
  duration_min: number
  required_equipment: { id: string; name: string; bulky: boolean }[]
  window: [string, string]
  window_hard: boolean
  priority: Priority
  sla_deadline: string
  known_at_day_start: boolean
  contact_phone: string
  status: 'new' | 'planned' | 'done' | 'unassigned'
  engineer_id?: string | null
}

export interface PlanDiff {
  moved: { job_id: string; from: string; to: string }[]
  added: { job_id: string; to: string }[]
  removed: string[]
  kept: number
  affected: string[]
  untouched: string[]
  summary: string
}

export interface Plan {
  id: string
  date: string
  status: string
  solve_ms: number
  warm_started: boolean
  now: string | null
  kpi: Kpi
  routes: Route[]
  unassigned: Job[]
  candidates: Record<string, string[]>
  /** Ручные закрепления диспетчера: заявка -> инженер. */
  pins: Record<string, string>
  completed: string[]
  diff?: PlanDiff
  events?: DayEvent[]
  cost?: PinCost
}

export interface PinCost {
  travel_delta_min: number
  assigned_delta: number
  sla_delta: number
  pins: Record<string, string>
}

export interface Engineer {
  id: string
  name: string
  skills: { specialization: string; specialization_name: string; level: number }[]
  shift: [string, string]
  break_window: [string, string]
  break_min: number
  vehicle_type: 'car' | 'van' | 'walk_transit'
  can_carry_bulky: boolean
  home: { lat: number; lon: number; address: string }
  equipment: { id: string; name: string }[]
  max_overtime_min: number
}

export interface DayEvent {
  at: string
  type: string
  payload: string
  comment: string
  fired?: boolean
}

export interface LogEntry {
  at: string
  kind: 'build' | 'replan' | 'event'
  text: string
  version: number
  affected?: string[]
  event_type?: string
}

export interface Explanation {
  job_id: string
  engineer_id: string
  engineer_name: string
  choice: string[]
  timing: string[]
  alternatives: {
    engineer_id: string
    engineer_name: string
    possible: boolean
    detail: string
    extra_travel_min: number
  }[]
  text: string
}

export interface WhyNot {
  job_id: string
  verdict: string
  counts: Record<string, number>
  qualified: {
    engineer_id: string
    engineer_name: string
    reason: string
    detail: string
  }[]
  blocked_total: number
  feasible_with_shift: { engineer_name: string; detail: string }[]
  job?: Job
}

export interface CompareRow {
  label: string
  manual: string
  optimized: string
  effect: string
}

export interface Compare {
  scope: string
  note: string
  baseline_kpi: Kpi
  optimized_kpi: Kpi
  rows: CompareRow[]
}

export interface Dataset {
  date: string
  day: [string, string]
  specializations: Record<string, string>
  work_types: {
    id: string
    name: string
    specialization: string
    min_level: number
    base_duration_min: number
    equipment: { id: string; name: string; bulky: boolean }[]
  }[]
  equipment: {
    id: string
    name: string
    bulky: boolean
    units: number
    stock: string
    rare: boolean
  }[]
  warehouses: {
    id: string
    name: string
    lat: number
    lon: number
    address: string
    open: [string, string]
  }[]
  counts: { jobs: number; engineers: number; events: number }
}

// --------------------------------------------------------------------------

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) message = String(body.detail)
    } catch {
      /* тело не JSON — оставляем статус */
    }
    throw new Error(message)
  }
  return res.json() as Promise<T>
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) })

export const api = {
  dataset: () => request<Dataset>('/dataset'),
  engineers: () => request<Engineer[]>('/engineers'),
  jobs: () => request<Job[]>('/jobs'),
  events: () => request<DayEvent[]>('/events'),
  log: () => request<LogEntry[]>('/log'),
  plan: () => request<Plan>('/plan'),
  build: (preset: string, timeLimit: number) =>
    post<Plan>('/plan/build', { preset, time_limit_s: timeLimit }),
  step: (timeLimit: number, stability: number) =>
    post<Plan>('/plan/step', { time_limit_s: timeLimit, stability }),
  replan: (at: string, timeLimit: number, stability: number) =>
    post<Plan>('/plan/replan', { at, time_limit_s: timeLimit, stability }),
  reset: () => post<{ ok: boolean; now: string }>('/plan/reset'),
  pin: (jobId: string, engineerId: string | null) =>
    post<Plan>('/plan/pin', { job_id: jobId, engineer_id: engineerId }),
  explain: (jobId: string) => request<Explanation>(`/plan/explain/${jobId}`),
  whyNot: (jobId: string) => request<WhyNot>(`/plan/why-not/${jobId}`),
  whyNotAll: () => request<WhyNot[]>('/plan/why-not'),
  compare: () => request<Compare>('/plan/compare'),
}

/** "12:45" -> 765. Гант считает позиции в минутах. */
export function toMin(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number)
  return h * 60 + m
}

export function fmtDuration(min: number): string {
  if (min < 60) return `${min} мин`
  const h = Math.floor(min / 60)
  const rest = min % 60
  return rest ? `${h} ч ${rest} мин` : `${h} ч`
}

/**
 * Клиент бэкенда. Типы повторяют то, что отдаёт app/api/serialize.py.
 *
 * Всё время приходит строками "ЧЧ:ММ": минуты от полуночи живут только внутри
 * солвера. Для вёрстки Ганта их приходится разбирать обратно — см. toMin().
 */

/** Справочники ТЗ «Билайн Бизнес», п. 2.4.1. */
export type Priority = 'normal' | 'urgent'
export type Transport = 'car' | 'foot' | 'bike' | 'transit'

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
  /** Адрес геокодирован не до дома — точка на карте приблизительная. */
  approx?: boolean
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
  vehicle_type: Transport
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
  priority_label: string
  sla_deadline: string
  known_at_day_start: boolean
  contact_phone: string
  status: 'new' | 'planned' | 'done' | 'unassigned' | 'cancelled'
  engineer_id?: string | null
  /** Требуемый тип транспорта (ТЗ) — только при наличии ограничения. */
  required_transport: Transport | null
  required_transport_label: string | null
  /** Точность геокодирования адреса: всё, кроме house, — приблизительно. */
  geo_precision: 'house' | 'house~' | 'street' | 'district' | 'none'
}

export interface PlanDiff {
  moved: { job_id: string; from: string; to: string }[]
  added: { job_id: string; to: string }[]
  removed: string[]
  /** Тот же инженер, другое место в маршруте (позиции с единицы). */
  reordered: { job_id: string; engineer_id: string; from: number; to: number }[]
  /** Тот же инженер и порядок, но начало сдвинулось на 15 минут и больше. */
  shifted: { job_id: string; engineer_id: string; from: string; to: string }[]
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
  /** Справочник инженеров менялся после расчёта — план устарел. */
  staff_changed?: boolean
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
  vehicle_type: Transport
  vehicle_label: string
  can_carry_bulky: boolean
  home: { lat: number; lon: number; address: string }
  equipment: { id: string; name: string }[]
  max_overtime_min: number
}

/** Заявка, поступающая в течение дня. */
export interface JobInput {
  customer: string
  work_type_id: string
  address: string
  district: string
  lat: number
  lon: number
  complexity: number
  priority: Priority
  tw_start: string
  tw_end: string
  tw_hard: boolean
  contact_phone: string
  required_transport?: Transport | null
}

/** Карточка инженера в том виде, в каком её принимает сервис. */
export interface EngineerInput {
  id?: string | null
  name: string
  skills: Record<string, number>
  shift_start: string
  shift_end: string
  break_from: string
  break_to: string
  break_min: number
  vehicle_type: Transport
  home_lat: number
  home_lon: number
  home_address: string
  onboard_equipment: string[]
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

/** Почему маршрут инженера такой: что диктовали ограничения, что выбрала оптимизация. */
export interface RouteExplanation {
  engineer_id: string
  engineer_name: string
  headline: string
  constraints: string[]
  choices: string[]
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
  engineers_total: number
  skill_ok: number
  feasible_with_shift: { engineer_name: string; detail: string }[]
  job?: Job
}

export interface CompareRow {
  label: string
  manual: string
  optimized: string
  effect: string
}

export interface RouteSummary {
  engineer_id: string
  engineer_name: string
  job_count: number
  travel_min: number
  travel_km: number
}

export interface Compare {
  scope: string
  baseline: 'tz' | 'smart'
  baseline_label: string
  baseline_note: string
  note: string
  baseline_kpi: Kpi
  optimized_kpi: Kpi
  rows: CompareRow[]
  baseline_routes: RouteSummary[]
  optimized_routes: RouteSummary[]
  /** Распределение диспетчера заказчика — только у выгрузки «Билайн Бизнес». */
  fact: {
    label: string
    note: string
    kpi: Kpi & { control_done: number; control_overdue: number; control_cancelled: number }
    rows: CompareRow[]
    routes: RouteSummary[]
    statuses: { done: number; overdue: number; cancelled: number; unsent: number }
  } | null
}

export interface DatasetInfo {
  key: string
  title: string
  kind: 'customer' | 'synthetic' | 'uploaded'
  note: string
  available: boolean
}

export interface Dataset {
  date: string
  key: string | null
  title: string
  assumptions: string[]
  return_to_start: boolean
  uses_levels: boolean
  uses_equipment: boolean
  day: [string, string]
  specializations: Record<string, string>
  transport_types: { id: Transport; name: string }[]
  priorities: { id: Priority; name: string }[]
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

  /** Приём заявки: сервис сам пересчитывает остаток дня и возвращает план. */
  createJob: (body: JobInput) =>
    post<{ job: Job; plan: Plan | null }>('/jobs', body),

  /** Событие по требованию — отмена заявки или недоступность инженера — с
   *  немедленным пересчётом остатка дня (ТЗ, п. 2.1 (6)). */
  addEvent: (type: 'job_cancelled' | 'engineer_unavailable', id: string) =>
    post<{ event: DayEvent; plan: Plan | null }>('/events', { type, id }),

  createEngineer: (body: EngineerInput) => post<Engineer>('/engineers', body),
  updateEngineer: (id: string, body: EngineerInput) =>
    request<Engineer>(`/engineers/${id}`, {
      method: 'PATCH', body: JSON.stringify(body),
    }),
  deleteEngineer: (id: string) =>
    request<{ ok: boolean; engineers: number }>(`/engineers/${id}`, {
      method: 'DELETE',
    }),
  explain: (jobId: string) => request<Explanation>(`/plan/explain/${jobId}`),
  explainRoute: (engineerId: string) =>
    request<RouteExplanation>(`/plan/explain-route/${engineerId}`),
  whyNot: (jobId: string) => request<WhyNot>(`/plan/why-not/${jobId}`),
  whyNotAll: () => request<WhyNot[]>('/plan/why-not'),
  compare: (baseline: 'tz' | 'smart' = 'tz') =>
    request<Compare>(`/plan/compare?baseline=${baseline}`),

  datasets: () => request<{ active: string | null; items: DatasetInfo[] }>('/datasets'),
  activateDataset: (key: string) =>
    post<{ ok: boolean; active: string; title: string }>(`/datasets/${key}/activate`),
  /** Загрузка CSV/JSON: multipart, поэтому без JSON-заголовка. */
  uploadDataset: async (files: File[], name: string) => {
    const form = new FormData()
    for (const f of files) form.append('files', f, f.name)
    form.append('name', name)
    const res = await fetch('/api/datasets/upload', { method: 'POST', body: form })
    if (!res.ok) {
      let message = `${res.status} ${res.statusText}`
      try {
        const body = await res.json()
        const d = body?.detail
        if (d?.problems) message = `${d.message}:\n• ${d.problems.join('\n• ')}`
        else if (d) message = String(d)
      } catch { /* не JSON */ }
      throw new Error(message)
    }
    return res.json() as Promise<{ ok: boolean; active: string; title: string; note: string }>
  },
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

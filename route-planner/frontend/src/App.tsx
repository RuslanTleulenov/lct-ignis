import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  api, type Compare, type DayEvent, type Dataset, type Engineer,
  type Explanation, type Job, type LogEntry, type Plan, type WhyNot,
} from './api'
import { go, useRoute } from './router'
import { OverviewScreen } from './screens/Overview'
import { EngineersScreen } from './screens/Engineers'
import { EngineerScreen } from './screens/Engineer'
import { JobsScreen, JobScreen } from './screens/Jobs'
import { BacklogScreen, EffectScreen, ReferenceScreen } from './screens/Misc'

const NAV = [
  { path: '/', screen: 'overview', ic: '◉', label: 'Сводка дня' },
  { path: '/engineers', screen: 'engineers', ic: '☰', label: 'Инженеры' },
  { path: '/jobs', screen: 'jobs', ic: '✦', label: 'Заявки' },
  { path: '/backlog', screen: 'backlog', ic: '⚠', label: 'Отложенные' },
  { path: '/effect', screen: 'effect', ic: '↗', label: 'Оценка эффекта' },
  { path: '/reference', screen: 'reference', ic: '▤', label: 'Справочники' },
] as const

export default function App() {
  const route = useRoute()

  const [dataset, setDataset] = useState<Dataset | null>(null)
  const [engineers, setEngineers] = useState<Engineer[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [events, setEvents] = useState<DayEvent[]>([])
  const [log, setLog] = useState<LogEntry[]>([])
  const [plan, setPlan] = useState<Plan | null>(null)
  const [compare, setCompare] = useState<Compare | null>(null)
  const [whyNotAll, setWhyNotAll] = useState<WhyNot[]>([])

  const [selectedJob, setSelectedJob] = useState<string | null>(null)
  const [explanation, setExplanation] = useState<Explanation | null>(null)
  const [whyNot, setWhyNot] = useState<WhyNot | null>(null)

  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<{ head: string; body: string } | null>(null)
  const [playing, setPlaying] = useState(false)
  const [railOpen, setRailOpen] = useState(true)

  const [preset, setPreset] = useState('default')
  const [timeLimit, setTimeLimit] = useState(15)
  const [stability, setStability] = useState(200)

  const busyRef = useRef(false)

  useEffect(() => {
    Promise.all([api.dataset(), api.engineers(), api.events()])
      .then(([d, e, ev]) => { setDataset(d); setEngineers(e); setEvents(ev) })
      .catch((err) => setError(String(err.message ?? err)))
    api.plan().then((p) => { setPlan(p); void refreshAux() }).catch(() => {})
  }, [])

  const refreshAux = useCallback(async () => {
    const [j, l] = await Promise.all([api.jobs(), api.log()])
    setJobs(j)
    setLog(l)
    api.whyNotAll().then(setWhyNotAll).catch(() => setWhyNotAll([]))
  }, [])

  const run = useCallback(async (
    label: string, fn: () => Promise<Plan>,
  ): Promise<Plan | null> => {
    if (busyRef.current) return null
    busyRef.current = true
    setBusy(label)
    setError(null)
    try {
      const p = await fn()
      setPlan(p)
      await refreshAux()
      return p
    } catch (err) {
      setError(String((err as Error).message ?? err))
      setPlaying(false)
      return null
    } finally {
      busyRef.current = false
      setBusy(null)
    }
  }, [refreshAux])

  const build = useCallback(async () => {
    setSelectedJob(null)
    setExplanation(null)
    setCompare(null)
    const p = await run('Считаю план дня…', () => api.build(preset, timeLimit))
    if (p) {
      setToast({
        head: 'План построен',
        body: `Назначено ${p.kpi.jobs_assigned} из ${p.kpi.jobs_total}, `
          + `в пути ${p.kpi.travel_min} мин, нарушений SLA ${p.kpi.sla_violations}`,
      })
      api.compare().then(setCompare).catch(() => {})
    }
  }, [run, preset, timeLimit])

  const step = useCallback(async () => {
    const p = await run('Перепланирую…', () => api.step(3, stability))
    if (p?.events?.length) {
      setToast({
        head: `${p.now} · ${p.events.map((e) => e.comment).join(' · ')}`,
        body: p.diff?.summary ?? '',
      })
    }
    return p
  }, [run, stability])

  const pin = useCallback(async (jobId: string, engineerId: string | null) => {
    const p = await run(engineerId ? 'Закрепляю и пересчитываю…' : 'Снимаю закрепление…',
      () => api.pin(jobId, engineerId))
    if (!p) return
    const c = p.cost
    const sign = (n: number) => (n > 0 ? `+${n}` : String(n))
    const parts: string[] = []
    if (c?.travel_delta_min) parts.push(`${sign(c.travel_delta_min)} мин в пути`)
    if (c?.assigned_delta) parts.push(`${sign(c.assigned_delta)} заявок в плане`)
    if (c?.sla_delta) parts.push(`${sign(c.sla_delta)} нарушений SLA`)
    setToast({
      head: engineerId ? 'Заявка закреплена' : 'Закрепление снято',
      body: parts.length ? `Цена решения: ${parts.join(', ')}` : 'План не изменился',
    })
  }, [run])

  const reset = useCallback(async () => {
    setPlaying(false)
    await api.reset()
    setPlan(null); setCompare(null); setSelectedJob(null)
    setExplanation(null); setWhyNotAll([]); setToast(null)
    await refreshAux()
  }, [refreshAux])

  useEffect(() => {
    if (!playing || busy) return
    if (!events.some((e) => e.at > (plan?.now ?? '00:00'))) { setPlaying(false); return }
    const id = setTimeout(() => { void step() }, 900)
    return () => clearTimeout(id)
  }, [playing, busy, plan, events, step])

  // ---- объяснение выбранной заявки ----
  const jobInFocus = route.screen === 'job' ? route.id : selectedJob
  useEffect(() => {
    if (!jobInFocus || !plan) { setExplanation(null); setWhyNot(null); return }
    setExplanation(null); setWhyNot(null)
    const assigned = plan.routes.some((r) => r.stops.some((s) => s.job_id === jobInFocus))
    if (assigned) api.explain(jobInFocus).then(setExplanation).catch(() => {})
    else api.whyNot(jobInFocus).then(setWhyNot).catch(() => {})
  }, [jobInFocus, plan])

  useEffect(() => {
    if (!toast) return
    const id = setTimeout(() => setToast(null), 6000)
    return () => clearTimeout(id)
  }, [toast])

  // Сравнение считается лениво: оно требует прогона жадного планировщика,
  // а нужно только на своём экране. Без этого экран «Эффект» оставался пустым
  // при заходе на уже построенный план.
  useEffect(() => {
    if (route.screen !== 'effect' || compare || !plan || busy) return
    api.compare().then(setCompare).catch(() => {})
  }, [route.screen, compare, plan, busy])

  const changedJobs = useMemo(() => {
    const s = new Set<string>()
    for (const m of plan?.diff?.moved ?? []) s.add(m.job_id)
    for (const a of plan?.diff?.added ?? []) s.add(a.job_id)
    return s
  }, [plan])

  const hasEventsLeft = events.some((e) => e.at > (plan?.now ?? '00:00'))
  const pinCount = plan ? Object.keys(plan.pins).length : 0
  const counts: Record<string, number | undefined> = {
    '/engineers': engineers.length || undefined,
    '/jobs': jobs.length || undefined,
    '/backlog': plan?.unassigned.length || undefined,
  }

  return (
    <div className="app">
      <div className="topbar">
        <button className="ghost" style={{ padding: '7px 11px' }}
                onClick={() => setRailOpen((v) => !v)} title="Свернуть меню">☰</button>
        <a className="brand" href="#/">
          <span className="mark">ВС</span>
          <span>
            <span className="name">Выездная служба</span>
            <span className="sub">{dataset
              ? `${dataset.date} · ${dataset.counts.jobs} заявок · ${dataset.counts.engineers} инженеров`
              : 'загрузка…'}</span>
          </span>
        </a>

        <span className="clock">{plan?.now ?? '—'}</span>
        {pinCount > 0 && <span className="pill accent">закреплено · {pinCount}</span>}
        {busy && (
          <span className="pill" style={{ animation: 'pulse 1.2s infinite' }}>
            {busy}
          </span>
        )}

        <span className="spacer" />

        <select value={preset} onChange={(e) => setPreset(e.target.value)}
                title="Критерий оптимизации">
          <option value="default">Сбалансированный план</option>
          <option value="sla">Приоритет соблюдения SLA</option>
          <option value="travel">Минимум пробега</option>
          <option value="balance">Равномерная загрузка</option>
        </select>
        <select value={timeLimit} onChange={(e) => setTimeLimit(Number(e.target.value))}
                title="Ограничение времени расчёта">
          <option value={5}>5 с</option>
          <option value={15}>15 с</option>
          <option value={30}>30 с</option>
        </select>
        <button className="primary" onClick={build} disabled={!!busy}>Построить план</button>
        <button onClick={step} disabled={!!busy || !plan || !hasEventsLeft}>
          Следующее событие
        </button>
        <button onClick={() => setPlaying((v) => !v)}
                disabled={(!!busy && !playing) || !plan || !hasEventsLeft}>
          {playing ? 'Приостановить' : 'Смоделировать день'}
        </button>
        <select value={stability} onChange={(e) => setStability(Number(e.target.value))}
                title="Насколько дорого обходится передача визита другому исполнителю">
          <option value={0}>Стабильность: не учитывать</option>
          <option value={80}>Стабильность: низкая</option>
          <option value={200}>Стабильность: средняя</option>
          <option value={600}>Стабильность: высокая</option>
        </select>
        <button className="ghost" onClick={reset} disabled={!!busy}>Сбросить</button>
      </div>

      <div className="body">
        <nav className={`rail${railOpen ? '' : ' collapsed'}`}>
          {NAV.map((n) => (
            <a key={n.path} href={`#${n.path}`} title={n.label}
               className={route.screen === n.screen
                 || (n.screen === 'engineers' && route.screen === 'engineer')
                 || (n.screen === 'jobs' && route.screen === 'job') ? 'on' : ''}>
              <span className="ic">{n.ic}</span>
              <span className="label">{n.label}</span>
              {counts[n.path] !== undefined && (
                <span className="badge">{counts[n.path]}</span>
              )}
            </a>
          ))}
        </nav>

        <main className="screen">
          {error && <div className="err" style={{ marginBottom: 12 }}>{error}</div>}

          {route.screen === 'overview' && (
            <OverviewScreen plan={plan} dataset={dataset} engineers={engineers}
                            events={events} log={log} selectedJob={selectedJob}
                            changedJobs={changedJobs}
                            onSelectJob={(id) => go(`/jobs/${id}`)} />
          )}
          {route.screen === 'engineers' && (
            <EngineersScreen plan={plan} engineers={engineers} />
          )}
          {route.screen === 'engineer' && route.id && (
            <EngineerScreen id={route.id} plan={plan} engineers={engineers}
                            jobs={jobs} dataset={dataset} selectedJob={selectedJob}
                            onSelectJob={(id) => { setSelectedJob(id); go(`/jobs/${id}`) }} />
          )}
          {route.screen === 'jobs' && <JobsScreen jobs={jobs} plan={plan} />}
          {route.screen === 'job' && route.id && (
            <JobScreen id={route.id} jobs={jobs} plan={plan} explanation={explanation}
                       whyNot={whyNot} busy={!!busy} onPin={pin} />
          )}
          {route.screen === 'backlog' && (
            <BacklogScreen plan={plan} whyNotAll={whyNotAll} />
          )}
          {route.screen === 'effect' && <EffectScreen compare={compare} />}
          {route.screen === 'reference' && (
            <ReferenceScreen dataset={dataset} engineers={engineers} />
          )}
        </main>
      </div>

      {toast && (
        <div className="toast">
          <div className="hd">{toast.head}</div>
          {toast.body && <div className="dim" style={{ fontSize: 12.5 }}>{toast.body}</div>}
        </div>
      )}
    </div>
  )
}

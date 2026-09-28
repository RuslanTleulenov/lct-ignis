import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  api, type Compare, type DayEvent, type Dataset, type DatasetInfo, type Engineer,
  type Explanation, type Job, type JobInput, type LogEntry, type Plan, type WhyNot,
} from './api'
import { dayLabel } from './format'
import { go, useRoute } from './router'
import { OverviewScreen } from './screens/Overview'
import { EngineersScreen } from './screens/Engineers'
import { EngineerScreen } from './screens/Engineer'
import { JobsScreen, JobScreen } from './screens/Jobs'
import { BacklogScreen, EffectScreen, ReferenceScreen } from './screens/Misc'

// Состав и подписи разделов заданы макетом — менять их нельзя без правки
// макета, иначе интерфейс и прототип расходятся.
const NAV = [
  { path: '/', screen: 'overview', ic: '◎', label: 'Обзор дня' },
  { path: '/engineers', screen: 'engineers', ic: '⚑', label: 'Инженеры' },
  { path: '/jobs', screen: 'jobs', ic: '▤', label: 'Заявки' },
  { path: '/backlog', screen: 'backlog', ic: '!', label: 'Не назначено' },
  { path: '/effect', screen: 'effect', ic: '↗', label: 'Эффект' },
  { path: '/reference', screen: 'reference', ic: '⊞', label: 'Справочники' },
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
  const [baseline, setBaseline] = useState<'tz' | 'smart'>('tz')
  const [datasets, setDatasets] = useState<DatasetInfo[]>([])
  const [whyNotAll, setWhyNotAll] = useState<WhyNot[]>([])

  const [selectedJob, setSelectedJob] = useState<string | null>(null)
  const [explanation, setExplanation] = useState<Explanation | null>(null)
  const [whyNot, setWhyNot] = useState<WhyNot | null>(null)

  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<{ head: string; body: string } | null>(null)
  const [playing, setPlaying] = useState(false)
  const [railOpen, setRailOpen] = useState(true)

  // Кейс требует настраиваемых весов цели, но в макете шапка — это четыре
  // кнопки. Компромисс: параметры спрятаны за отдельной кнопкой и не ломают
  // ряд, а значения по умолчанию совпадают с прежними.
  const [preset, setPreset] = useState('default')
  const [timeLimit, setTimeLimit] = useState(15)
  const [stability, setStability] = useState(200)
  const [showParams, setShowParams] = useState(false)

  const busyRef = useRef(false)

  /** Перечитать всё, что зависит от набора данных. */
  const loadDataset = useCallback(async () => {
    const [d, e, ev, list] = await Promise.all([
      api.dataset(), api.engineers(), api.events(), api.datasets(),
    ])
    setDataset(d); setEngineers(e); setEvents(ev); setDatasets(list.items)
  }, [])

  useEffect(() => {
    loadDataset().catch((err) => setError(String(err.message ?? err)))
    api.plan().then((p) => { setPlan(p); void refreshAux() }).catch(() => {})
  }, [loadDataset])

  /** Смена набора: встроенный либо загруженный файлами. День начинается заново. */
  const changeDataset = useCallback(async (
    action: { activate: string } | { upload: File[]; name: string },
  ) => {
    setPlaying(false)
    const res = 'activate' in action
      ? await api.activateDataset(action.activate)
      : await api.uploadDataset(action.upload, action.name)
    await loadDataset()
    setPlan(null); setCompare(null); setSelectedJob(null)
    setExplanation(null); setWhyNotAll([])
    await refreshAux()
    setToast({ head: `Открыт набор: ${res.title}`,
               body: 'Постройте план — прежний сброшен вместе с прожитым днём' })
  }, [loadDataset])

  /** Состав службы изменился: перечитываем справочник и признак устаревания. */
  const reloadStaff = useCallback(async () => {
    const [list, current] = await Promise.all([
      api.engineers(),
      api.plan().catch(() => null),
    ])
    setEngineers(list)
    if (current) setPlan(current)
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
      api.compare(baseline).then(setCompare).catch(() => {})
    }
  }, [run, preset, timeLimit, baseline])

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

  /** Приём заявки: сервис сам пересчитывает день и возвращает новый план. */
  const addJob = useCallback(async (body: JobInput) => {
    setError(null)
    setBusy('Принимаю заявку и пересчитываю день…')
    try {
      const res = await api.createJob(body)
      if (res.plan) setPlan(res.plan)
      await refreshAux()
      const moved = res.plan?.diff?.moved.length ?? 0
      const affected = res.plan?.diff?.affected.length ?? 0
      setToast({
        head: `Заявка ${res.job.id} принята — ${res.job.customer}`,
        body: res.plan
          ? `День пересчитан: перенесено ${moved}, затронуто инженеров ${affected}`
          : 'План не построен — заявка попадёт в него при расчёте',
      })
    } finally {
      setBusy(null)
    }
  }, [refreshAux])

  /** Отмена заявки или недоступность инженера — с этого момента, с пересчётом. */
  const fireEvent = useCallback(async (
    type: 'job_cancelled' | 'engineer_unavailable', id: string,
  ) => {
    setError(null)
    setBusy(type === 'job_cancelled' ? 'Отменяю и пересчитываю…' : 'Вывожу из смены и пересчитываю…')
    try {
      const res = await api.addEvent(type, id)
      if (res.plan) setPlan(res.plan)
      setEvents(await api.events())
      await refreshAux()
      const moved = res.plan?.diff?.moved.length ?? 0
      const affected = res.plan?.diff?.affected.length ?? 0
      setToast({
        head: res.event.comment,
        body: res.plan
          ? `День пересчитан: перенесено ${moved}, затронуто инженеров ${affected}`
          : 'План не построен — событие учтётся при расчёте',
      })
      if (type === 'engineer_unavailable') go('/engineers')
    } catch (err) {
      setError(String((err as Error).message ?? err))
    } finally {
      setBusy(null)
    }
  }, [refreshAux])

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
    api.compare(baseline).then(setCompare).catch(() => {})
  }, [route.screen, compare, plan, busy, baseline])

  /** Переключение базового варианта на экране «Эффект». */
  const switchBaseline = useCallback((b: 'tz' | 'smart') => {
    setBaseline(b)
    setCompare(null)
  }, [])

  const changedJobs = useMemo(() => {
    const s = new Set<string>()
    for (const m of plan?.diff?.moved ?? []) s.add(m.job_id)
    for (const a of plan?.diff?.added ?? []) s.add(a.job_id)
    for (const r of plan?.diff?.reordered ?? []) s.add(r.job_id)
    for (const t of plan?.diff?.shifted ?? []) s.add(t.job_id)
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
            <span className="sub" title={dataset?.title}>{dataset
              ? `${dataset.title || 'диспетчерская · Москва'} · ${dayLabel(dataset.date)}`
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

        <div className="params">
          <button className={`ghost${showParams ? ' on' : ''}`}
                  onClick={() => setShowParams((v) => !v)}
                  title="Критерий оптимизации и лимиты расчёта">Параметры</button>
          {showParams && (
            <>
              <div className="params-catch" onClick={() => setShowParams(false)} />
              <div className="params-panel">
                <label className="field">
                  <span>Что важнее сегодня</span>
                  <select value={preset} onChange={(e) => setPreset(e.target.value)}>
                    <option value="default">сбалансированно</option>
                    <option value="staff">меньше персонала (метрика ТЗ)</option>
                    <option value="sla">уложиться в SLA</option>
                    <option value="travel">экономить пробег</option>
                    <option value="balance">ровная загрузка</option>
                  </select>
                </label>
                <label className="field">
                  <span>Секунд на расчёт плана</span>
                  <select value={timeLimit}
                          onChange={(e) => setTimeLimit(Number(e.target.value))}>
                    <option value={5}>5 с — черновик</option>
                    <option value={15}>15 с — обычный</option>
                    <option value={30}>30 с — тщательный</option>
                  </select>
                </label>
                <label className="field">
                  <span>Стабильность при пересчёте</span>
                  <select value={stability}
                          onChange={(e) => setStability(Number(e.target.value))}>
                    <option value={0}>не беречь план</option>
                    <option value={80}>низкая — двигать свободно</option>
                    <option value={200}>средняя</option>
                    <option value={600}>высокая — трогать минимум</option>
                  </select>
                </label>
                <div className="muted" style={{ fontSize: 11.5 }}>
                  Применяются со следующего расчёта. Стабильность — во сколько
                  минут пути обходится передача визита другому инженеру.
                </div>
              </div>
            </>
          )}
        </div>
        <button className="primary" onClick={build} disabled={!!busy}>Построить план</button>
        <button onClick={step} disabled={!!busy || !plan || !hasEventsLeft}>
          Следующее событие ›
        </button>
        <button onClick={() => setPlaying((v) => !v)}
                disabled={(!!busy && !playing) || !plan || !hasEventsLeft}>
          {playing ? '❚❚ Пауза' : '▶ Проиграть день'}
        </button>
        <button className="ghost" onClick={reset} disabled={!!busy}>Сброс</button>
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
            <EngineersScreen plan={plan} engineers={engineers} dataset={dataset}
                             onStaffChange={reloadStaff} />
          )}
          {route.screen === 'engineer' && route.id && (
            <EngineerScreen id={route.id} plan={plan} engineers={engineers}
                            jobs={jobs} dataset={dataset} selectedJob={selectedJob}
                            onSelectJob={(id) => { setSelectedJob(id); go(`/jobs/${id}`) }}
                            onUnavailable={(eid) => fireEvent('engineer_unavailable', eid)}
                            busy={!!busy} />
          )}
          {route.screen === 'jobs' && (
            <JobsScreen jobs={jobs} plan={plan} dataset={dataset}
                        onJobAdded={addJob} />
          )}
          {route.screen === 'job' && route.id && (
            <JobScreen id={route.id} jobs={jobs} plan={plan} explanation={explanation}
                       whyNot={whyNot} busy={!!busy} onPin={pin}
                       onCancel={(jid) => fireEvent('job_cancelled', jid)} />
          )}
          {route.screen === 'backlog' && (
            <BacklogScreen plan={plan} whyNotAll={whyNotAll} />
          )}
          {route.screen === 'effect' && (
            <EffectScreen compare={compare} onBaseline={switchBaseline} />
          )}
          {route.screen === 'reference' && (
            <ReferenceScreen dataset={dataset} engineers={engineers} plan={plan}
                             datasets={datasets} onDataset={changeDataset} />
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

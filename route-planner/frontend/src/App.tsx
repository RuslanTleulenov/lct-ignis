import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  api, type Compare, type DayEvent, type Dataset, type Engineer,
  type Explanation, type Job, type LogEntry, type Plan, type WhyNot,
} from './api'
import { MapView } from './components/MapView'
import { Gantt } from './components/Gantt'
import { KpiBar } from './components/KpiBar'
import { SidePanel, type Tab } from './components/SidePanel'

export default function App() {
  const [dataset, setDataset] = useState<Dataset | null>(null)
  const [engineers, setEngineers] = useState<Engineer[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [events, setEvents] = useState<DayEvent[]>([])
  const [log, setLog] = useState<LogEntry[]>([])
  const [plan, setPlan] = useState<Plan | null>(null)
  const [compare, setCompare] = useState<Compare | null>(null)
  const [whyNotAll, setWhyNotAll] = useState<WhyNot[]>([])

  const [selectedJob, setSelectedJob] = useState<string | null>(null)
  const [selectedEngineer, setSelectedEngineer] = useState<string | null>(null)
  const [explanation, setExplanation] = useState<Explanation | null>(null)
  const [whyNot, setWhyNot] = useState<WhyNot | null>(null)

  const [tab, setTab] = useState<Tab>('explain')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<{ head: string; body: string } | null>(null)
  const [playing, setPlaying] = useState(false)

  const [preset, setPreset] = useState('default')
  const [timeLimit, setTimeLimit] = useState(15)
  const [stability, setStability] = useState(200)

  const busyRef = useRef(false)

  // ---- справочники ----
  useEffect(() => {
    Promise.all([api.dataset(), api.engineers(), api.events()])
      .then(([d, e, ev]) => { setDataset(d); setEngineers(e); setEvents(ev) })
      .catch((err) => setError(String(err.message ?? err)))
    api.plan().then(afterPlan).catch(() => { /* плана ещё нет — это норма */ })
  }, [])

  const refreshAux = useCallback(async () => {
    const [j, l] = await Promise.all([api.jobs(), api.log()])
    setJobs(j)
    setLog(l)
    api.whyNotAll().then(setWhyNotAll).catch(() => setWhyNotAll([]))
  }, [])

  function afterPlan(p: Plan) {
    setPlan(p)
    void refreshAux()
  }

  // ---- операции ----
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
    setSelectedEngineer(null)
    setExplanation(null)
    setCompare(null)
    const p = await run('Считаю план дня…', () => api.build(preset, timeLimit))
    if (p) {
      setToast({
        head: 'План построен',
        body: `Назначено ${p.kpi.jobs_assigned} из ${p.kpi.jobs_total}, ` +
          `в пути ${p.kpi.travel_min} мин, нарушений SLA ${p.kpi.sla_violations}`,
      })
      api.compare().then(setCompare).catch(() => { /* не критично */ })
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
    const p = await run(
      engineerId ? 'Закрепляю и пересчитываю…' : 'Снимаю закрепление…',
      () => api.pin(jobId, engineerId),
    )
    if (!p) return
    // Цену ручного решения показываем сразу и без прикрас: диспетчер имеет
    // право поступить по-своему, но должен видеть, во что это обошлось.
    const c = p.cost
    const parts: string[] = []
    const sign = (n: number) => (n > 0 ? `+${n}` : String(n))
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
    setPlan(null)
    setCompare(null)
    setSelectedJob(null)
    setSelectedEngineer(null)
    setExplanation(null)
    setWhyNotAll([])
    setToast(null)
    await refreshAux()
  }, [refreshAux])

  // ---- автопрогон дня ----
  useEffect(() => {
    if (!playing || busy) return
    const remaining = events.filter((e) => e.at > (plan?.now ?? '00:00'))
    if (!remaining.length) { setPlaying(false); return }
    const id = setTimeout(() => { void step() }, 900)
    return () => clearTimeout(id)
  }, [playing, busy, plan, events, step])

  // ---- WebSocket: план мог поменять другой клиент ----
  useEffect(() => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    let socket: WebSocket | null = null
    try {
      socket = new WebSocket(`${proto}://${location.host}/ws`)
    } catch {
      return
    }
    socket.onmessage = (msg) => {
      const data = JSON.parse(msg.data)
      if (data.type === 'plan' && data.plan) {
        setPlan((prev) => {
          const cur = Number(prev?.id?.split('-')[1] ?? 0)
          return data.version > cur ? data.plan : prev
        })
      }
    }
    return () => socket?.close()
  }, [])

  // ---- выбор заявки ----
  useEffect(() => {
    if (!selectedJob || !plan) { setExplanation(null); setWhyNot(null); return }
    setExplanation(null)
    setWhyNot(null)
    const assigned = plan.routes.some((r) =>
      r.stops.some((s) => s.job_id === selectedJob))
    if (assigned) {
      api.explain(selectedJob).then(setExplanation).catch(() => setExplanation(null))
    } else {
      api.whyNot(selectedJob).then(setWhyNot).catch(() => setWhyNot(null))
    }
  }, [selectedJob, plan])

  const selectJob = useCallback((id: string) => {
    setSelectedJob(id)
    const assigned = plan?.routes.some((r) => r.stops.some((s) => s.job_id === id))
    setTab(assigned ? 'explain' : 'why')
    if (assigned) {
      const route = plan?.routes.find((r) => r.stops.some((s) => s.job_id === id))
      if (route) setSelectedEngineer(route.engineer_id)
    }
  }, [plan])

  const selectEngineer = useCallback((id: string | null) => {
    setSelectedEngineer(id)
    if (id) { setSelectedJob(null); setTab('explain') }
  }, [])

  const changedJobs = useMemo(() => {
    const s = new Set<string>()
    for (const m of plan?.diff?.moved ?? []) s.add(m.job_id)
    for (const a of plan?.diff?.added ?? []) s.add(a.job_id)
    return s
  }, [plan])

  useEffect(() => {
    if (!toast) return
    const id = setTimeout(() => setToast(null), 6000)
    return () => clearTimeout(id)
  }, [toast])

  const hasEventsLeft = events.some((e) => e.at > (plan?.now ?? '00:00'))

  return (
    <div className="app">
      <div className="header">
        <h1>Диспетчерская</h1>
        <span className="sub">
          {dataset ? `${dataset.date} · ${dataset.counts.jobs} заявок · ${dataset.counts.engineers} инженеров` : 'загрузка…'}
        </span>
        <span className="spacer" />

        <select value={preset} onChange={(e) => setPreset(e.target.value)}
                title="Что важнее сегодня">
          <option value="default">Сбалансировано</option>
          <option value="sla">Уложиться в SLA</option>
          <option value="travel">Экономить пробег</option>
          <option value="balance">Ровная загрузка</option>
        </select>
        <select value={timeLimit} onChange={(e) => setTimeLimit(Number(e.target.value))}
                title="Секунд на расчёт">
          <option value={5}>5 с</option>
          <option value={15}>15 с</option>
          <option value={30}>30 с</option>
        </select>
        <button className="primary" onClick={build} disabled={!!busy}>
          Построить план
        </button>

        {plan && Object.keys(plan.pins).length > 0 && (
          <span className="badge out" title="Заявки, закреплённые диспетчером вручную">
            🔒 {Object.keys(plan.pins).length}
          </span>
        )}
        <span className="clock">{plan?.now ?? '—'}</span>
        <button onClick={step} disabled={!!busy || !plan || !hasEventsLeft}>
          Следующее событие
        </button>
        <button onClick={() => setPlaying((v) => !v)}
                disabled={!!busy && !playing || !plan || !hasEventsLeft}>
          {playing ? '⏸ Пауза' : '▶ Проиграть день'}
        </button>
        <select value={stability} onChange={(e) => setStability(Number(e.target.value))}
                title="Во сколько минут пути обходится перенос визита другому инженеру">
          <option value={0}>без штрафа</option>
          <option value={80}>стабильность 80</option>
          <option value={200}>стабильность 200</option>
          <option value={600}>стабильность 600</option>
        </select>
        <button onClick={reset} disabled={!!busy}>Сброс</button>
      </div>

      {error && <div className="err">{error}</div>}
      <KpiBar plan={plan} />

      <div className="middle">
        <div className="map-wrap">
          <MapView
            plan={plan}
            dataset={dataset}
            selectedJob={selectedJob}
            selectedEngineer={selectedEngineer}
            changedJobs={changedJobs}
            onSelectJob={selectJob}
            onSelectEngineer={selectEngineer}
          />
          <div className="legend">
            <div className="row">
              <span className="sw" style={{ background: '#12151c', border: '2px solid #f03e3e' }} />
              не назначена
            </div>
            <div className="row">
              <span className="sw" style={{ background: '#1f2733', border: '2px solid #f1c40f' }} />
              склад
            </div>
            <div className="row">
              <span className="sw" style={{ background: '#fff' }} />
              изменено пересчётом
            </div>
          </div>
          {busy && (
            <div className="busy">
              <div className="spinner" />
              <div>{busy}</div>
            </div>
          )}
          {toast && (
            <div className="toast">
              <div className="hd">{toast.head}</div>
              {toast.body && <div className="bd">{toast.body}</div>}
            </div>
          )}
          {!plan && !busy && (
            <div className="busy" style={{ background: 'rgba(10,13,18,0.55)' }}>
              <div style={{ textAlign: 'center', maxWidth: 380 }}>
                <div style={{ fontSize: 15, marginBottom: 6 }}>План на день не построен</div>
                <div style={{ color: 'var(--text-dim)' }}>
                  Нажмите «Построить план». Затем «Проиграть день» — увидите, как
                  система перестраивает маршруты по ходу событий.
                </div>
              </div>
            </div>
          )}
        </div>

        <SidePanel
          tab={tab} onTab={setTab}
          plan={plan} engineers={engineers} jobs={jobs}
          events={events} log={log}
          explanation={explanation} whyNot={whyNot} whyNotAll={whyNotAll}
          compare={compare}
          selectedJob={selectedJob} selectedEngineer={selectedEngineer}
          busy={!!busy}
          onSelectJob={selectJob} onSelectEngineer={selectEngineer}
          onPin={pin}
        />
      </div>

      <Gantt
        plan={plan} engineers={engineers}
        selectedJob={selectedJob} selectedEngineer={selectedEngineer}
        changedJobs={changedJobs}
        onSelectJob={selectJob} onSelectEngineer={selectEngineer}
      />
    </div>
  )
}

import { useEffect, useMemo, useState } from 'react'
import { api, type Engineer as Eng, type Job, type Plan, type RouteExplanation } from '../api'
import { engineerColor, PRIORITY_COLOR, VEHICLE_LABEL } from '../colors'
import { dur, initials, num, plural } from '../format'
import { go } from '../router'
import { MapView } from '../components/MapView'
import { Timeline } from '../components/Timeline'

interface Props {
  id: string
  plan: Plan | null
  engineers: Eng[]
  jobs: Job[]
  dataset: import('../api').Dataset | null
  selectedJob: string | null
  onSelectJob: (id: string) => void
  onUnavailable: (engineerId: string) => void
  busy: boolean
}

export function EngineerScreen({ id, plan, engineers, jobs, dataset,
                                 selectedJob, onSelectJob, onUnavailable, busy: working }: Props) {
  const eng = engineers.find((e) => e.id === id)
  const route = plan?.routes.find((r) => r.engineer_id === id) ?? null

  // Объяснение маршрута считается по готовому плану — перечитываем при каждой
  // его версии, иначе после пересчёта на карточке останется старый разбор.
  const [why, setWhy] = useState<RouteExplanation | null>(null)
  useEffect(() => {
    if (!plan || !route) { setWhy(null); return }
    let alive = true
    api.explainRoute(id).then((r) => { if (alive) setWhy(r) }).catch(() => setWhy(null))
    return () => { alive = false }
  }, [id, plan, route])

  const order = useMemo(() => engineers.map((e) => e.id), [engineers])
  const pos = order.indexOf(id)
  const prev = pos > 0 ? order[pos - 1] : null
  const next = pos >= 0 && pos < order.length - 1 ? order[pos + 1] : null

  const avg = useMemo(() => {
    const active = (plan?.routes ?? []).filter((r) => r.job_count)
    if (!active.length) return null
    return {
      work: Math.round(active.reduce((s, r) => s + r.work_min, 0) / active.length),
      travel: Math.round(active.reduce((s, r) => s + r.travel_min, 0) / active.length),
      busy: active.map((r) => r.work_min + r.travel_min),
    }
  }, [plan])

  if (!eng) return <div className="empty">Инженер не найден</div>

  const color = engineerColor(eng.id)
  const visits = route?.stops.filter((s) => s.kind === 'job') ?? []
  const busy = route ? route.work_min + route.travel_min : 0
  const total = busy + (route?.lunch_min ?? 0) + (route?.wait_min ?? 0)

  return (
    <div className="stack">
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <a href="#/engineers" className="dim" style={{ fontSize: 13 }}>← Все инженеры</a>
        <span className="spacer" />
        {plan && (
          <button className="ghost" disabled={working}
                  title="Заболел, авария, отозван — заявки уйдут другим с этого момента"
                  onClick={() => onUnavailable(id)}>Вывести из смены</button>
        )}
        <button className="ghost" disabled={!prev}
                onClick={() => prev && go(`/engineers/${prev}`)}>‹ Пред.</button>
        <button className="ghost" disabled={!next}
                onClick={() => next && go(`/engineers/${next}`)}>След. ›</button>
      </div>

      {/* ---- шапка ---- */}
      <div className="card" style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <span className="avatar" style={{ background: color }}>{initials(eng.name)}</span>
        <div style={{ minWidth: 0 }}>
          <h1 style={{ fontSize: 21, fontWeight: 800 }}>{eng.name}</h1>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 6 }}>
            <span className="pill">{VEHICLE_LABEL[eng.vehicle_type]}</span>
            <span className="pill">смена {eng.shift[0]}–{eng.shift[1]}</span>
            {eng.skills.map((s) => (
              <span key={s.specialization} className="pill accent">
                {s.specialization_name}{dataset?.uses_levels ? ` ур. ${s.level}` : ''}
              </span>
            ))}
            {route?.pickup_warehouse && (
              <span className="pill">получение оборудования: склад {route.pickup_warehouse}</span>
            )}
            {dataset?.uses_equipment && !eng.can_carry_bulky && (
              <span className="pill warn">без перевозки габарита</span>
            )}
          </div>
        </div>
        <span className="spacer" />
        <div style={{ textAlign: 'right' }}>
          <div className="muted" style={{ fontSize: 12 }}>визитов</div>
          <b className="num" style={{ fontSize: 22 }}>{route?.job_count ?? 0}</b>
        </div>
      </div>

      {!route?.job_count ? (
        <div className="card">
          <div className="empty">
            <div style={{ fontSize: 15, color: 'var(--ink)', marginBottom: 6 }}>
              Заявки на смену не назначены
            </div>
            <span style={{ fontSize: 12.5 }}>
              Подходящих по квалификации заявок не осталось: их приняли инженеры,
              у которых объекты оказались ближе по маршруту.
            </span>
          </div>
        </div>
      ) : (
        <>
          {/* ---- лента дня ---- */}
          <div className="card">
            <h2>Лента дня
              <span className="hint">маршрут {route.start}–{route.end}</span>
            </h2>
            <Timeline route={route} plan={plan} selected={selectedJob}
                      onSelect={onSelectJob} />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', gap: 14 }}>
            {/* ---- карта ---- */}
            <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
              <div style={{ padding: '16px 20px 10px' }}>
                <h2 style={{ marginBottom: 0 }}>Маршрут
                  <span className="hint">
                    {num(route.travel_km, 1)} км · {dur(route.travel_min)} в пути
                  </span>
                </h2>
              </div>
              <div style={{ position: 'relative', height: 420 }}>
                <MapView plan={plan} dataset={dataset} selectedJob={selectedJob}
                         selectedEngineer={eng.id} focusEngineer={eng.id}
                         changedJobs={new Set()} onSelectJob={onSelectJob}
                         onSelectEngineer={() => {}} />
              </div>
            </div>

            <div className="stack">
              {/* ---- загрузка ---- */}
              <div className="card">
                <h2>Загрузка<span className="hint">занятость {dur(busy)}</span></h2>
                <div className="load">
                  <span className="work" style={{ width: `${route.work_min / total * 100}%` }} />
                  <span className="travel" style={{ width: `${route.travel_min / total * 100}%` }} />
                  <span className="lunch" style={{ width: `${route.lunch_min / total * 100}%` }} />
                  <span className="idle" style={{ width: `${route.wait_min / total * 100}%` }} />
                </div>
                <div className="legend" style={{ marginTop: 8 }}>
                  <span><i style={{ background: 'var(--accent)' }} />работа {route.work_min}</span>
                  <span><i style={{ background: '#6b6b7a' }} />дорога {route.travel_min}</span>
                  <span><i style={{ background: '#3a3a44' }} />обед {route.lunch_min}</span>
                  <span><i style={{ background: 'var(--danger-soft)' }} />простой {route.wait_min}</span>
                </div>
                {avg && (
                  <div className="muted" style={{ fontSize: 11.5, marginTop: 10 }}>
                    В среднем по службе: работа {avg.work} мин, дорога {avg.travel} мин.
                    Разброс занятости — от {Math.min(...avg.busy)} до {Math.max(...avg.busy)} мин.
                  </div>
                )}
              </div>

              {/* ---- оборудование: только у наборов, где оно есть ---- */}
              {dataset?.uses_equipment && (
              <div className="card">
                <h2>Оборудование на руках
                  <span className="hint">
                    {eng.equipment.length}{' '}
                    {plural(eng.equipment.length, 'позиция', 'позиции', 'позиций')}
                  </span>
                </h2>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {eng.equipment.map((q) => (
                    <span key={q.id} className="pill">{q.name}</span>
                  ))}
                </div>
              </div>
              )}
            </div>
          </div>

          {/* ---- почему маршрут такой: ТЗ, п. 2.4.2 ---- */}
          {why && (
            <div className="card">
              <h2>Почему маршрут такой<span className="hint">{why.headline}</span></h2>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 18 }}>
                <div>
                  <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase',
                    letterSpacing: .4, marginBottom: 6 }}>Что диктовали ограничения</div>
                  {why.constraints.map((t, i) => (
                    <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 6, fontSize: 13 }}>
                      <span style={{ color: 'var(--accent)' }}>●</span><span>{t}</span>
                    </div>
                  ))}
                </div>
                <div>
                  <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase',
                    letterSpacing: .4, marginBottom: 6 }}>Что выбрала оптимизация</div>
                  {why.choices.map((t, i) => (
                    <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 6, fontSize: 13 }}>
                      <span style={{ color: 'var(--info)' }}>●</span><span>{t}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* ---- визиты ---- */}
          <div className="card">
            <h2>Визиты по порядку</h2>
            <table className="grid">
              <thead>
                <tr>
                  <th style={{ width: 28 }}>#</th>
                  <th style={{ width: 118 }}>Время визита</th>
                  <th>Заказчик и работа</th>
                  <th style={{ width: 150 }}>Окно клиента</th>
                  <th className="r" style={{ width: 120 }}>От предыдущей</th>
                  <th className="r" style={{ width: 110 }}>Статус</th>
                </tr>
              </thead>
              <tbody>
                {visits.map((s, i) => {
                  const job = jobs.find((j) => j.id === s.job_id)
                  const done = plan?.completed.includes(s.job_id!)
                  return (
                    <tr key={s.job_id} className="click"
                        onClick={() => go(`/jobs/${s.job_id}`)}>
                      <td className="muted num">{i + 1}</td>
                      <td className="num">{s.service_start}–{s.service_end}</td>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                          <span className="dot" style={{
                            background: PRIORITY_COLOR[s.priority ?? 'normal'] }} />
                          <b>{s.customer}</b>
                          {s.priority === 'urgent' && (
                            <span className="muted" style={{ color: PRIORITY_COLOR.urgent }}>срочная</span>
                          )}
                        </div>
                        <div className="muted" style={{ fontSize: 12 }}>
                          {s.work_type} · {job?.address ?? s.district}
                        </div>
                      </td>
                      <td className="num">
                        {s.window?.[0]}–{s.window?.[1]}{' '}
                        <span className={s.window_hard ? 'pill warn' : 'muted'}
                              style={{ fontSize: 11 }}>
                          {s.window_hard ? 'жёсткое' : 'мягкое'}
                        </span>
                      </td>
                      <td className="r num">
                        {s.travel_min} мин · {num(s.travel_km, 1)} км
                        {s.wait_min > 0 && (
                          <div className="muted" style={{ fontSize: 11 }}>
                            ожидание {s.wait_min} мин
                          </div>
                        )}
                        {s.via_metro && (
                          <div style={{ fontSize: 11, color: '#7aa7ff' }}>через метро</div>
                        )}
                      </td>
                      <td className="r">
                        {s.sla_late_min > 0
                          ? <span className="pill danger">SLA +{s.sla_late_min}</span>
                          : done ? <span className="pill ok">выполнено</span>
                            : <span className="muted" style={{ fontSize: 12 }}>впереди</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

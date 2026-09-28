import { useMemo, useState } from 'react'
import type { Dataset, Explanation, Job, JobInput, Plan, WhyNot } from '../api'
import { engineerColor, PRIORITY_COLOR, PRIORITY_LABEL } from '../colors'
import { JobForm } from '../components/JobForm'
import { initials, num } from '../format'
import { go } from '../router'

// Формулировки статусов заданы макетом.
/** Как геокодер нашёл адрес: всё, кроме точного дома, показываем явно. */
const GEO_NOTE: Record<string, string> = {
  'house~': 'дом без корпуса',
  street: 'только улица',
  district: 'центр района',
  none: 'не найден',
}

const STATUS_LABEL: Record<string, string> = {
  planned: 'в плане', done: 'выполнена',
  unassigned: 'не назначена', new: 'новая', cancelled: 'отменена',
}

export function JobsScreen({ jobs, plan, dataset, onJobAdded }: {
  jobs: Job[]
  plan: Plan | null
  dataset: Dataset | null
  /** Принять заявку: сервис пересчитает день сам и вернёт новый план. */
  onJobAdded: (body: JobInput) => Promise<void>
}) {
  const [status, setStatus] = useState('all')
  const [prio, setPrio] = useState('all')
  const [query, setQuery] = useState('')
  const [creating, setCreating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  async function submit(body: JobInput) {
    setSaving(true)
    setFormError(null)
    try {
      await onJobAdded(body)
      setCreating(false)
    } catch (err) {
      setFormError(String((err as Error).message ?? err))
    } finally {
      setSaving(false)
    }
  }

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    return jobs.filter((j) =>
      (status === 'all' || j.status === status)
      && (prio === 'all' || j.priority === prio)
      && (!q || j.customer.toLowerCase().includes(q)
        || j.work_type.toLowerCase().includes(q)
        || j.district.toLowerCase().includes(q)))
  }, [jobs, status, prio, query])

  const byStatus = useMemo(() => {
    const c: Record<string, number> = {}
    for (const j of jobs) c[j.status] = (c[j.status] ?? 0) + 1
    return c
  }, [jobs])

  return (
    <div className="stack">
      <div className="card">
        <h2>Заявки<span className="hint">{rows.length} из {jobs.length}</span></h2>

        <div style={{ display: 'flex', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
          <input placeholder="Заказчик, тип работ, район" value={query}
                 onChange={(e) => setQuery(e.target.value)} style={{ width: 280 }} />
          <button className="primary" onClick={() => setCreating(true)}>
            Новая заявка
          </button>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="all">все статусы</option>
            {Object.entries(byStatus).map(([s, n]) => (
              <option key={s} value={s}>{STATUS_LABEL[s] ?? s} ({n})</option>
            ))}
          </select>
          <select value={prio} onChange={(e) => setPrio(e.target.value)}>
            <option value="all">все приоритеты</option>
            {['urgent', 'normal'].map((p) => (
              <option key={p} value={p}>{PRIORITY_LABEL[p]}</option>
            ))}
          </select>
        </div>

        <table className="grid">
          <thead>
            <tr>
              <th style={{ width: 46 }}>Прио</th>
              <th>Заказчик и работа</th>
              <th style={{ width: 130 }}>Район</th>
              <th style={{ width: 140 }}>Окно</th>
              <th style={{ width: 180 }}>Исполнитель</th>
              <th className="r" style={{ width: 120 }}>Статус</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((j) => {
              const route = plan?.routes.find((r) => r.engineer_id === j.engineer_id)
              const stop = route?.stops.find((s) => s.job_id === j.id)
              return (
                <tr key={j.id} className="click" onClick={() => go(`/jobs/${j.id}`)}>
                  <td>
                    <span className="pill" style={{
                      background: PRIORITY_COLOR[j.priority] + '22',
                      color: PRIORITY_COLOR[j.priority], borderColor: 'transparent',
                      fontWeight: 700, padding: '3px 9px',
                    }}>{PRIORITY_LABEL[j.priority]}</span>
                  </td>
                  <td>
                    <b>{j.customer}</b>
                    <div className="muted" style={{ fontSize: 12 }}>{j.work_type}</div>
                  </td>
                  <td className="dim">{j.district}</td>
                  <td className="num dim">
                    {j.window[0]}–{j.window[1]}
                    {j.window_hard && <span className="pill warn" style={{
                      marginLeft: 6, fontSize: 10, padding: '2px 7px' }}>жёсткое</span>}
                  </td>
                  <td>
                    {route ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                        <span className="dot" style={{ background: engineerColor(route.engineer_id) }} />
                        <span>{route.engineer_name}</span>
                        {stop && <span className="muted num">{stop.service_start}</span>}
                      </div>
                    ) : <span className="muted">—</span>}
                  </td>
                  <td className="r">
                    <span className={'pill ' + (j.status === 'unassigned' ? 'danger'
                      : j.status === 'done' ? 'ok' : j.status === 'new' ? '' : 'info')}>
                      {STATUS_LABEL[j.status]}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {!rows.length && <div className="empty">Ничего не найдено</div>}
      </div>

      {creating && dataset && (
        <JobForm
          dataset={dataset}
          now={plan?.now ?? dataset.day[0]}
          busy={saving}
          error={formError}
          onSubmit={submit}
          onClose={() => { setCreating(false); setFormError(null) }}
        />
      )}
    </div>
  )
}

// --------------------------------------------------------------------------

interface JobProps {
  id: string
  jobs: Job[]
  plan: Plan | null
  explanation: Explanation | null
  whyNot: WhyNot | null
  busy: boolean
  onPin: (jobId: string, engineerId: string | null) => void
  onCancel: (jobId: string) => void
}

export function JobScreen({ id, jobs, plan, explanation, whyNot, busy, onPin, onCancel }: JobProps) {
  const job = jobs.find((j) => j.id === id)
  if (!job) return <div className="empty">Заявка не найдена</div>

  const route = plan?.routes.find((r) => r.stops.some((s) => s.job_id === id))
  const stop = route?.stops.find((s) => s.job_id === id)
  const pinnedTo = plan?.pins[id]
  const cancellable = !!plan && job.status !== 'done' && job.status !== 'cancelled'

  return (
    <div className="stack">
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <a href="#/jobs" className="dim" style={{ fontSize: 13 }}>← Все заявки</a>
      </div>

      <div className="card">
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14 }}>
          <span className="pill" style={{
            background: PRIORITY_COLOR[job.priority] + '22',
            color: PRIORITY_COLOR[job.priority], borderColor: 'transparent',
            fontWeight: 700, fontSize: 13,
          }}>{PRIORITY_LABEL[job.priority]}</span>
          <div style={{ minWidth: 0, flex: 1 }}>
            <h1 style={{ fontSize: 20, fontWeight: 800 }}>{job.customer}</h1>
            <div className="dim">{job.work_type}</div>
          </div>
          {pinnedTo && <span className="pill accent">закреплена</span>}
          {cancellable && (
            <button className="ghost" disabled={busy} title="Клиент отменил визит — пересчитать день"
                    onClick={() => onCancel(id)}>Отменить заявку</button>
          )}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginTop: 16 }}>
          <Field k="Адрес" v={job.geo_precision === 'house'
            ? job.address
            : `${job.address} · координаты приблизительные (${GEO_NOTE[job.geo_precision] ?? job.geo_precision})`} />
          <Field k="Окно клиента" v={`${job.window[0]}–${job.window[1]}${job.window_hard ? ' (жёсткое)' : ''}`} />
          <Field k="SLA до" v={job.sla_deadline} />
          <Field k="Навык" v={job.min_level > 1
            ? `${job.specialization_name}, ур. ${job.min_level}` : job.specialization_name} />
          <Field k="Длительность" v={`${job.duration_min} мин`} />
          <Field k="Транспорт" v={job.required_transport_label ?? 'не ограничен'} />
          {job.required_equipment.length > 0 && (
            <Field k="Оборудование" v={job.required_equipment
              .map((q) => q.bulky ? `${q.name} (габарит)` : q.name).join(', ')} />
          )}
        </div>
      </div>

      {stop && route && (
        <div className="card">
          <h2>Назначение</h2>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
            <span className="avatar sm" style={{ background: engineerColor(route.engineer_id) }}>
              {initials(route.engineer_name)}
            </span>
            <a href={`#/engineers/${route.engineer_id}`}><b>{route.engineer_name}</b></a>
            <span className="pill num">{stop.service_start}–{stop.service_end}</span>
            <span className="pill num">{stop.travel_min} мин · {num(stop.travel_km, 1)} км</span>
            {stop.wait_min > 0 && <span className="pill">ожидание {stop.wait_min} мин</span>}
            {stop.sla_late_min > 0 && <span className="pill danger">SLA +{stop.sla_late_min} мин</span>}
            {pinnedTo && (
              <button style={{ marginLeft: 'auto' }} disabled={busy}
                      onClick={() => onPin(id, null)}>Снять закрепление</button>
            )}
          </div>
        </div>
      )}

      {explanation && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <div className="card">
            <h2>Почему {explanation.engineer_name.split(' ')[0]}</h2>
            <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--ink-75)' }}>
              {explanation.choice.map((r, i) => <li key={i} style={{ marginBottom: 5 }}>{r}</li>)}
            </ul>
            <h2 style={{ marginTop: 16 }}>Почему в это время</h2>
            <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--ink-75)' }}>
              {explanation.timing.map((r, i) => <li key={i} style={{ marginBottom: 5 }}>{r}</li>)}
            </ul>
          </div>

          <div className="card">
            <h2>Альтернативы
              <span className="hint">что было бы у других допустимых</span>
            </h2>
            {!explanation.alternatives.length && (
              <p className="dim">Других допустимых исполнителей нет.</p>
            )}
            {explanation.alternatives.map((a) => (
              <div key={a.engineer_id} className="card sunk" style={{ marginBottom: 8, padding: 12 }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                  <span style={{ color: a.possible ? 'var(--ok)' : 'var(--danger)' }}>
                    {a.possible ? '○' : '✗'}
                  </span>
                  <div style={{ flex: 1 }}>
                    <b>{a.engineer_name}</b>
                    <div className="dim" style={{ fontSize: 12.5 }}>{a.detail}</div>
                  </div>
                </div>
                {a.possible && (
                  <button style={{ marginTop: 8 }} disabled={busy}
                          onClick={() => onPin(id, a.engineer_id)}>
                    Передать {a.engineer_name.split(' ')[0]}
                  </button>
                )}
              </div>
            ))}
            <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
              Указана стоимость включения заявки в текущий маршрут исполнителя.
              Итоговая величина определяется после полного пересчёта и, как
              правило, выше: смещаются и остальные визиты.
            </div>
          </div>
        </div>
      )}

      {job.status === 'cancelled' && (
        <div className="card">
          <h2>Отменена</h2>
          <p className="dim">Клиент отменил визит — заявка снята с плана, остаток дня
            пересчитан без неё.</p>
        </div>
      )}

      {whyNot && job.status !== 'cancelled' && (
        <div className="card">
          <h2>Не назначена — вердикт</h2>
          <p style={{ color: 'var(--ink)' }}>{whyNot.verdict}</p>
          {whyNot.qualified.map((b) => (
            <div key={b.engineer_id} style={{ display: 'flex', gap: 8, marginTop: 6 }}>
              <span style={{ color: 'var(--danger)' }}>✗</span>
              <span><b>{b.engineer_name}</b>: <span className="dim">{b.detail}</span></span>
            </div>
          ))}
          <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
            Из {whyNot.engineers_total} инженеров службы требуемый навык есть
            у {whyNot.skill_ok}; остальным мешают обязательные ограничения выше.
          </div>
        </div>
      )}
    </div>
  )
}

function Field({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <div className="muted" style={{ fontSize: 11 }}>{k}</div>
      <div>{v}</div>
    </div>
  )
}

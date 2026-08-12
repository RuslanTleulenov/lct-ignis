import { useMemo, useState } from 'react'
import type { Engineer, Plan } from '../api'
import { engineerColor, VEHICLE_LABEL } from '../colors'
import { dur, initials } from '../format'
import { go } from '../router'

/**
 * Триаж по инженерам: кто перегружен, кто простаивает.
 *
 * Реальный разброс занятости — почти втрое (177–485 мин), и это первое, что
 * диспетчер должен увидеть. Инженеров без заявок не прячем в конец: пустой
 * день это тоже проблема, просто другая.
 */

type Sort = 'busy' | 'jobs' | 'name' | 'travel'

interface Props {
  plan: Plan | null
  engineers: Engineer[]
}

export function EngineersScreen({ plan, engineers }: Props) {
  const [sort, setSort] = useState<Sort>('busy')
  const [query, setQuery] = useState('')

  const rows = useMemo(() => {
    const byId = new Map((plan?.routes ?? []).map((r) => [r.engineer_id, r]))
    const list = engineers.map((e) => {
      const r = byId.get(e.id)
      return {
        eng: e,
        route: r ?? null,
        jobs: r?.job_count ?? 0,
        work: r?.work_min ?? 0,
        travel: r?.travel_min ?? 0,
        lunch: r?.lunch_min ?? 0,
        idle: r?.wait_min ?? 0,
        busy: (r?.work_min ?? 0) + (r?.travel_min ?? 0),
        late: r?.stops.filter((s) => s.sla_late_min > 0).length ?? 0,
      }
    })
    const q = query.trim().toLowerCase()
    const filtered = q
      ? list.filter((x) => x.eng.name.toLowerCase().includes(q)
        || x.eng.skills.some((s) => s.specialization_name.toLowerCase().includes(q)))
      : list
    const cmp: Record<Sort, (a: typeof list[0], b: typeof list[0]) => number> = {
      busy: (a, b) => b.busy - a.busy,
      jobs: (a, b) => b.jobs - a.jobs,
      travel: (a, b) => b.travel - a.travel,
      name: (a, b) => a.eng.name.localeCompare(b.eng.name, 'ru'),
    }
    return [...filtered].sort(cmp[sort])
  }, [engineers, plan, sort, query])

  const maxBusy = Math.max(1, ...rows.map((r) => r.busy + r.lunch + r.idle))
  const idle = rows.filter((r) => !r.jobs).length

  return (
    <div className="stack">
      <div className="card">
        <h2>Все инженеры
          <span className="hint">
            кто чем занят и как загружен · клик по строке — карточка
          </span>
        </h2>

        <div style={{ display: 'flex', gap: 10, marginBottom: 14, alignItems: 'center' }}>
          <input placeholder="Поиск по имени или специализации" value={query}
                 onChange={(e) => setQuery(e.target.value)} style={{ width: 280 }} />
          <span className="spacer" />
          {idle > 0 && (
            <span className="pill warn">{idle} без заявок</span>
          )}
          <select value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
            <option value="busy">по занятости</option>
            <option value="jobs">по числу заявок</option>
            <option value="travel">по времени в пути</option>
            <option value="name">по алфавиту</option>
          </select>
        </div>

        <table className="grid">
          <thead>
            <tr>
              <th>Инженер</th>
              <th style={{ width: 130 }}>Транспорт</th>
              <th style={{ width: 110 }}>Смена</th>
              <th className="r" style={{ width: 70 }}>Заявок</th>
              <th style={{ width: 260 }}>Загрузка за день</th>
              <th className="r" style={{ width: 96 }}>Занятость</th>
              <th className="r" style={{ width: 80 }}>SLA</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ eng, jobs, work, travel, lunch, idle: wait, busy, late }) => (
              <tr key={eng.id} className="click" onClick={() => go(`/engineers/${eng.id}`)}>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span className="avatar sm" style={{ background: engineerColor(eng.id) }}>
                      {initials(eng.name)}
                    </span>
                    <div style={{ minWidth: 0 }}>
                      <b>{eng.name}</b>
                      <div className="muted" style={{ fontSize: 11.5 }}>
                        {eng.skills.map((s) => `${s.specialization_name} ур.${s.level}`).join(' · ')}
                      </div>
                    </div>
                  </div>
                </td>
                <td className="dim">
                  {VEHICLE_LABEL[eng.vehicle_type]}
                  {!eng.can_carry_bulky && (
                    <div className="muted" style={{ fontSize: 11 }}>без габарита</div>
                  )}
                </td>
                <td className="dim num">{eng.shift[0]}–{eng.shift[1]}</td>
                <td className="r num">
                  {jobs || <span className="muted">—</span>}
                </td>
                <td>
                  {jobs ? (
                    <div className="load" style={{ width: `${(busy + lunch + wait) / maxBusy * 100}%` }}>
                      <span className="work" style={{ width: `${work / (busy + lunch + wait) * 100}%` }} />
                      <span className="travel" style={{ width: `${travel / (busy + lunch + wait) * 100}%` }} />
                      <span className="lunch" style={{ width: `${lunch / (busy + lunch + wait) * 100}%` }} />
                      <span className="idle" style={{ width: `${wait / (busy + lunch + wait) * 100}%` }} />
                    </div>
                  ) : <span className="muted" style={{ fontSize: 12 }}>день пуст</span>}
                </td>
                <td className="r num">{jobs ? dur(busy) : '—'}</td>
                <td className="r">
                  {late > 0 ? <span className="pill danger">{late}</span>
                    : <span className="muted">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="legend" style={{ marginTop: 12 }}>
          <span><i style={{ background: 'var(--accent)' }} />работа</span>
          <span><i style={{ background: '#6b6b7a' }} />дорога</span>
          <span><i style={{ background: '#3a3a44' }} />обед</span>
          <span><i style={{ background: 'var(--danger-soft)' }} />простой</span>
        </div>
      </div>
    </div>
  )
}

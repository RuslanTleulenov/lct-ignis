import { toMin, type DayEvent, type Dataset, type Engineer, type LogEntry,
  type Plan, type Route } from '../api'
import { engineerColor, VEHICLE_LABEL } from '../colors'
import { dur, initials, num } from '../format'
import { go } from '../router'
import { MapView } from '../components/MapView'

interface Props {
  plan: Plan | null
  dataset: Dataset | null
  engineers: Engineer[]
  events: DayEvent[]
  log: LogEntry[]
  selectedJob: string | null
  changedJobs: Set<string>
  onSelectJob: (id: string) => void
}

/** Чем инженер занят в момент `now`: визит, переезд или смена не началась. */
function currentActivity(route: Route, now: string | null):
  { text: string; live: boolean } {
  if (!now) return { text: 'смена не началась', live: false }
  const t = toMin(now)
  if (t < toMin(route.start)) return { text: 'смена не началась', live: false }
  if (t > toMin(route.end)) return { text: 'день завершён', live: false }
  for (const s of route.stops) {
    if (s.kind !== 'job') continue
    if (t >= toMin(s.service_start) && t <= toMin(s.service_end)) {
      return { text: `визит · ${s.customer ?? ''}`, live: true }
    }
  }
  return { text: 'в пути', live: true }
}

// Подписи событий заданы макетом.
const EVENT_LABEL: Record<string, string> = {
  job_created: 'Новая заявка',
  job_cancelled: 'Отмена',
  job_overrun: 'Визит затянулся',
  engineer_unavailable: 'Больничный',
  vehicle_breakdown: 'Поломка машины',
}

export function OverviewScreen({ plan, dataset, engineers, events, log,
                                 selectedJob, changedJobs, onSelectJob }: Props) {
  if (!plan) {
    return (
      <div className="card">
        <div className="empty">
          <div style={{ fontSize: 16, color: 'var(--ink)', marginBottom: 6 }}>
            План на день ещё не построен
          </div>
          Нажмите «Построить план», чтобы распределить заявки между инженерами
          и рассчитать маршруты.
        </div>
      </div>
    )
  }

  const k = plan.kpi
  const busy = plan.routes.filter((r) => r.job_count).map((r) => r.work_min + r.travel_min)
  const spread = busy.length ? Math.max(...busy) / Math.max(1, Math.min(...busy)) : 0

  // Строки таблицы «Все инженеры»: занятость, загрузка и чем человек занят
  // прямо сейчас — по времени плана, а не по расписанию вообще.
  const rows = plan.routes
    .filter((r) => r.job_count)
    .map((route) => {
      const eng = engineers.find((e) => e.id === route.engineer_id)
      const load = {
        work: route.work_min, travel: route.travel_min,
        lunch: route.lunch_min, idle: route.wait_min,
        total: Math.max(1, route.work_min + route.travel_min
          + route.lunch_min + route.wait_min),
      }
      return { route, eng, busy: load, now: currentActivity(route, plan.now) }
    })
    .sort((a, b) => b.route.job_count - a.route.job_count)
  const maxBusy = Math.max(1, ...rows.map((r) => r.busy.total))

  // Подписи плиток заданы макетом — строчные, без заглавных.
  const tiles = [
    { v: `${k.jobs_assigned}/${k.jobs_total}`, l: `назначено · ${num(k.assign_rate, 1)} %`,
      tone: k.assign_rate >= 90 ? 'good' : k.assign_rate >= 75 ? 'warn' : 'bad' },
    { v: String(k.sla_violations), l: 'нарушения SLA',
      tone: k.sla_violations === 0 ? 'good' : 'bad' },
    { v: dur(k.travel_min), l: 'время в пути' },
    { v: `${num(k.travel_km)} км`, l: 'пробег службы' },
    { v: dur(k.wait_min), l: 'простой в ожидании', tone: k.wait_min > 240 ? 'warn' : undefined },
    { v: `${k.engineers_used}/${plan.routes.length}`, l: 'инженеров занято' },
    { v: `×${num(spread, 1)}`, l: busy.length
      ? `разброс загрузки (${Math.min(...busy)}–${Math.max(...busy)} мин)` : 'разброс загрузки' },
    { v: String(k.locked_done ?? 0), l: 'визитов выполнено' },
    { v: `${num(plan.solve_ms / 1000, 1)} с`, l: 'время расчёта' },
  ]

  const now = plan.now ?? '08:00'
  const fired = events.filter((e) => e.at <= now)

  return (
    <div className="stack">
      <div className="kpis">
        {tiles.map((t) => (
          <div key={t.l} className={`kpi${t.tone ? ' ' + t.tone : ''}`}>
            <div className="v">{t.v}</div>
            <div className="l" title={t.l}>{t.l}</div>
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 380px', gap: 14 }}>
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{ padding: '16px 20px 10px' }}>
            <h2 style={{ marginBottom: 0 }}>Карта маршрутов</h2>
          </div>
          <div style={{ position: 'relative', height: 460 }}>
            <MapView plan={plan} dataset={dataset} selectedJob={selectedJob}
                     selectedEngineer={null} changedJobs={changedJobs} neutral
                     onSelectJob={onSelectJob}
                     onSelectEngineer={(id) => id && go(`/engineers/${id}`)} />
          </div>
        </div>

        <div className="card" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <h2>Лента событий
            <span className="hint">{fired.length} из {events.length}</span>
          </h2>
          <div style={{ overflowY: 'auto', maxHeight: 430, marginRight: -8, paddingRight: 8 }}>
            {[...log].reverse().map((entry, i) => (
              <div key={i} style={{
                display: 'flex', gap: 10, padding: '8px 0',
                borderBottom: '1px solid var(--line)',
              }}>
                <span className="muted num" style={{ width: 42, flex: 'none' }}>{entry.at}</span>
                <div style={{ minWidth: 0 }}>
                  {entry.kind === 'event' && (
                    <div style={{
                      fontSize: 10, letterSpacing: '.06em', textTransform: 'uppercase',
                      color: 'var(--accent)', fontWeight: 700,
                    }}>
                      {EVENT_LABEL[entry.event_type ?? ''] ?? 'Событие'}
                    </div>
                  )}
                  <div style={{ fontSize: 12.5, color: entry.kind === 'event'
                    ? 'var(--ink)' : 'var(--ink-55)' }}>{entry.text}</div>
                </div>
              </div>
            ))}
            {!log.length && <div className="empty">Событий не зарегистрировано</div>}
          </div>
        </div>
      </div>

      <div className="card">
        <h2>Все инженеры
          <span className="hint">
            кто чем занят сейчас и как загружен · клик по строке — карточка
          </span>
        </h2>
        <table className="grid">
          <thead>
            <tr>
              <th>Инженер</th>
              <th style={{ width: 130 }}>Транспорт</th>
              <th style={{ width: 100 }}>Смена</th>
              <th className="r" style={{ width: 70 }}>Заявок</th>
              <th style={{ width: 200 }}>Сейчас</th>
              <th style={{ width: 260 }}>Загрузка за день</th>
              <th className="r" style={{ width: 100 }}>Занятость</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ route, eng, busy, now: activity }) => (
              <tr key={route.engineer_id} className="click"
                  onClick={() => go(`/engineers/${route.engineer_id}`)}>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span className="avatar sm"
                          style={{ background: engineerColor(route.engineer_id) }}>
                      {initials(route.engineer_name)}
                    </span>
                    <b>{route.engineer_name}</b>
                  </div>
                </td>
                <td className="dim">{eng ? VEHICLE_LABEL[eng.vehicle_type] : '—'}</td>
                <td className="dim num">{eng ? `${eng.shift[0]}–${eng.shift[1]}` : '—'}</td>
                <td className="r num">{route.job_count}</td>
                <td className="dim" style={{ whiteSpace: 'nowrap', overflow: 'hidden',
                                             textOverflow: 'ellipsis' }}>
                  <span className="dot" style={{
                    background: activity.live ? 'var(--info)' : 'var(--ink-30)' }} />
                  {' '}{activity.text}
                </td>
                <td>
                  <div className="load" style={{ width: `${busy.total / maxBusy * 100}%` }}>
                    <span className="work" style={{ width: `${busy.work / busy.total * 100}%` }} />
                    <span className="travel" style={{ width: `${busy.travel / busy.total * 100}%` }} />
                    <span className="lunch" style={{ width: `${busy.lunch / busy.total * 100}%` }} />
                    <span className="idle" style={{ width: `${busy.idle / busy.total * 100}%` }} />
                  </div>
                </td>
                <td className="r num" style={busy.work + busy.travel > 520
                  ? { color: 'var(--danger)' } : undefined}>
                  {dur(busy.work + busy.travel)}
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

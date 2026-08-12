import type { DayEvent, Dataset, Engineer, LogEntry, Plan } from '../api'
import { engineerColor } from '../colors'
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
            План на день не построен
          </div>
          Нажмите «Построить план» в шапке. Затем «Проиграть день» — увидите,
          как система перестраивает маршруты по ходу событий.
        </div>
      </div>
    )
  }

  const k = plan.kpi
  const busy = plan.routes.filter((r) => r.job_count).map((r) => r.work_min + r.travel_min)
  const spread = busy.length ? Math.max(...busy) / Math.max(1, Math.min(...busy)) : 0

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
            <h2 style={{ marginBottom: 0 }}>Карта маршрутов
              <span className="hint">
                линии нейтральные — цвет инженера на его карточке
              </span>
            </h2>
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
            <span className="hint">{fired.length} из {events.length} произошло</span>
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
            {!log.length && <div className="empty">Пока ничего не произошло</div>}
          </div>
        </div>
      </div>

      <div className="card">
        <h2>Инженеры
          <span className="hint">клик — карточка с маршрутом</span>
        </h2>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {plan.routes.filter((r) => r.job_count)
            .sort((a, b) => b.job_count - a.job_count)
            .map((r) => {
              const eng = engineers.find((e) => e.id === r.engineer_id)
              return (
                <button key={r.engineer_id} className="ghost"
                        style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 14px 6px 6px' }}
                        onClick={() => go(`/engineers/${r.engineer_id}`)}>
                  <span className="avatar sm" style={{ background: engineerColor(r.engineer_id) }}>
                    {initials(r.engineer_name)}
                  </span>
                  {r.engineer_name}
                  <span className="muted num">{r.job_count}</span>
                  {eng && !eng.can_carry_bulky && <span className="muted">пешком</span>}
                </button>
              )
            })}
        </div>
      </div>
    </div>
  )
}

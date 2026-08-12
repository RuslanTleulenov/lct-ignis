import { useMemo } from 'react'
import { type Engineer, type Plan, toMin } from '../api'
import { engineerColor, PRIORITY_COLOR, VEHICLE_LABEL } from '../colors'

/**
 * Таймлайн по инженерам.
 *
 * Гант отвечает на вопросы, которые карта не показывает: кто чем занят прямо
 * сейчас, где в дне дыры, укладывается ли визит в окно клиента. Полоска окна
 * рисуется над визитом — так сразу видно, приехал инженер вовремя или впритык.
 */

interface Props {
  plan: Plan | null
  engineers: Engineer[]
  selectedJob: string | null
  selectedEngineer: string | null
  changedJobs: Set<string>
  onSelectJob: (id: string) => void
  onSelectEngineer: (id: string | null) => void
}

export function Gantt({
  plan, engineers, selectedJob, selectedEngineer, changedJobs,
  onSelectJob, onSelectEngineer,
}: Props) {
  const { t0, span } = useMemo(() => {
    let lo = 8 * 60
    let hi = 19 * 60
    for (const e of engineers) {
      lo = Math.min(lo, toMin(e.shift[0]))
      hi = Math.max(hi, toMin(e.shift[1]))
    }
    for (const r of plan?.routes ?? []) {
      if (r.job_count) hi = Math.max(hi, toMin(r.end))
    }
    lo -= 15
    hi += 15
    return { t0: lo, span: Math.max(60, hi - lo) }
  }, [plan, engineers])

  const pos = (min: number) => ((min - t0) / span) * 100
  const width = (from: number, to: number) => Math.max(0.25, ((to - from) / span) * 100)

  const rows = useMemo(() => {
    const byId = new Map((plan?.routes ?? []).map((r) => [r.engineer_id, r]))
    return engineers
      .map((e) => ({ engineer: e, route: byId.get(e.id) ?? null }))
      .sort((a, b) => (b.route?.job_count ?? 0) - (a.route?.job_count ?? 0))
  }, [plan, engineers])

  const nowMin = plan?.now ? toMin(plan.now) : null
  const affected = new Set(plan?.diff?.affected ?? [])

  const ticks: number[] = []
  for (let h = Math.ceil(t0 / 60); h * 60 <= t0 + span; h++) ticks.push(h * 60)

  return (
    <div className="gantt">
      <div className="gantt-head">
        <h2>Загрузка инженеров</h2>
        <span style={{ color: 'var(--text-mute)', fontSize: 11 }}>
          полоска сверху — окно клиента · штриховка — обед · белая обводка — изменено
          пересчётом · пунктир — закреплено диспетчером
        </span>
        {selectedEngineer && (
          <button style={{ marginLeft: 'auto', padding: '3px 9px' }}
                  onClick={() => onSelectEngineer(null)}>
            Показать всех
          </button>
        )}
      </div>

      <div className="gantt-body">
        {rows.map(({ engineer, route }) => {
          const color = engineerColor(engineer.id)
          const dim = selectedEngineer !== null && selectedEngineer !== engineer.id
          const shiftFrom = toMin(engineer.shift[0])
          const shiftTo = toMin(engineer.shift[1])
          return (
            <div
              key={engineer.id}
              className={`gantt-row${dim ? ' dim' : ''}${
                affected.has(engineer.id) ? ' affected' : ''}`}
            >
              <div
                className="gantt-name"
                title={`${engineer.name} · ${VEHICLE_LABEL[engineer.vehicle_type]} · смена ${engineer.shift[0]}–${engineer.shift[1]}`}
                onClick={() =>
                  onSelectEngineer(selectedEngineer === engineer.id ? null : engineer.id)}
              >
                <span className="dot" style={{ background: color }} />
                <span className="nm">{engineer.name}</span>
                <span className="cnt">{route?.job_count ?? 0}</span>
              </div>

              <div className="gantt-track">
                <div
                  className="gantt-shift"
                  style={{ left: `${pos(shiftFrom)}%`, width: `${width(shiftFrom, shiftTo)}%` }}
                />
                {route?.lunch_start && (
                  <div
                    className="gantt-lunch"
                    title={`Обед ${route.lunch_min} мин`}
                    style={{
                      left: `${pos(toMin(route.lunch_start))}%`,
                      width: `${width(0, route.lunch_min)}%`,
                    }}
                  />
                )}
                {route?.stops.filter((s) => s.kind === 'job').map((s) => {
                  const from = toMin(s.service_start)
                  const to = toMin(s.service_end)
                  const win = s.window
                  const pinned = !!plan?.pins[s.job_id!]
                  return (
                    <div key={s.job_id}>
                      {win && (
                        <div
                          className="gantt-window"
                          style={{
                            left: `${pos(toMin(win[0]))}%`,
                            width: `${width(toMin(win[0]), toMin(win[1]))}%`,
                            opacity: s.window_hard ? 0.9 : 0.45,
                          }}
                        />
                      )}
                      <div
                        className={`gantt-bar${selectedJob === s.job_id ? ' selected' : ''}${
                          s.sla_late_min ? ' late' : ''}`}
                        style={{
                          left: `${pos(from)}%`,
                          width: `${width(from, to)}%`,
                          background: color,
                          boxShadow: changedJobs.has(s.job_id!)
                            ? '0 0 0 2px #fff' : undefined,
                        }}
                        title={`${s.service_start}–${s.service_end} ${s.customer}\n${s.work_type}\nокно ${win?.[0]}–${win?.[1]}${s.window_hard ? ' (жёсткое)' : ''}${s.sla_late_min ? `\nSLA нарушен на ${s.sla_late_min} мин` : ''}${pinned ? '\nЗакреплено диспетчером' : ''}`}
                        onClick={() => onSelectJob(s.job_id!)}
                      >
                        {s.priority === 'P1' && (
                          <span style={{
                            position: 'absolute', inset: 0, borderRadius: 2,
                            border: `2px solid ${PRIORITY_COLOR.P1}`,
                          }} />
                        )}
                        {pinned && (
                          <span style={{
                            position: 'absolute', inset: 0, borderRadius: 2,
                            border: '2px dashed #fff',
                          }} />
                        )}
                      </div>
                    </div>
                  )
                })}
                {nowMin !== null && nowMin >= t0 && nowMin <= t0 + span && (
                  <div className="gantt-now" style={{ left: `${pos(nowMin)}%` }} />
                )}
              </div>
            </div>
          )
        })}
      </div>

      <div className="gantt-axis">
        {ticks.map((t) => (
          <span key={t} className="gantt-tick" style={{ left: `${pos(t)}%` }}>
            {String(Math.floor(t / 60)).padStart(2, '0')}:00
          </span>
        ))}
      </div>
    </div>
  )
}

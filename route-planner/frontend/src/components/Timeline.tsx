import type { Plan, Route } from '../api'
import { dur, hhmm, toMin } from '../format'

/**
 * Лента дня одного инженера.
 *
 * В отличие от общего Ганта здесь одна широкая полоса, поэтому в карточку
 * визита помещаются номер, время и заказчик — не нужно наводить мышь, чтобы
 * понять, что происходит.
 */

interface Props {
  route: Route
  plan: Plan | null
  from?: number
  to?: number
  selected?: string | null
  onSelect?: (jobId: string) => void
}

export function Timeline({ route, plan, from = 8 * 60, to = 20 * 60,
                           selected, onSelect }: Props) {
  const span = to - from
  const at = (min: number) => ((min - from) / span) * 100
  const width = (a: number, b: number) => Math.max(0.4, ((b - a) / span) * 100)

  const ticks: number[] = []
  for (let h = Math.ceil(from / 60); h * 60 <= to; h++) ticks.push(h * 60)

  const visits = route.stops.filter((s) => s.kind === 'job')
  const now = plan?.now ? toMin(plan.now) : null
  const pins = plan?.pins ?? {}

  return (
    <div>
      <div className="tl">
        <div className="tl-grid">
          {ticks.map((t) => (
            <span key={t} className="tl-tick" style={{ left: `${at(t)}%` }} />
          ))}
          {ticks.map((t) => (
            <span key={`h${t}`} className="tl-hour" style={{ left: `${at(t)}%` }}>
              {hhmm(t)}
            </span>
          ))}
        </div>

        <div className="tl-lane">
          {/* перегоны между точками */}
          {route.stops.slice(1).map((s, i) => {
            const prev = route.stops[i]
            const a = toMin(prev.service_end)
            const b = toMin(s.arrival)
            if (b <= a) return null
            return (
              <span
                key={`leg${i}`}
                className={`tl-leg${s.via_metro ? ' metro' : ''}`}
                style={{ left: `${at(a)}%`, width: `${width(a, b)}%` }}
                title={`${s.travel_min} мин · ${s.travel_km} км${s.via_metro ? ' · через метро' : ''}`}
              />
            )
          })}

          {route.lunch_start && (
            <div
              className="tl-lunch"
              style={{
                left: `${at(toMin(route.lunch_start))}%`,
                width: `${width(0, route.lunch_min)}%`,
              }}
              title={`Обед ${route.lunch_min} мин`}
            >
              Обед
            </div>
          )}

          {visits.map((s, i) => {
            const a = toMin(s.service_start)
            const b = toMin(s.service_end)
            const w = width(a, b)
            return (
              <div key={s.job_id}>
                {s.window && (
                  <span
                    className="tl-window"
                    style={{
                      left: `${at(toMin(s.window[0]))}%`,
                      width: `${width(toMin(s.window[0]), toMin(s.window[1]))}%`,
                      opacity: s.window_hard ? 0.95 : 0.4,
                    }}
                  />
                )}
                <div
                  className={`tl-visit${s.sla_late_min ? ' late' : ''}`
                    + (pins[s.job_id!] ? ' pinned' : '')}
                  style={{
                    left: `${at(a)}%`, width: `${w}%`,
                    outline: selected === s.job_id ? '2px solid #fff' : undefined,
                  }}
                  onClick={() => onSelect?.(s.job_id!)}
                  title={`${s.service_start}–${s.service_end} · ${s.customer}\n${s.work_type}`
                    + (s.window ? `\nокно ${s.window[0]}–${s.window[1]}${s.window_hard ? ' (жёсткое)' : ''}` : '')
                    + (s.sla_late_min ? `\nSLA нарушен на ${s.sla_late_min} мин` : '')}
                >
                  <div className="n">{i + 1}</div>
                  {w > 5 && <div className="t">{s.service_start}–{s.service_end}</div>}
                  {w > 11 && <div className="c">{s.customer}</div>}
                </div>
              </div>
            )
          })}

          {now !== null && now >= from && now <= to && (
            <span className="tl-now" data-label={`сейчас ${plan!.now}`}
                  style={{ left: `${at(now)}%` }} />
          )}
        </div>
      </div>

      <div className="legend" style={{ marginTop: 8 }}>
        <span><i style={{ background: '#24304a', border: '1px solid #35507f' }} />визит</span>
        <span><i style={{ background: '#43434e' }} />переезд</span>
        <span><i style={{ background: '#4d7cff' }} />метро</span>
        <span><i style={{ background: '#3a3a44' }} />обеденный перерыв</span>
        <span><i style={{ background: '#3d4a5f' }} />окно доступа</span>
        <span><i style={{ background: 'transparent', border: '2px solid var(--danger)' }} />срыв SLA</span>
        <span><i style={{ background: 'transparent', border: '2px dashed var(--accent)' }} />закреплена диспетчером</span>
        <span className="num" style={{ marginLeft: 'auto' }}>
          работы {dur(route.work_min)} · переезды {dur(route.travel_min)}
        </span>
      </div>
    </div>
  )
}

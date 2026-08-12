import type { Plan } from '../api'

interface Props {
  plan: Plan | null
}

export function KpiBar({ plan }: Props) {
  if (!plan) return null
  const k = plan.kpi
  const done = k.locked_done ?? 0

  const tiles: { label: string; value: string; sub?: string; tone?: string }[] = [
    {
      label: 'Назначено',
      value: `${k.jobs_assigned}/${k.jobs_total}`,
      sub: `${k.assign_rate}%`,
      tone: k.assign_rate >= 90 ? 'good' : k.assign_rate >= 75 ? 'warn' : 'bad',
    },
    {
      label: 'Нарушений SLA',
      value: String(k.sla_violations),
      sub: k.sla_late_min ? `${k.sla_late_min} мин` : undefined,
      tone: k.sla_violations === 0 ? 'good' : 'bad',
    },
    { label: 'Время в пути', value: `${k.travel_min}`, sub: 'мин' },
    { label: 'Пробег', value: `${Math.round(k.travel_km)}`, sub: 'км' },
    {
      label: 'Простой',
      value: String(k.wait_min),
      sub: 'мин',
      tone: k.wait_min > 240 ? 'warn' : undefined,
    },
    { label: 'Инженеров занято', value: `${k.engineers_used}`, sub: `из ${plan.routes.length}` },
    {
      label: 'Разброс загрузки',
      value: `${k.busy_spread_min}`,
      sub: `σ ${k.busy_stdev_min}`,
    },
    { label: 'Выполнено', value: String(done), sub: 'за день' },
    {
      label: 'Расчёт',
      value: `${(plan.solve_ms / 1000).toFixed(1)}`,
      sub: plan.warm_started ? 'с · тёплый старт' : 'с',
    },
  ]

  return (
    <div className="kpis">
      {tiles.map((t) => (
        <div key={t.label} className={`kpi${t.tone ? ' ' + t.tone : ''}`}>
          <div className="label">{t.label}</div>
          <div className="value">
            {t.value} {t.sub && <small>{t.sub}</small>}
          </div>
        </div>
      ))}
    </div>
  )
}

import { useMemo, useState } from 'react'
import type { Compare, Dataset, Engineer, Plan, WhyNot } from '../api'
import { PRIORITY_COLOR, VEHICLE_LABEL } from '../colors'
import { go } from '../router'

/* ------------------------------------------------------------- Не назначено */

export function BacklogScreen({ plan, whyNotAll }: {
  plan: Plan | null; whyNotAll: WhyNot[]
}) {
  const summary = useMemo(() => {
    const reasons: Record<string, number> = {}
    const specs: Record<string, number> = {}
    for (const w of whyNotAll) {
      for (const b of w.qualified) reasons[b.reason] = (reasons[b.reason] ?? 0) + 1
      const spec = w.job?.specialization_name
      if (spec) specs[spec] = (specs[spec] ?? 0) + 1
    }
    return { reasons, specs }
  }, [whyNotAll])

  if (!plan) return <div className="empty">План ещё не построен</div>
  if (!plan.unassigned.length) {
    return <div className="card"><div className="empty">Все заявки назначены.</div></div>
  }

  return (
    <div className="stack">
      <div className="card">
        <h2>Не назначено
          <span className="hint">
            {plan.unassigned.length} из {plan.kpi.jobs_total}
          </span>
        </h2>
        <p className="dim">
          Управленческий инструмент: из разбора видно, чего службе не хватает —
          людей, приборов или машин.
        </p>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
          {Object.entries(summary.reasons).sort((a, b) => b[1] - a[1]).map(([r, n]) => (
            <span key={r} className="pill warn">{r} — {n}</span>
          ))}
          {Object.entries(summary.specs).map(([s, n]) => (
            <span key={s} className="pill">{s}: {n}</span>
          ))}
        </div>
      </div>

      {plan.unassigned.map((job) => {
        const info = whyNotAll.find((w) => w.job_id === job.id)
        return (
          <div key={job.id} className="card">
            <div style={{ display: 'flex', gap: 10, alignItems: 'baseline' }}>
              <span className="pill" style={{
                background: PRIORITY_COLOR[job.priority] + '22',
                color: PRIORITY_COLOR[job.priority], borderColor: 'transparent',
                fontWeight: 700,
              }}>{job.priority}</span>
              <a href={`#/jobs/${job.id}`}><b>{job.customer}</b></a>
              <span className="dim">{job.work_type}</span>
              <span className="spacer" />
              <span className="muted num">
                {job.district} · окно {job.window[0]}–{job.window[1]}
                {job.window_hard && ' (жёсткое)'}
              </span>
            </div>
            {info && (
              <>
                <div style={{ marginTop: 10, color: 'var(--ink)' }}>{info.verdict}</div>
                {info.qualified.map((b) => (
                  <div key={b.engineer_id} style={{ display: 'flex', gap: 8, marginTop: 5 }}>
                    <span style={{ color: 'var(--danger)' }}>✗</span>
                    <span><b>{b.engineer_name}</b>: <span className="dim">{b.detail}</span></span>
                  </div>
                ))}
              </>
            )}
          </div>
        )
      })}
    </div>
  )
}

/* ------------------------------------------------------------------- Эффект */

export function EffectScreen({ compare }: { compare: Compare | null }) {
  if (!compare) {
    return (
      <div className="card">
        <div className="empty">
          Постройте план — посчитаю эффект против ручного планирования.
        </div>
      </div>
    )
  }
  return (
    <div className="stack">
      <div className="card">
        <h2>Эффект</h2>
        <p className="muted" style={{ fontSize: 12 }}>
          Сравнение с ручным планированием на тех же {compare.optimized_kpi.jobs_total}{' '}
          утренних заявках.
        </p>
        {/* Колонок две, без отдельного «Эффекта»: в макете величины стоят
            рядом, и разница читается сама. Строка про SLA несёт пояснение
            прямо под названием — она единственная, где мы хуже. */}
        <table className="grid" style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th>Показатель</th>
              <th className="r" style={{ width: 170 }}>Сервис</th>
              <th className="r" style={{ width: 170 }}>Ручной план</th>
            </tr>
          </thead>
          <tbody>
            {compare.rows.map((r, i) => {
              const lead = i === 0
              const worse = r.effect.startsWith('+') && !r.effect.includes('✓')
              return (
                <tr key={r.label} style={lead ? { background: 'var(--accent-soft)' } : undefined}>
                  <td style={{ fontWeight: lead ? 700 : 400,
                               color: lead ? 'var(--ink)' : undefined }}>
                    {r.label}
                    {worse && (
                      <div style={{ color: 'var(--danger)', fontSize: 11.5, marginTop: 2 }}>
                        У ручного плана меньше потому, что он не взял
                        {' '}{compare.baseline_kpi.jobs_unassigned}{' '}
                        неудобных заявок вовсе
                      </div>
                    )}
                  </td>
                  <td className="r num" style={{
                    fontWeight: 700,
                    color: worse ? 'var(--danger)' : lead ? 'var(--accent)' : 'var(--ink)',
                  }}>{r.optimized}</td>
                  <td className="r num dim">{r.manual}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <div className="card">
          <h2>Как считался baseline</h2>
          <p className="dim">
            За основу принято ручное планирование: заявки разбираются по срочности
            окна доступа и передаются ближайшему подходящему инженеру, ранее
            назначенное не пересматривается. Комплектация оборудованием и заезды
            на склад совпадают с расчётными — сопоставляется качество
            маршрутизации, а не условия выдачи инструмента.
          </p>
        </div>
        <div className="card">
          <h2>О расхождении по SLA</h2>
          <p className="dim">
            При ручном планировании нарушений меньше только потому, что часть
            заявок не принимается к исполнению вовсе. Для заказчика невыполненная
            заявка хуже, чем выполненная с опозданием, поэтому ключевым
            показателем принято «закрыто в срок».
          </p>
        </div>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------- Справочники */

export function ReferenceScreen({ dataset, engineers, plan }: {
  dataset: Dataset | null; engineers: Engineer[]; plan: Plan | null
}) {
  const [tab, setTab] = useState<'types' | 'equip' | 'wh' | 'staff'>('types')
  if (!dataset) return <div className="empty">Загрузка…</div>

  /** На какой склад инженер заезжает утром — из построенного плана. */
  const pickupAt = (engineerId: string) =>
    plan?.routes.find((r) => r.engineer_id === engineerId)?.pickup_warehouse ?? null

  // Четыре вкладки, как в макете: матрица совместимости живёт внутри
  // «Типов работ», а не отдельным разделом.
  const tabs = [
    ['types', 'Типы работ'], ['equip', 'Оборудование'],
    ['wh', 'Склады'], ['staff', 'Состав службы'],
  ] as const

  return (
    <div className="stack">
      <div className="card">
        <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
          {tabs.map(([k, label]) => (
            <button key={k} className={tab === k ? 'primary' : 'ghost'}
                    onClick={() => setTab(k)}>{label}</button>
          ))}
        </div>

        {/* Вкладка «Типы работ» — это и есть матрица совместимости: в макете
            она не вынесена отдельно, а служит основным видом справочника. */}
        {tab === 'types' && (
          <div style={{ overflowX: 'auto' }}>
            <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
              Матрица «работа → оборудование». Жёлтым — дефицитные приборы:
              по 2 экземпляра на службу.
            </p>
            <table className="grid" style={{ fontSize: 11.5 }}>
              <thead><tr>
                <th style={{ minWidth: 240 }}>Тип работ</th>
                <th className="r" style={{ width: 74 }}>Мин. ур.</th>
                {dataset.equipment.map((q) => (
                  <th key={q.id} className="r" style={{ width: 34 }}>
                    <span title={q.name} style={{
                      writingMode: 'vertical-rl', transform: 'rotate(180deg)',
                      whiteSpace: 'nowrap', display: 'inline-block', height: 128,
                      color: q.rare ? 'var(--accent)' : undefined,
                    }}>{q.name}</span>
                  </th>
                ))}
              </tr></thead>
              <tbody>
                {dataset.work_types.map((w) => {
                  const need = new Set(w.equipment.map((q) => q.id))
                  return (
                    <tr key={w.id}>
                      <td>
                        <b>{dataset.specializations[w.specialization]}</b>
                        <div className="muted" style={{ fontSize: 11.5 }}>{w.name}</div>
                      </td>
                      <td className="r num">{w.min_level}</td>
                      {dataset.equipment.map((q) => (
                        <td key={q.id} className="r">
                          <span className="cell-box" style={need.has(q.id) ? {
                            background: q.rare ? 'var(--accent)' : 'var(--info)',
                            borderColor: 'transparent',
                          } : undefined} />
                        </td>
                      ))}
                    </tr>
                  )
                })}
              </tbody>
            </table>
            <div className="legend" style={{ marginTop: 10 }}>
              <span><i style={{ background: 'var(--info)' }} />требуется</span>
              <span><i style={{ background: 'var(--accent)' }} />дефицитный прибор</span>
            </div>
          </div>
        )}

        {/* Оборудование и склады в макете — плитки, а не таблицы: позиций
            немного, а дефицит должен бросаться в глаза. */}
        {tab === 'equip' && (
          <div className="tiles">
            {dataset.equipment.map((q) => (
              <div key={q.id} className={`tile${q.rare ? ' accent' : ''}`}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 600 }}>{q.name}</div>
                  <div className="muted" style={{ fontSize: 11.5 }}>
                    {q.rare ? 'дефицит — узкое место службы'
                      : q.bulky ? 'габарит: только с машиной' : 'в достатке'}
                  </div>
                </div>
                <div className="num" style={{
                  fontSize: 22, fontWeight: 700, whiteSpace: 'nowrap',
                  color: q.rare ? 'var(--accent)' : 'var(--ink)',
                }}>
                  {q.units}<span style={{ fontSize: 12, marginLeft: 3 }} className="muted">шт</span>
                </div>
              </div>
            ))}
          </div>
        )}

        {tab === 'wh' && (
          <div className="tiles">
            {dataset.warehouses.map((w) => {
              const crew = engineers.filter((e) => pickupAt(e.id) === w.id)
              return (
                <div key={w.id} className="tile" style={{ flexDirection: 'column',
                  alignItems: 'stretch', gap: 10 }}>
                  <span className="mark" style={{ width: 32, height: 32, fontSize: 13 }}>С</span>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: 15 }}>{w.name}</div>
                    <div className="muted" style={{ fontSize: 12 }}>{w.address}</div>
                  </div>
                  <div style={{ fontSize: 12 }}>
                    <span className="muted">утренний заезд: </span>
                    <b>{crew.length} инж.</b>
                    {crew.length > 0 && (
                      <div className="muted" style={{ marginTop: 2 }}>
                        {crew.map((e) => e.name).join(', ')}
                      </div>
                    )}
                  </div>
                  <div className="muted" style={{ fontSize: 12 }}>
                    режим работы {w.open[0]}–{w.open[1]}
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {tab === 'staff' && (
          <table className="grid">
            <thead><tr>
              <th>Инженер</th><th style={{ width: 320 }}>Квалификация</th>
              <th style={{ width: 150 }}>Транспорт</th>
              <th className="r" style={{ width: 130 }}>Рабочая смена</th>
            </tr></thead>
            <tbody>
              {engineers.map((e) => (
                <tr key={e.id} className="click" onClick={() => go(`/engineers/${e.id}`)}>
                  <td><b>{e.name}</b> <span className="muted">{e.id}</span></td>
                  <td>
                    {e.skills.map((s) => (
                      <span key={s.specialization} className="pill accent"
                            style={{ marginRight: 6 }}>
                        {s.specialization_name} ур. {s.level}
                      </span>
                    ))}
                  </td>
                  <td className="dim">{VEHICLE_LABEL[e.vehicle_type]}</td>
                  <td className="r num">{e.shift[0]}–{e.shift[1]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

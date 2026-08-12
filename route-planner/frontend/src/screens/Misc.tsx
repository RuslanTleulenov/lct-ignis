import { useMemo, useState } from 'react'
import type { Compare, Dataset, Engineer, Plan, WhyNot } from '../api'
import { PRIORITY_COLOR, VEHICLE_LABEL } from '../colors'
import { plural } from '../format'
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
        <h2>Заявки без исполнителя
          <span className="hint">
            {plan.unassigned.length}{' '}
            {plural(plan.unassigned.length, 'заявка', 'заявки', 'заявок')} к переносу
          </span>
        </h2>
        <p className="dim">
          Спрос превышает ресурс службы. Ниже указано, что именно ограничивает
          назначение по каждой заявке: квалификация, оборудование, транспорт или
          загрузка исполнителей.
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
        <h2>Эффект против ручного планирования</h2>
        <p className="muted" style={{ fontSize: 12 }}>{compare.note}</p>
        <table className="grid" style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th>Показатель</th>
              <th className="r" style={{ width: 130 }}>Вручную</th>
              <th className="r" style={{ width: 130 }}>Оптимизатор</th>
              <th className="r" style={{ width: 130 }}>Эффект</th>
            </tr>
          </thead>
          <tbody>
            {compare.rows.map((r, i) => (
              <tr key={r.label}>
                <td style={{ fontWeight: i === 0 ? 700 : 400,
                             color: i === 0 ? 'var(--ink)' : undefined }}>{r.label}</td>
                <td className="r num dim">{r.manual}</td>
                <td className="r num" style={{ fontWeight: i === 0 ? 700 : 400 }}>{r.optimized}</td>
                <td className="r num" style={{
                  color: r.effect.includes('✓') ? 'var(--ok)'
                    : r.effect.startsWith('+') ? 'var(--danger)' : undefined,
                  fontWeight: r.effect.includes('✓') ? 700 : 400,
                }}>{r.effect}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <div className="card">
          <h2>Методика сравнения</h2>
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

export function ReferenceScreen({ dataset, engineers }: {
  dataset: Dataset | null; engineers: Engineer[]
}) {
  const [tab, setTab] = useState<'types' | 'matrix' | 'equip' | 'wh' | 'staff'>('types')
  if (!dataset) return <div className="empty">Загрузка…</div>

  const tabs = [
    ['types', 'Виды работ'], ['matrix', 'Матрица совместимости'],
    ['equip', 'Оборудование'], ['wh', 'Склады'], ['staff', 'Инженерный состав'],
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

        {tab === 'types' && (
          <table className="grid">
            <thead><tr>
              <th>Вид работ</th><th style={{ width: 230 }}>Специализация</th>
              <th className="r" style={{ width: 130 }}>Квалификация</th>
              <th className="r" style={{ width: 140 }}>Норматив времени</th>
            </tr></thead>
            <tbody>
              {dataset.work_types.map((w) => (
                <tr key={w.id}>
                  <td><b>{w.name}</b>
                    <div className="muted" style={{ fontSize: 11.5 }}>
                      {w.equipment.map((q) => q.name).join(', ')}
                    </div>
                  </td>
                  <td className="dim">{dataset.specializations[w.specialization]}</td>
                  <td className="r num">не ниже {w.min_level} уровня</td>
                  <td className="r num">{w.base_duration_min} мин</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {tab === 'matrix' && (
          <div style={{ overflowX: 'auto' }}>
            <table className="grid" style={{ fontSize: 11.5 }}>
              <thead><tr>
                <th style={{ minWidth: 240 }}>Вид работ</th>
                {dataset.equipment.map((q) => (
                  <th key={q.id} className="r" style={{ width: 34 }}>
                    <span title={q.name} style={{
                      writingMode: 'vertical-rl', transform: 'rotate(180deg)',
                      whiteSpace: 'nowrap', display: 'inline-block', height: 116,
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
                      <td>{w.name}</td>
                      {dataset.equipment.map((q) => (
                        <td key={q.id} className="r">
                          {need.has(q.id) && (
                            <span className="dot" style={{
                              background: q.bulky ? 'var(--warn)' : 'var(--accent)' }} />
                          )}
                        </td>
                      ))}
                    </tr>
                  )
                })}
              </tbody>
            </table>
            <div className="legend" style={{ marginTop: 10 }}>
              <span><i style={{ background: 'var(--accent)' }} />требуется</span>
              <span><i style={{ background: 'var(--warn)' }} />габаритное, необходим автотранспорт</span>
              <span style={{ color: 'var(--accent)' }}>жёлтым выделены приборы ограниченного парка</span>
            </div>
          </div>
        )}

        {tab === 'equip' && (
          <table className="grid">
            <thead><tr>
              <th>Наименование</th><th style={{ width: 180 }}>Место хранения</th>
              <th className="r" style={{ width: 120 }}>В парке</th>
              <th className="r" style={{ width: 180 }}>Требования к перевозке</th>
            </tr></thead>
            <tbody>
              {dataset.equipment.map((q) => (
                <tr key={q.id}>
                  <td><b>{q.name}</b>
                    {q.rare && <span className="pill accent" style={{ marginLeft: 8 }}>
                      ограниченный парк</span>}
                  </td>
                  <td className="dim">{q.stock === 'all'
                    ? 'на всех складах' : `централизованно, склад ${q.stock}`}</td>
                  <td className="r num">{q.units} шт.</td>
                  <td className="r">{q.bulky
                    ? <span className="pill warn">только автотранспортом</span>
                    : <span className="muted">без ограничений</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {tab === 'wh' && (
          <table className="grid">
            <thead><tr>
              <th>Наименование</th><th>Адрес</th>
              <th className="r" style={{ width: 160 }}>Режим работы</th>
            </tr></thead>
            <tbody>
              {dataset.warehouses.map((w) => (
                <tr key={w.id}>
                  <td><b>{w.name}</b> <span className="muted">{w.id}</span></td>
                  <td className="dim">{w.address}</td>
                  <td className="r num">{w.open[0]}–{w.open[1]}</td>
                </tr>
              ))}
            </tbody>
          </table>
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

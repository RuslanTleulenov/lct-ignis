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
        <h2>Не влезло в день
          <span className="hint">{plan.unassigned.length} заявок</span>
        </h2>
        <p className="dim">
          Это не сбой: спрос выше ёмкости службы. Система показывает, что именно
          упирается — из этого видно, чего не хватает: людей, приборов или машин.
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
          <h2>Как считался baseline</h2>
          <p className="dim">
            Грамотный диспетчер: разбирает заявки по срочности окна, отдаёт каждую
            ближайшему подходящему инженеру, назначенное не переставляет.
            Комплектация инструментом и заезды на склад — те же, что у оптимизатора:
            сравнивается маршрутизация, а не удача утренней выдачи.
          </p>
        </div>
        <div className="card">
          <h2>Почему нарушений SLA больше</h2>
          <p className="dim">
            У ручного плана их меньше только потому, что он не взял неудобные
            заявки вовсе, а для клиента несделанная заявка хуже опоздания.
            Поэтому первой строкой стоит «закрыто в срок».
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
    ['types', 'Типы работ'], ['matrix', 'Работа → оборудование'],
    ['equip', 'Оборудование'], ['wh', 'Склады'], ['staff', 'Состав службы'],
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
              <th>Тип работ</th><th style={{ width: 220 }}>Специализация</th>
              <th className="r" style={{ width: 90 }}>Уровень</th>
              <th className="r" style={{ width: 110 }}>Базовое время</th>
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
                  <td className="r num">от {w.min_level}</td>
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
                <th style={{ minWidth: 230 }}>Работа \ Оборудование</th>
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
              <span><i style={{ background: 'var(--warn)' }} />габаритное — нужна машина</span>
              <span style={{ color: 'var(--accent)' }}>жёлтым в заголовке — дефицитные приборы</span>
            </div>
          </div>
        )}

        {tab === 'equip' && (
          <table className="grid">
            <thead><tr>
              <th>Оборудование</th><th style={{ width: 150 }}>Хранение</th>
              <th className="r" style={{ width: 110 }}>Экземпляров</th>
              <th className="r" style={{ width: 130 }}>Габарит</th>
            </tr></thead>
            <tbody>
              {dataset.equipment.map((q) => (
                <tr key={q.id}>
                  <td><b>{q.name}</b>
                    {q.rare && <span className="pill accent" style={{ marginLeft: 8 }}>дефицит</span>}
                  </td>
                  <td className="dim">{q.stock === 'all' ? 'на любом складе' : `склад ${q.stock}`}</td>
                  <td className="r num">{q.units}</td>
                  <td className="r">{q.bulky
                    ? <span className="pill warn">нужна машина</span>
                    : <span className="muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {tab === 'wh' && (
          <table className="grid">
            <thead><tr>
              <th>Склад</th><th>Адрес</th><th className="r" style={{ width: 140 }}>Часы работы</th>
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
              <th>Инженер</th><th style={{ width: 300 }}>Квалификация</th>
              <th style={{ width: 140 }}>Транспорт</th>
              <th className="r" style={{ width: 120 }}>Смена</th>
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

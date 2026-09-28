import { useMemo, useState } from 'react'
import type { Compare, Dataset, DatasetInfo, Engineer, Plan, WhyNot } from '../api'
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

export function EffectScreen({ compare, onBaseline }: {
  compare: Compare | null
  onBaseline: (b: 'tz' | 'smart') => void
}) {
  if (!compare) {
    return (
      <div className="card">
        <div className="empty">
          Постройте план — посчитаю эффект против базового варианта.
        </div>
      </div>
    )
  }
  const fact = compare.fact
  // Строки трёх столбцов сшиваются по названию показателя: у факта свой
  // набор сравнений, но подписи те же.
  const factByLabel = new Map((fact?.rows ?? []).map((r) => [r.label, r]))

  // Обязательные метрики ТЗ, п. 2.3: число исполнителей и пробег по каждому.
  const perEngineer = compare.optimized_routes
    .filter((r) => r.job_count > 0)
    .sort((a, b) => b.travel_km - a.travel_km)
  const total = (rows: typeof perEngineer) => rows.reduce((a, r) => a + r.travel_km, 0)

  return (
    <div className="stack">
      <div className="card">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <h2 style={{ margin: 0 }}>Эффект</h2>
          <span className="spacer" />
          <span className="muted" style={{ fontSize: 12 }}>базовый вариант:</span>
          <div className="seg">
            <button className={compare.baseline === 'tz' ? 'on' : ''}
                    onClick={() => onBaseline('tz')}>по ТЗ</button>
            <button className={compare.baseline === 'smart' ? 'on' : ''}
                    onClick={() => onBaseline('smart')}>грамотный диспетчер</button>
          </div>
        </div>
        <p className="muted" style={{ fontSize: 12 }}>
          На тех же {compare.optimized_kpi.jobs_total} утренних заявках.{' '}
          {compare.baseline_note}
        </p>
        <table className="grid" style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th>Показатель</th>
              <th className="r" style={{ width: 150 }}>Сервис</th>
              <th className="r" style={{ width: 150 }}>{compare.baseline_label}</th>
              {fact && <th className="r" style={{ width: 150 }}>Факт диспетчера</th>}
            </tr>
          </thead>
          <tbody>
            {compare.rows.map((r, i) => {
              const lead = i === 0
              const worse = r.effect.startsWith('+') && !r.effect.includes('✓')
              const f = factByLabel.get(r.label)
              return (
                <tr key={r.label} style={lead ? { background: 'var(--accent-soft)' } : undefined}>
                  <td style={{ fontWeight: lead ? 700 : 400,
                               color: lead ? 'var(--ink)' : undefined }}>
                    {r.label}
                    {worse && r.label === 'Нарушений SLA' && (
                      <div style={{ color: 'var(--danger)', fontSize: 11.5, marginTop: 2 }}>
                        У базового варианта меньше потому, что он не взял
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
                  {fact && <td className="r num dim">{f?.manual ?? '—'}</td>}
                </tr>
              )
            })}
          </tbody>
        </table>
        {fact && (
          <p className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>
            {fact.note} По статусам выгрузки: выполнено {fact.statuses.done},
            просрочено {fact.statuses.overdue}, отменено {fact.statuses.cancelled},
            не отправлено {fact.statuses.unsent}.
          </p>
        )}
      </div>

      <div className="card">
        <h2>Обязательные метрики ТЗ
          <span className="hint">
            задействовано {perEngineer.length} из {compare.optimized_routes.length}
            {' '}· пробег {total(perEngineer).toFixed(1)} км
          </span>
        </h2>
        <table className="grid">
          <thead>
            <tr>
              <th>Исполнитель</th>
              <th className="r" style={{ width: 90 }}>Заявок</th>
              <th className="r" style={{ width: 110 }}>В пути</th>
              <th className="r" style={{ width: 110 }}>Пробег, км</th>
              {fact && <th className="r" style={{ width: 140 }}>Факт, км</th>}
            </tr>
          </thead>
          <tbody>
            {compare.optimized_routes
              .slice()
              .sort((a, b) => b.job_count - a.job_count || b.travel_km - a.travel_km)
              .map((r) => {
                const f = fact?.routes.find((x) => x.engineer_id === r.engineer_id)
                return (
                  <tr key={r.engineer_id} className={r.job_count ? '' : 'dim'}>
                    <td>{r.engineer_name}</td>
                    <td className="r num">{r.job_count || '—'}</td>
                    <td className="r num">{r.job_count ? `${r.travel_min} мин` : '—'}</td>
                    <td className="r num" style={{ fontWeight: 700 }}>
                      {r.job_count ? r.travel_km.toFixed(1) : '—'}
                    </td>
                    {fact && (
                      <td className="r num dim">
                        {f?.job_count ? `${f.travel_km.toFixed(1)} (${f.job_count} з.)` : '—'}
                      </td>
                    )}
                  </tr>
                )
              })}
          </tbody>
        </table>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <div className="card">
          <h2>Как считался базовый вариант</h2>
          <p className="dim">
            {compare.baseline === 'tz'
              ? 'Ровно по ТЗ, п. 2.3: заявки обрабатываются по порядку поступления и ' +
                'назначаются первому по порядку во входных данных доступному инженеру, ' +
                'который удовлетворяет обязательным ограничениям; порядок посещения ' +
                'соответствует порядку назначения. Глобальная оптимизация не выполняется.'
              : 'Грамотный диспетчер: заявки разбираются по срочности окна доступа и ' +
                'передаются ближайшему подходящему инженеру, ранее назначенное не ' +
                'пересматривается. Это честный потолок ручного планирования.'}
            {' '}Комплектация оборудованием и заезды на склад совпадают с расчётными —
            сопоставляется качество маршрутизации, а не условия выдачи инструмента.
          </p>
        </div>
        <div className="card">
          <h2>О расхождении по SLA</h2>
          <p className="dim">
            У базового варианта нарушений может быть меньше только потому, что
            часть заявок не принимается к исполнению вовсе. Для заказчика
            невыполненная заявка хуже, чем выполненная с опозданием, поэтому
            ключевым показателем принято «закрыто в срок».
          </p>
        </div>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------- Справочники */

export function ReferenceScreen({ dataset, engineers, plan, datasets, onDataset }: {
  dataset: Dataset | null; engineers: Engineer[]; plan: Plan | null
  datasets: DatasetInfo[]
  onDataset: (action: { activate: string } | { upload: File[]; name: string }) => Promise<void>
}) {
  const [tab, setTab] = useState<'data' | 'dicts' | 'types' | 'equip' | 'wh' | 'staff'>('data')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [files, setFiles] = useState<File[]>([])
  const [name, setName] = useState('')
  if (!dataset) return <div className="empty">Загрузка…</div>

  /** На какой склад инженер заезжает утром — из построенного плана. */
  const pickupAt = (engineerId: string) =>
    plan?.routes.find((r) => r.engineer_id === engineerId)?.pickup_warehouse ?? null

  const run = async (action: Parameters<typeof onDataset>[0]) => {
    setBusy(true); setError(null)
    try {
      await onDataset(action)
      setFiles([]); setName('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  // Вкладки: данные и справочники ТЗ — всегда; оборудование и склады — только
  // у наборов, где они есть (у выгрузки заказчика их нет).
  const tabs = ([
    ['data', 'Данные'], ['dicts', 'Справочники ТЗ'], ['types', 'Типы работ'],
    ...(dataset.uses_equipment ? [['equip', 'Оборудование'], ['wh', 'Склады']] : []),
    ['staff', 'Состав службы'],
  ] as [typeof tab, string][])

  return (
    <div className="stack">
      <div className="card">
        <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
          {tabs.map(([k, label]) => (
            <button key={k} className={tab === k ? 'primary' : 'ghost'}
                    onClick={() => setTab(k)}>{label}</button>
          ))}
        </div>

        {tab === 'data' && (
          <div className="stack">
            <div>
              <h2 style={{ marginTop: 0 }}>Набор данных
                <span className="hint">{dataset.title || dataset.date}</span>
              </h2>
              <div className="tiles">
                {datasets.map((d) => (
                  <div key={d.key} className={`tile${d.key === dataset.key ? ' accent' : ''}`}
                       style={{ flexDirection: 'column', alignItems: 'stretch', gap: 8,
                                opacity: d.available ? 1 : .5 }}>
                    <div style={{ fontWeight: 700 }}>{d.title}</div>
                    <div className="muted" style={{ fontSize: 12 }}>{d.note}</div>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <span className="pill">{{ customer: 'выгрузка заказчика',
                        synthetic: 'синтетика', uploaded: 'загружен' }[d.kind]}</span>
                      <span className="spacer" />
                      {d.key === dataset.key
                        ? <span className="muted" style={{ fontSize: 12 }}>активен</span>
                        : <button className="ghost" disabled={busy || !d.available}
                                  onClick={() => run({ activate: d.key })}>Открыть</button>}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div>
              <h2>Загрузить свой набор
                <span className="hint">CSV или JSON — ТЗ, п. 2.1</span>
              </h2>
              <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
                Два CSV — заявки и инженеры (по имени файла: jobs/заявки,
                engineers/инженеры), при желании третий с событиями; либо один JSON
                со списками jobs, engineers, events; либо snapshot.json сервиса.
                Поля — минимальные из ТЗ: id, адрес или координаты, длительность,
                окно, приоритет, навык, транспорт. Адреса без координат
                геокодируются.
              </p>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                <input type="file" multiple accept=".csv,.json,text/csv,application/json"
                       onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
                <input value={name} placeholder="название набора (необязательно)"
                       style={{ width: 260 }} onChange={(e) => setName(e.target.value)} />
                <button className="primary" disabled={busy || !files.length}
                        onClick={() => run({ upload: files, name })}>
                  {busy ? 'Загружаю…' : 'Загрузить и открыть'}
                </button>
              </div>
              {error && (
                <pre className="err" style={{ whiteSpace: 'pre-wrap', marginTop: 10 }}>{error}</pre>
              )}
            </div>

            {dataset.assumptions.length > 0 && (
              <div>
                <h2>Допущения набора
                  <span className="hint">чего нет в исходных данных и что принято вместо</span>
                </h2>
                <ul className="dim" style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                  {dataset.assumptions.map((a, i) => <li key={i} style={{ marginBottom: 4 }}>{a}</li>)}
                </ul>
              </div>
            )}
          </div>
        )}

        {tab === 'dicts' && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14 }}>
            <div>
              <h2 style={{ marginTop: 0 }}>Навыки
                <span className="hint">у заявки один, у инженера 1–3</span>
              </h2>
              {Object.entries(dataset.specializations).map(([k, v]) => (
                <div key={k} className="pill accent" style={{ display: 'block', marginBottom: 6 }}>{v}</div>
              ))}
            </div>
            <div>
              <h2 style={{ marginTop: 0 }}>Тип транспорта
                <span className="hint">один на инженера; в заявке — только при ограничении</span>
              </h2>
              {dataset.transport_types.map((t) => (
                <div key={t.id} className="pill" style={{ display: 'block', marginBottom: 6 }}>{t.name}</div>
              ))}
            </div>
            <div>
              <h2 style={{ marginTop: 0 }}>Приоритет
                <span className="hint">срочная выше при перепланировании</span>
              </h2>
              {dataset.priorities.map((pr) => (
                <div key={pr.id} className="pill" style={{ display: 'block', marginBottom: 6,
                  color: PRIORITY_COLOR[pr.id] }}>{pr.name}</div>
              ))}
              <p className="muted" style={{ fontSize: 12 }}>
                Обязательные ограничения ТЗ: навык заявки входит в навыки инженера;
                начало работ попадает в окно, а работа с дорогой — в смену; при
                указанном транспорте у инженера именно он.
                {dataset.uses_equipment && ' Оборудование — дополнительное усложнение.'}
                {dataset.return_to_start
                  ? ' В этом наборе маршрут заканчивается возвратом домой.'
                  : ' Возврат в стартовую точку не планируется.'}
              </p>
            </div>
          </div>
        )}

        {/* Вкладка «Типы работ» — матрица «работа → оборудование» там, где
            оборудование есть; иначе простой список с нормативами. */}
        {tab === 'types' && (
          <div style={{ overflowX: 'auto' }}>
            {dataset.uses_equipment && (
              <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
                Матрица «работа → оборудование». Жёлтым — дефицитные приборы:
                по 2 экземпляра на службу.
              </p>
            )}
            <table className="grid" style={{ fontSize: 11.5 }}>
              <thead><tr>
                <th style={{ minWidth: 240 }}>Тип работ</th>
                <th className="r" style={{ width: 90 }}>Норматив</th>
                {dataset.uses_levels && <th className="r" style={{ width: 74 }}>Мин. ур.</th>}
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
                        <b>{w.name}</b>
                        <div className="muted" style={{ fontSize: 11.5 }}>
                          {dataset.specializations[w.specialization]}
                        </div>
                      </td>
                      <td className="r num">{w.base_duration_min} мин</td>
                      {dataset.uses_levels && <td className="r num">{w.min_level}</td>}
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
            {dataset.uses_equipment && (
              <div className="legend" style={{ marginTop: 10 }}>
                <span><i style={{ background: 'var(--info)' }} />требуется</span>
                <span><i style={{ background: 'var(--accent)' }} />дефицитный прибор</span>
              </div>
            )}
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
              <th>Инженер</th><th style={{ width: 320 }}>Навыки</th>
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
                        {s.specialization_name}{dataset.uses_levels ? ` ур. ${s.level}` : ''}
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

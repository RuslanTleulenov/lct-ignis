import type {
  Compare, DayEvent, Engineer, Explanation, Job, LogEntry, Plan, WhyNot,
} from '../api'
import { engineerColor, PRIORITY_COLOR, PRIORITY_LABEL, VEHICLE_LABEL } from '../colors'

export type Tab = 'explain' | 'why' | 'compare' | 'feed'

interface Props {
  tab: Tab
  onTab: (t: Tab) => void
  plan: Plan | null
  engineers: Engineer[]
  jobs: Job[]
  events: DayEvent[]
  log: LogEntry[]
  explanation: Explanation | null
  whyNot: WhyNot | null
  whyNotAll: WhyNot[]
  compare: Compare | null
  selectedJob: string | null
  selectedEngineer: string | null
  busy: boolean
  onSelectJob: (id: string) => void
  onSelectEngineer: (id: string | null) => void
  onPin: (jobId: string, engineerId: string | null) => void
}

export function SidePanel(p: Props) {
  return (
    <div className="side">
      <div className="tabs">
        <button className={p.tab === 'explain' ? 'on' : ''} onClick={() => p.onTab('explain')}>
          Почему так
        </button>
        <button className={p.tab === 'why' ? 'on' : ''} onClick={() => p.onTab('why')}>
          Не назначено{p.plan ? ` (${p.plan.unassigned.length})` : ''}
        </button>
        <button className={p.tab === 'compare' ? 'on' : ''} onClick={() => p.onTab('compare')}>
          Эффект
        </button>
        <button className={p.tab === 'feed' ? 'on' : ''} onClick={() => p.onTab('feed')}>
          Лента
        </button>
      </div>
      <div className="panel">
        {p.tab === 'explain' && <ExplainTab {...p} />}
        {p.tab === 'why' && <WhyTab {...p} />}
        {p.tab === 'compare' && <CompareTab compare={p.compare} />}
        {p.tab === 'feed' && <FeedTab plan={p.plan} events={p.events} log={p.log} />}
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------

function ExplainTab({
  plan, engineers, jobs, explanation, selectedJob, selectedEngineer, busy,
  onSelectJob, onPin,
}: Props) {
  if (selectedEngineer && !selectedJob) {
    const eng = engineers.find((e) => e.id === selectedEngineer)
    const route = plan?.routes.find((r) => r.engineer_id === selectedEngineer)
    if (!eng) return <div className="empty">Инженер не найден</div>
    return (
      <>
        <h3>
          <span className="dot" style={{
            display: 'inline-block', width: 9, height: 9, borderRadius: '50%',
            background: engineerColor(eng.id), marginRight: 6,
          }} />
          {eng.name}
        </h3>
        <div className="kv"><span className="k">Транспорт</span>
          <span className="v">{VEHICLE_LABEL[eng.vehicle_type]}
            {!eng.can_carry_bulky && ' — габарит не увезёт'}</span></div>
        <div className="kv"><span className="k">Смена</span>
          <span className="v">{eng.shift[0]}–{eng.shift[1]}, обед {eng.break_min} мин</span></div>
        <div className="kv"><span className="k">Квалификация</span>
          <span className="v">{eng.skills.map((s) =>
            `${s.specialization_name} ур.${s.level}`).join(', ')}</span></div>
        {route && (
          <>
            <div className="kv"><span className="k">Маршрут</span>
              <span className="v">{route.start}–{route.end}, {route.job_count} заявок</span></div>
            <div className="kv"><span className="k">В пути</span>
              <span className="v">{route.travel_min} мин / {route.travel_km} км</span></div>
            {route.pickup_warehouse && (
              <div className="kv"><span className="k">Утром</span>
                <span className="v">заезд на склад {route.pickup_warehouse}</span></div>
            )}
          </>
        )}
        <h4>Оборудование на руках</h4>
        <p>{eng.equipment.map((q) => q.name).join(', ') || '—'}</p>
        <h4>Визиты</h4>
        {route?.stops.filter((s) => s.kind === 'job').map((s) => (
          <div key={s.job_id} className="card clickable" onClick={() => onSelectJob(s.job_id!)}>
            <div className="top">
              <span className="badge" style={{ background: PRIORITY_COLOR[s.priority!] }}>
                {s.priority}
              </span>
              <span className="ttl">{s.customer}</span>
              <span className="meta">{s.service_start}</span>
            </div>
            <div className="meta">{s.work_type} · {s.district}</div>
          </div>
        )) ?? <p>Маршрут пуст</p>}
      </>
    )
  }

  if (!selectedJob) {
    return (
      <div className="empty">
        Выберите заявку на карте или на Ганте —<br />покажу, почему она попала
        именно этому инженеру.
      </div>
    )
  }

  const job = jobs.find((j) => j.id === selectedJob)
  if (!explanation) {
    return (
      <>
        {job && <JobCard job={job} />}
        <div className="empty">Заявки нет в текущем плане.</div>
      </>
    )
  }

  const pinnedTo = plan?.pins[explanation.job_id]
  return (
    <>
      {job && <JobCard job={job} />}
      <h3>
        <span style={{
          display: 'inline-block', width: 9, height: 9, borderRadius: '50%',
          background: engineerColor(explanation.engineer_id), marginRight: 6,
        }} />
        {explanation.engineer_name}
        {pinnedTo && <span className="badge out" style={{ marginLeft: 8 }}>🔒 закреплено</span>}
      </h3>

      {pinnedTo && (
        <button style={{ marginBottom: 8 }} disabled={busy}
                onClick={() => onPin(explanation.job_id, null)}>
          Снять закрепление и пересчитать
        </button>
      )}

      <h4>Почему он</h4>
      <ul>{explanation.choice.map((r, i) => <li key={i}>{r}</li>)}</ul>

      <h4>Почему в это время</h4>
      <ul>{explanation.timing.map((r, i) => <li key={i}>{r}</li>)}</ul>

      <h4>Альтернативы</h4>
      {explanation.alternatives.length === 0 && (
        <p>Других допустимых исполнителей нет — заявку мог взять только он.</p>
      )}
      {explanation.alternatives.map((a) => (
        <div key={a.engineer_id} className="card" style={{ padding: 8 }}>
          <div className={`reason ${a.possible ? 'yes' : 'no'}`} style={{ marginBottom: 6 }}>
            <span className="mark">{a.possible ? '○' : '✗'}</span>
            <span><b>{a.engineer_name}</b>: {a.detail}</span>
          </div>
          {a.possible && (
            <button style={{ padding: '3px 9px', fontSize: 12 }} disabled={busy}
                    onClick={() => onPin(explanation.job_id, a.engineer_id)}>
              Передать {a.engineer_name.split(' ')[0]}
            </button>
          )}
        </div>
      ))}

      <h4>Ручное вмешательство</h4>
      <p style={{ fontSize: 11.5 }}>
        Закреплённую заявку солвер не тронет ни при одном пересчёте. Цифра у
        альтернативы — стоимость вставки в её нынешний маршрут; итоговая цена
        считается после полного пересчёта и обычно выше, потому что сдвигаются
        и другие визиты. Её и покажет плашка сверху.
      </p>
    </>
  )
}

function JobCard({ job }: { job: Job }) {
  return (
    <div className="card">
      <div className="top">
        <span className="badge" style={{ background: PRIORITY_COLOR[job.priority] }}>
          {job.priority}
        </span>
        <span className="ttl">{job.customer}</span>
      </div>
      <div className="meta">{job.work_type}</div>
      <div style={{ height: 6 }} />
      <div className="kv"><span className="k">Адрес</span><span className="v">{job.address}</span></div>
      <div className="kv"><span className="k">Окно</span>
        <span className="v">{job.window[0]}–{job.window[1]}
          {job.window_hard && <b style={{ color: 'var(--warn)' }}> жёсткое</b>}</span></div>
      <div className="kv"><span className="k">SLA до</span><span className="v">{job.sla_deadline}</span></div>
      <div className="kv"><span className="k">Требуется</span>
        <span className="v">{job.specialization_name}, ур. {job.min_level}</span></div>
      <div className="kv"><span className="k">Сложность</span>
        <span className="v">{job.complexity} из 5 · {job.duration_min} мин</span></div>
      <div className="kv"><span className="k">Оборудование</span>
        <span className="v">{job.required_equipment.map((q) =>
          q.bulky ? `${q.name} (габарит)` : q.name).join(', ')}</span></div>
      <div className="kv"><span className="k">Приоритет</span>
        <span className="v">{PRIORITY_LABEL[job.priority]}</span></div>
    </div>
  )
}

// --------------------------------------------------------------------------

function WhyTab({ plan, whyNot, whyNotAll, selectedJob, onSelectJob }: Props) {
  if (!plan) return <div className="empty">План ещё не построен</div>
  if (!plan.unassigned.length) {
    return <div className="empty">Все заявки назначены.</div>
  }

  const current = selectedJob === whyNot?.job_id ? whyNot : null

  return (
    <>
      <p>
        {plan.unassigned.length} заявок не влезло в день. Это не сбой: спрос выше
        ёмкости, и система показывает, что именно упирается.
      </p>
      {plan.unassigned.map((job) => {
        const info = whyNotAll.find((w) => w.job_id === job.id)
        const open = current?.job_id === job.id
        return (
          <div
            key={job.id}
            className="card clickable"
            onClick={() => onSelectJob(job.id)}
            style={open ? { borderColor: 'var(--accent)' } : undefined}
          >
            <div className="top">
              <span className="badge" style={{ background: PRIORITY_COLOR[job.priority] }}>
                {job.priority}
              </span>
              <span className="ttl">{job.customer}</span>
            </div>
            <div className="meta">{job.work_type} · {job.district} ·
              окно {job.window[0]}–{job.window[1]}</div>
            {(open ? current : info) && (
              <>
                <div style={{ height: 6 }} />
                <div style={{ color: 'var(--text)' }}>{(open ? current : info)!.verdict}</div>
                {open && current!.qualified.map((b) => (
                  <div key={b.engineer_id} className="reason no" style={{ marginTop: 5 }}>
                    <span className="mark">✗</span>
                    <span><b>{b.engineer_name}</b>: {b.detail}</span>
                  </div>
                ))}
                {open && current!.feasible_with_shift.map((f, i) => (
                  <div key={i} className="reason yes" style={{ marginTop: 5 }}>
                    <span className="mark">○</span>
                    <span><b>{f.engineer_name}</b>: {f.detail}</span>
                  </div>
                ))}
              </>
            )}
          </div>
        )
      })}
    </>
  )
}

// --------------------------------------------------------------------------

function CompareTab({ compare }: { compare: Compare | null }) {
  if (!compare) {
    return <div className="empty">Постройте план — посчитаю эффект против ручного планирования.</div>
  }
  return (
    <>
      <h3>Эффект против ручного планирования</h3>
      <p style={{ fontSize: 11.5 }}>{compare.note}</p>
      <table className="cmp">
        <thead>
          <tr><th>Показатель</th><th>Вручную</th><th>Оптимизатор</th><th>Эффект</th></tr>
        </thead>
        <tbody>
          {compare.rows.map((r, i) => (
            <tr key={r.label} className={i === 0 ? 'head' : ''}>
              <td>{r.label}</td>
              <td>{r.manual}</td>
              <td>{r.optimized}</td>
              <td className={r.effect.includes('✓') ? 'eff-good'
                : r.effect.startsWith('+') && !r.effect.includes('✓') ? 'eff-bad' : ''}>
                {r.effect}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <h4>Как считался baseline</h4>
      <p>
        Грамотный диспетчер: разбирает заявки по срочности окна, отдаёт каждую
        ближайшему подходящему инженеру, назначенное не переставляет. Комплектация
        инструментом и заезды на склад — те же, что у оптимизатора: сравнивается
        маршрутизация, а не удача утренней выдачи.
      </p>
      <h4>Почему нарушений SLA больше</h4>
      <p>
        У ручного плана их меньше только потому, что он не взял неудобные заявки
        вовсе, а для клиента несделанная заявка хуже опоздания. Поэтому первой
        строкой стоит «закрыто в срок».
      </p>
    </>
  )
}

// --------------------------------------------------------------------------

function FeedTab({ plan, events, log }: {
  plan: Plan | null; events: DayEvent[]; log: LogEntry[]
}) {
  const now = plan?.now ?? '08:00'
  const upcoming = events.filter((e) => e.at > now)
  return (
    <>
      <h4>Что уже произошло</h4>
      {log.length === 0 && <p>Пока ничего. Постройте план.</p>}
      {[...log].reverse().map((entry, i) => (
        <div key={i} className={`feed-item ${entry.kind}`}>
          <span className="t">{entry.at}</span>
          <span className="txt">
            {entry.kind === 'event' && '● '}
            {entry.text}
          </span>
        </div>
      ))}
      <h4>Впереди ({upcoming.length})</h4>
      {upcoming.slice(0, 12).map((e, i) => (
        <div key={i} className="feed-item future">
          <span className="t">{e.at}</span>
          <span className="txt">{e.comment}</span>
        </div>
      ))}
    </>
  )
}

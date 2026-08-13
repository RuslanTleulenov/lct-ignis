import { useMemo, useState } from 'react'
import type { Dataset, JobInput } from '../api'
import { PRIORITY_COLOR, PRIORITY_LABEL } from '../colors'
import { outsideArea, PointPicker } from './PointPicker'

/**
 * Приём заявки в течение дня.
 *
 * Всё, что можно вывести, выводится: тип работ задаёт специализацию,
 * требуемый уровень и набор оборудования, а категория сложности — норматив
 * времени. Диспетчер не должен вводить то, что уже записано в справочнике.
 *
 * После сохранения сервис пересчитывает остаток дня сам — это и есть
 * «автоматическое перепланирование при поступлении новой заявки».
 */

const PRIORITIES: JobInput['priority'][] = ['P1', 'P2', 'P3', 'P4']

/** Норматив визита. Формула та же, что в сервисе и генераторе. */
export function nominalDuration(base: number, complexity: number): number {
  return Math.round(base * (0.85 + 0.15 * complexity) / 5) * 5
}

interface Props {
  dataset: Dataset
  now: string
  busy: boolean
  error: string | null
  onSubmit: (body: JobInput) => void
  onClose: () => void
}

export function JobForm({ dataset, now, busy, error, onSubmit, onClose }: Props) {
  const [form, setForm] = useState<JobInput>(() => ({
    customer: '', work_type_id: dataset.work_types[0]?.id ?? '',
    address: '', district: '',
    lat: 55.7558, lon: 37.6173,
    complexity: 3, priority: 'P2',
    tw_start: now, tw_end: addHours(now, 4), tw_hard: false,
    contact_phone: '',
  }))

  const set = <K extends keyof JobInput>(k: K, v: JobInput[K]) =>
    setForm((f) => ({ ...f, [k]: v }))

  const wt = useMemo(
    () => dataset.work_types.find((w) => w.id === form.work_type_id),
    [dataset, form.work_type_id])
  const duration = wt ? nominalDuration(wt.base_duration_min, form.complexity) : 0
  const windowMin = toMin(form.tw_end) - toMin(form.tw_start)

  const outside = outsideArea(form.lat, form.lon)
  const tooNarrow = windowMin < duration
  const closed = toMin(form.tw_end) <= toMin(now)
  const ready = form.customer.trim().length >= 2 && !!wt
    && !outside && !tooNarrow && !closed && windowMin > 0

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2 style={{ margin: 0 }}>Новая заявка</h2>
          <span className="pill">поступила в {now}</span>
          <span className="spacer" />
          <button className="ghost" onClick={onClose}>Закрыть</button>
        </div>

        <div className="modal-body">
          <div className="form-grid">
            <label className="field" style={{ gridColumn: 'span 2' }}>
              <span>Заказчик</span>
              <input value={form.customer} placeholder="Название организации"
                     onChange={(e) => set('customer', e.target.value)} />
            </label>
            <label className="field" style={{ gridColumn: 'span 2' }}>
              <span>Контактный телефон</span>
              <input value={form.contact_phone} placeholder="+7 900 000-00-00"
                     onChange={(e) => set('contact_phone', e.target.value)} />
            </label>

            <label className="field" style={{ gridColumn: 'span 3' }}>
              <span>Тип работ</span>
              <select value={form.work_type_id}
                      onChange={(e) => set('work_type_id', e.target.value)}>
                {dataset.work_types.map((w) => (
                  <option key={w.id} value={w.id}>{w.name}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Категория сложности</span>
              <select value={form.complexity}
                      onChange={(e) => set('complexity', Number(e.target.value))}>
                {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n} из 5</option>)}
              </select>
            </label>
          </div>

          {wt && (
            <div className="derived">
              <span>Специализация: <b>{dataset.specializations[wt.specialization]}</b>,
                не ниже <b>{wt.min_level}</b> уровня</span>
              <span>Норматив: <b className="num">{duration} мин</b></span>
              <span>Оборудование: {wt.equipment.map((q) => q.name).join(', ') || '—'}</span>
            </div>
          )}

          <h3>Срочность и доступ</h3>
          <div className="chips" style={{ marginBottom: 12 }}>
            {PRIORITIES.map((p) => (
              <button key={p} className={`chip-btn plain${form.priority === p ? ' on' : ''}`}
                      style={form.priority === p
                        ? { borderColor: PRIORITY_COLOR[p], color: PRIORITY_COLOR[p] }
                        : undefined}
                      onClick={() => set('priority', p)}>
                {p} · {PRIORITY_LABEL[p]}
              </button>
            ))}
          </div>

          <div className="form-grid">
            <label className="field">
              <span>Окно доступа с</span>
              <input type="time" value={form.tw_start}
                     onChange={(e) => set('tw_start', e.target.value)} />
            </label>
            <label className="field">
              <span>до</span>
              <input type="time" value={form.tw_end}
                     onChange={(e) => set('tw_end', e.target.value)} />
            </label>
            <label className="field" style={{ justifyContent: 'flex-end' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8,
                              fontSize: 13, cursor: 'pointer' }}>
                <input type="checkbox" checked={form.tw_hard} style={{ width: 'auto' }}
                       onChange={(e) => set('tw_hard', e.target.checked)} />
                жёсткое окно
              </label>
            </label>
            <label className="field">
              <span>Район</span>
              <input value={form.district} placeholder="необязательно"
                     onChange={(e) => set('district', e.target.value)} />
            </label>
          </div>

          {tooNarrow && !closed && (
            <div className="err" style={{ margin: '10px 0 0' }}>
              Окно {windowMin} мин короче норматива работ {duration} мин —
              расширьте его или снизьте категорию сложности.
            </div>
          )}
          {closed && (
            <div className="err" style={{ margin: '10px 0 0' }}>
              Окно закрывается в {form.tw_end}, сейчас {now} — заявку уже не выполнить.
            </div>
          )}

          <h3>Адрес объекта</h3>
          <label className="field" style={{ marginBottom: 10 }}>
            <span>Адрес</span>
            <input value={form.address} placeholder="Улица, дом"
                   onChange={(e) => set('address', e.target.value)} />
          </label>
          <PointPicker lat={form.lat} lon={form.lon}
                       onPick={(lat, lon) => setForm((f) => ({ ...f, lat, lon }))} />
          <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
            Кликните по карте, чтобы поставить объект. Сейчас{' '}
            <span className="num">{form.lat.toFixed(5)}, {form.lon.toFixed(5)}</span>
            {outside && <b style={{ color: 'var(--danger)' }}> — вне зоны обслуживания</b>}
          </div>
        </div>

        {error && <div className="err" style={{ margin: '0 20px' }}>{error}</div>}

        <div className="modal-foot">
          <span className="muted" style={{ fontSize: 12 }}>
            После приёма сервис сам пересчитает остаток дня.
          </span>
          <button className="ghost" onClick={onClose}>Отмена</button>
          <button className="primary" disabled={busy || !ready}
                  onClick={() => onSubmit(form)}>
            {busy ? 'Пересчитываю день…' : 'Принять заявку'}
          </button>
        </div>
      </div>
    </div>
  )
}

const toMin = (t: string) => {
  const [h, m] = t.split(':').map(Number)
  return h * 60 + m
}
const addHours = (t: string, h: number) => {
  const total = Math.min(toMin(t) + h * 60, 23 * 60 + 59)
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

import { useEffect, useState } from 'react'
import type { Dataset, Engineer, EngineerInput } from '../api'
import { outsideArea, PointPicker } from './PointPicker'

/**
 * Карточка инженера: заведение и правка.
 *
 * Дом задаётся точкой на карте, а не адресом: геокодирования в сервисе нет,
 * а координаты нужны точные — от них считается весь маршрут. Клик по карте
 * надёжнее строки адреса и не требует внешнего сервиса.
 */

const VEHICLES: [EngineerInput['vehicle_type'], string][] = [
  ['car', 'Легковой'], ['van', 'Фургон'], ['walk_transit', 'Пешком + метро'],
]

const EMPTY: EngineerInput = {
  name: '', skills: {}, shift_start: '09:00', shift_end: '18:00',
  break_from: '12:00', break_to: '15:00', break_min: 45,
  vehicle_type: 'car', home_lat: 55.7558, home_lon: 37.6173,
  home_address: '', onboard_equipment: [], max_overtime_min: 60,
}

interface Props {
  dataset: Dataset
  engineer: Engineer | null          // null — заводим нового
  busy: boolean
  error: string | null
  onSubmit: (body: EngineerInput) => void
  onClose: () => void
  onDelete?: () => void
}

export function EngineerForm({
  dataset, engineer, busy, error, onSubmit, onClose, onDelete,
}: Props) {
  const [form, setForm] = useState<EngineerInput>(() => engineer
    ? {
      id: engineer.id,
      name: engineer.name,
      skills: Object.fromEntries(engineer.skills.map((s) => [s.specialization, s.level])),
      shift_start: engineer.shift[0], shift_end: engineer.shift[1],
      break_from: engineer.break_window[0], break_to: engineer.break_window[1],
      break_min: engineer.break_min,
      vehicle_type: engineer.vehicle_type,
      home_lat: engineer.home.lat, home_lon: engineer.home.lon,
      home_address: engineer.home.address,
      onboard_equipment: engineer.equipment.map((q) => q.id),
      max_overtime_min: engineer.max_overtime_min,
    }
    : EMPTY)

  const set = <K extends keyof EngineerInput>(k: K, v: EngineerInput[K]) =>
    setForm((f) => ({ ...f, [k]: v }))

  const specs = Object.entries(dataset.specializations)
  const skillRows = Object.entries(form.skills)
  const canCarryBulky = form.vehicle_type !== 'walk_transit'

  // Пеший инженер не увезёт габарит: снимаем такие позиции сразу, чтобы
  // человек не получил отказ сервера после заполнения всей формы.
  useEffect(() => {
    if (canCarryBulky) return
    const heavy = new Set(dataset.equipment.filter((q) => q.bulky).map((q) => q.id))
    if (form.onboard_equipment.some((q) => heavy.has(q))) {
      set('onboard_equipment', form.onboard_equipment.filter((q) => !heavy.has(q)))
    }
  }, [canCarryBulky, form.onboard_equipment, dataset.equipment])

  const outside = outsideArea(form.home_lat, form.home_lon)

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2 style={{ margin: 0 }}>
            {engineer ? `Инженер ${engineer.id}` : 'Новый инженер'}
          </h2>
          <button className="ghost" onClick={onClose}>Закрыть</button>
        </div>

        <div className="modal-body">
          <div className="form-grid">
            <label className="field" style={{ gridColumn: '1 / -1' }}>
              <span>ФИО</span>
              <input value={form.name} placeholder="Фамилия и имя"
                     onChange={(e) => set('name', e.target.value)} />
            </label>

            <label className="field">
              <span>Транспорт</span>
              <select value={form.vehicle_type}
                      onChange={(e) => set('vehicle_type',
                        e.target.value as EngineerInput['vehicle_type'])}>
                {VEHICLES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Переработка, мин</span>
              <input type="number" min={0} max={240} value={form.max_overtime_min}
                     onChange={(e) => set('max_overtime_min', Number(e.target.value))} />
            </label>

            <label className="field">
              <span>Смена с</span>
              <input type="time" value={form.shift_start}
                     onChange={(e) => set('shift_start', e.target.value)} />
            </label>
            <label className="field">
              <span>до</span>
              <input type="time" value={form.shift_end}
                     onChange={(e) => set('shift_end', e.target.value)} />
            </label>

            <label className="field">
              <span>Окно обеда с</span>
              <input type="time" value={form.break_from}
                     onChange={(e) => set('break_from', e.target.value)} />
            </label>
            <label className="field">
              <span>до / длительность</span>
              <div style={{ display: 'flex', gap: 8 }}>
                <input type="time" value={form.break_to}
                       onChange={(e) => set('break_to', e.target.value)} />
                <input type="number" min={0} max={180} style={{ width: 80 }}
                       value={form.break_min}
                       onChange={(e) => set('break_min', Number(e.target.value))} />
              </div>
            </label>
          </div>

          <h3>Квалификация</h3>
          <div className="chips">
            {specs.map(([code, label]) => {
              const level = form.skills[code]
              return (
                <div key={code} className={`chip${level ? ' on' : ''}`}>
                  <button className="chip-btn" onClick={() => {
                    const next = { ...form.skills }
                    if (level) delete next[code]
                    else next[code] = 2
                    set('skills', next)
                  }}>{label}</button>
                  {level && (
                    <select value={level} onChange={(e) =>
                      set('skills', { ...form.skills, [code]: Number(e.target.value) })}>
                      {[1, 2, 3, 4].map((n) => <option key={n} value={n}>ур. {n}</option>)}
                    </select>
                  )}
                </div>
              )
            })}
          </div>
          {!skillRows.length && (
            <div className="muted" style={{ fontSize: 12 }}>
              Выберите хотя бы одну специализацию — без неё заявки назначать не из чего.
            </div>
          )}

          <h3>Оборудование на руках</h3>
          <div className="chips">
            {dataset.equipment.map((q) => {
              const on = form.onboard_equipment.includes(q.id)
              const blocked = q.bulky && !canCarryBulky
              return (
                <button key={q.id} disabled={blocked}
                        className={`chip-btn plain${on ? ' on' : ''}`}
                        title={blocked ? 'Габарит: нужен автомобиль' : q.name}
                        onClick={() => set('onboard_equipment', on
                          ? form.onboard_equipment.filter((x) => x !== q.id)
                          : [...form.onboard_equipment, q.id])}>
                  {q.name}{q.bulky && ' ⬛'}
                </button>
              )
            })}
          </div>
          <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
            ⬛ — габаритное, доступно только с автомобилем. Недостающее инженер
            получит утром на складе.
          </div>

          <h3>Дом — точка выезда</h3>
          <label className="field" style={{ marginBottom: 10 }}>
            <span>Адрес</span>
            <input value={form.home_address} placeholder="Улица, дом"
                   onChange={(e) => set('home_address', e.target.value)} />
          </label>
          <PointPicker lat={form.home_lat} lon={form.home_lon}
                       onPick={(lat, lon) => setForm((f) => ({ ...f, home_lat: lat, home_lon: lon }))} />
          <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
            Кликните по карте, чтобы поставить точку. Сейчас{' '}
            <span className="num">{form.home_lat.toFixed(5)}, {form.home_lon.toFixed(5)}</span>
            {outside && <b style={{ color: 'var(--danger)' }}> — вне зоны обслуживания</b>}
          </div>
        </div>

        {error && <div className="err" style={{ margin: '0 20px' }}>{error}</div>}

        <div className="modal-foot">
          <span className="muted" style={{ fontSize: 12 }}>
            Инженер попадёт в маршруты со следующего «Построить план».
          </span>
          {onDelete && (
            <button className="ghost" disabled={busy}
                    style={{ color: 'var(--danger)', borderColor: 'var(--danger)' }}
                    onClick={onDelete}>Удалить</button>
          )}
          <button className="ghost" onClick={onClose}>Отмена</button>
          <button className="primary" disabled={busy || outside || !skillRows.length}
                  onClick={() => onSubmit(form)}>
            {busy ? 'Сохраняю…' : engineer ? 'Сохранить' : 'Завести'}
          </button>
        </div>
      </div>
    </div>
  )
}

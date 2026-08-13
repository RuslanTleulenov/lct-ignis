/** Форматирование чисел и времени для интерфейса. */

export function toMin(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number)
  return h * 60 + m
}

export function hhmm(min: number): string {
  return `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`
}

/** 486 -> «8 ч 06 м». Часы с минутами читаются быстрее, чем «486 мин». */
export function dur(min: number): string {
  if (min < 60) return `${min} м`
  const h = Math.floor(min / 60)
  const m = min % 60
  return m ? `${h} ч ${String(m).padStart(2, '0')} м` : `${h} ч`
}

export function num(value: number, digits = 0): string {
  return value.toLocaleString('ru-RU', {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  })
}

/**
 * Склонение существительного при числительном: plural(2, 'позиция',
 * 'позиции', 'позиций') -> «позиции». Без этого в интерфейсе появляются
 * «2 позиций» и «21 заявок».
 */
export function plural(n: number, one: string, few: string, many: string): string {
  const mod100 = Math.abs(n) % 100
  if (mod100 >= 11 && mod100 <= 14) return many
  const mod10 = mod100 % 10
  if (mod10 === 1) return one
  if (mod10 >= 2 && mod10 <= 4) return few
  return many
}

const WEEKDAY = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб']
const MONTH = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']

/** «2026-08-12» -> «ср, 12 августа» — формат подзаголовка из макета. */
export function dayLabel(iso: string): string {
  const d = new Date(iso + 'T00:00:00')
  if (Number.isNaN(d.getTime())) return iso
  return `${WEEKDAY[d.getDay()]}, ${d.getDate()} ${MONTH[d.getMonth()]}`
}

export function initials(name: string): string {
  return name.split(/\s+/).slice(0, 2).map((w) => w[0] ?? '').join('').toUpperCase()
}

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

export function initials(name: string): string {
  return name.split(/\s+/).slice(0, 2).map((w) => w[0] ?? '').join('').toUpperCase()
}

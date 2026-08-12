/**
 * Цвета маршрутов.
 *
 * Инженеров под два десятка, и различать их нужно на карте одним взглядом,
 * поэтому палитра подобрана по тону, а не сгенерирована из хеша: соседние
 * оттенки разведены, а на светлой подложке OSM все читаются одинаково хорошо.
 */

const PALETTE = [
  '#e6194b', '#3cb44b', '#4363d8', '#f58231', '#911eb4',
  '#42d4f4', '#f032e6', '#bfef45', '#fabed4', '#469990',
  '#dcbeff', '#9a6324', '#800000', '#aaffc3', '#808000',
  '#ffd8b1', '#000075', '#a9a9a9', '#e6beff', '#ff6f61',
]

const cache = new Map<string, string>()

export function engineerColor(id: string): string {
  const known = cache.get(id)
  if (known) return known
  // ENG-07 -> 7: порядковый номер даёт стабильный цвет между перезапусками
  const n = Number(id.replace(/\D/g, '')) || cache.size
  const color = PALETTE[(n - 1 + PALETTE.length) % PALETTE.length]
  cache.set(id, color)
  return color
}

export const PRIORITY_COLOR: Record<string, string> = {
  P1: '#ff4d4f',
  P2: '#ff9f43',
  P3: '#4dabf7',
  P4: '#868e96',
}

export const PRIORITY_LABEL: Record<string, string> = {
  P1: 'Авария',
  P2: 'Срочная',
  P3: 'Плановая',
  P4: 'Низкий приоритет',
}

export const VEHICLE_LABEL: Record<string, string> = {
  car: 'Легковой',
  van: 'Фургон',
  walk_transit: 'Пешком',
}

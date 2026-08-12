import { useEffect, useState } from 'react'

/**
 * Хеш-роутер на минимуме.
 *
 * Экранов семь, вложенность одна — `react-router` здесь дал бы 40 КБ ради
 * разбора двух сегментов пути. Зависимостей во фронтенде намеренно три.
 */

export type Screen =
  | 'overview' | 'engineers' | 'engineer' | 'jobs' | 'job'
  | 'backlog' | 'effect' | 'reference'

export interface Route {
  screen: Screen
  id: string | null
  path: string
}

function parse(hash: string): Route {
  const path = hash.replace(/^#/, '') || '/'
  const [, head = '', id = ''] = path.split('/')
  const map: Record<string, Screen> = {
    '': 'overview',
    engineers: id ? 'engineer' : 'engineers',
    jobs: id ? 'job' : 'jobs',
    backlog: 'backlog',
    effect: 'effect',
    reference: 'reference',
  }
  return { screen: map[head] ?? 'overview', id: id || null, path }
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(window.location.hash))
  useEffect(() => {
    const onChange = () => {
      setRoute(parse(window.location.hash))
      // При смене экрана прокрутка должна начинаться сверху: иначе переход
      // с длинного списка на карточку открывается где-то в середине.
      document.querySelector('.screen')?.scrollTo({ top: 0 })
    }
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}

export function go(path: string): void {
  window.location.hash = path
}

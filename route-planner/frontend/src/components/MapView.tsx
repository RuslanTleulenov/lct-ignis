import { useEffect, useRef } from 'react'
import maplibregl, { type StyleSpecification } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Dataset, Plan } from '../api'
import { engineerColor } from '../colors'

/**
 * Карта маршрутов.
 *
 * Подложка — растровые тайлы OSM без ключей и регистраций: демонстрация не
 * должна зависеть от чужого API-токена. Если тайлы не загрузятся, маршруты
 * всё равно отрисуются на пустом фоне — геометрия своя, не из тайлов.
 */

const STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap',
    },
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
}

const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] }

interface Props {
  plan: Plan | null
  dataset: Dataset | null
  selectedJob: string | null
  selectedEngineer: string | null
  changedJobs: Set<string>
  onSelectJob: (id: string) => void
  onSelectEngineer: (id: string | null) => void
  /** Показывать маршрут одного инженера и подогнать масштаб под него. */
  focusEngineer?: string | null
  /** Приглушить цвета: 13 ярких линий на общей карте не различаются. */
  neutral?: boolean
}

export function MapView({
  plan, dataset, selectedJob, selectedEngineer, changedJobs,
  onSelectJob, onSelectEngineer, focusEngineer = null, neutral = false,
}: Props) {
  const holder = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const ready = useRef(false)
  const fitted = useRef<string | boolean>(false)
  // Обработчики читают актуальные пропсы через ref: иначе на карте навсегда
  // останется замыкание с первого рендера и клики начнут выбирать не то.
  const handlers = useRef({ onSelectJob, onSelectEngineer })
  handlers.current = { onSelectJob, onSelectEngineer }

  useEffect(() => {
    if (!holder.current || map.current) return
    const m = new maplibregl.Map({
      container: holder.current,
      style: STYLE,
      center: [37.62, 55.75],
      zoom: 9.6,
      attributionControl: { compact: true },
    })
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    map.current = m

    // MapLibre измеряет контейнер один раз при создании. Если в этот момент
    // раскладка ещё не устоялась (а в React со StrictMode так и бывает), карта
    // навсегда остаётся в дефолтных 400×300 внутри контейнера любого размера.
    // Наблюдаем за размером явно — заодно чинится и ресайз окна.
    const ro = new ResizeObserver(() => m.resize())
    ro.observe(holder.current)

    m.on('load', () => {
      m.resize()
      m.addSource('routes', { type: 'geojson', data: EMPTY })
      m.addSource('stops', { type: 'geojson', data: EMPTY })
      m.addSource('unassigned', { type: 'geojson', data: EMPTY })
      m.addSource('depots', { type: 'geojson', data: EMPTY })

      m.addLayer({
        id: 'routes', type: 'line', source: 'routes',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': ['get', 'color'],
          'line-width': ['interpolate', ['linear'], ['zoom'], 9, 1.6, 14, 3.4],
          'line-opacity': 0.85,
        },
      })
      m.addLayer({
        id: 'depots', type: 'circle', source: 'depots',
        paint: {
          'circle-radius': 7,
          'circle-color': '#1f2733',
          'circle-stroke-color': '#f1c40f',
          'circle-stroke-width': 2,
        },
      })
      m.addLayer({
        id: 'unassigned', type: 'circle', source: 'unassigned',
        paint: {
          'circle-radius': 6,
          'circle-color': '#12151c',
          'circle-stroke-color': '#f03e3e',
          'circle-stroke-width': 2.5,
        },
      })
      m.addLayer({
        id: 'stops', type: 'circle', source: 'stops',
        paint: {
          'circle-radius': [
            'case', ['boolean', ['get', 'changed'], false], 8,
            ['interpolate', ['linear'], ['zoom'], 9, 4, 14, 7],
          ],
          'circle-color': ['get', 'color'],
          'circle-stroke-color': [
            'case', ['boolean', ['get', 'changed'], false], '#ffffff', '#0e1117',
          ],
          'circle-stroke-width': [
            'case', ['boolean', ['get', 'changed'], false], 2.5, 1.2,
          ],
        },
      })
      m.addLayer({
        id: 'stop-order', type: 'symbol', source: 'stops',
        minzoom: 12,
        layout: {
          'text-field': ['get', 'seq'],
          'text-size': 10,
          'text-font': ['Noto Sans Regular'],
          'text-allow-overlap': true,
        },
        paint: { 'text-color': '#0e1117' },
      })

      const popup = new maplibregl.Popup({ closeButton: false, offset: 12 })
      for (const layer of ['stops', 'unassigned'] as const) {
        m.on('mouseenter', layer, (e) => {
          m.getCanvas().style.cursor = 'pointer'
          const p = e.features?.[0]?.properties as Record<string, string> | undefined
          if (!p) return
          popup
            .setLngLat((e.features![0].geometry as GeoJSON.Point).coordinates as [number, number])
            .setHTML(
              `<b>${p.customer ?? p.job_id}</b><br/>${p.work_type ?? ''}<br/>` +
              `<span style="color:#93a0b5">${p.time ?? 'не назначена'}</span>`,
            )
            .addTo(m)
        })
        m.on('mouseleave', layer, () => {
          m.getCanvas().style.cursor = ''
          popup.remove()
        })
        m.on('click', layer, (e) => {
          const p = e.features?.[0]?.properties as Record<string, string> | undefined
          if (p?.job_id) handlers.current.onSelectJob(p.job_id)
        })
      }
      m.on('click', 'routes', (e) => {
        const p = e.features?.[0]?.properties as Record<string, string> | undefined
        if (p?.engineer_id) handlers.current.onSelectEngineer(p.engineer_id)
      })
      m.on('click', (e) => {
        const hits = m.queryRenderedFeatures(e.point, {
          layers: ['stops', 'unassigned', 'routes'],
        })
        if (!hits.length) handlers.current.onSelectEngineer(null)
      })

      ready.current = true
      m.fire('data-ready')
    })

    return () => {
      ro.disconnect()
      m.remove()
      map.current = null
      ready.current = false
    }
  }, [])

  // ---- данные плана ----
  useEffect(() => {
    const m = map.current
    if (!m) return
    const apply = () => {
      if (!ready.current) return

      const routeFeatures: GeoJSON.Feature[] = []
      const stopFeatures: GeoJSON.Feature[] = []

      for (const route of plan?.routes ?? []) {
        if (!route.job_count) continue
        if (focusEngineer && route.engineer_id !== focusEngineer) continue
        const color = neutral && !focusEngineer
          ? '#6b6b7a' : engineerColor(route.engineer_id)
        // Ломаная приходит с бэкенда по перегонам и повторяет улицы. Если её
        // нет (время считалось приближением), соединяем точки прямой — как
        // раньше, но это заметно и честно отражает качество расчёта.
        const line: number[][] = []
        for (const s of route.stops) {
          if (s.geometry?.length) {
            line.push(...(line.length ? s.geometry.slice(1) : s.geometry))
          } else {
            line.push([s.lon, s.lat])
          }
        }
        routeFeatures.push({
          type: 'Feature',
          properties: { engineer_id: route.engineer_id, color },
          geometry: { type: 'LineString', coordinates: line },
        })
        let seq = 0
        for (const s of route.stops) {
          if (s.kind !== 'job' || !s.job_id) continue
          seq += 1
          stopFeatures.push({
            type: 'Feature',
            properties: {
              job_id: s.job_id,
              engineer_id: route.engineer_id,
              color,
              seq: String(seq),
              changed: changedJobs.has(s.job_id),
              customer: s.customer ?? '',
              work_type: s.work_type ?? '',
              time: `${s.service_start}–${s.service_end} · ${route.engineer_name}`,
            },
            geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
          })
        }
      }

      ;(m.getSource('routes') as maplibregl.GeoJSONSource | undefined)?.setData({
        type: 'FeatureCollection', features: routeFeatures,
      })
      ;(m.getSource('stops') as maplibregl.GeoJSONSource | undefined)?.setData({
        type: 'FeatureCollection', features: stopFeatures,
      })
      ;(m.getSource('unassigned') as maplibregl.GeoJSONSource | undefined)?.setData({
        type: 'FeatureCollection',
        features: (plan?.unassigned ?? []).map((j) => ({
          type: 'Feature',
          properties: {
            job_id: j.id, customer: j.customer, work_type: j.work_type,
            time: 'не назначена',
          },
          geometry: { type: 'Point', coordinates: [j.lon, j.lat] },
        })),
      })
      ;(m.getSource('depots') as maplibregl.GeoJSONSource | undefined)?.setData({
        type: 'FeatureCollection',
        features: (dataset?.warehouses ?? []).map((w) => ({
          type: 'Feature',
          properties: { customer: w.name, work_type: w.address, time: `с ${w.open[0]} до ${w.open[1]}` },
          geometry: { type: 'Point', coordinates: [w.lon, w.lat] },
        })),
      })

      // На карточке инженера масштаб подгоняется под его маршрут при каждой
      // смене инженера, на общей карте — только один раз, иначе карта будет
      // прыгать после каждого пересчёта.
      const shouldFit = focusEngineer ? fitted.current !== focusEngineer
        : !fitted.current
      if (shouldFit && stopFeatures.length) {
        const b = new maplibregl.LngLatBounds()
        for (const f of stopFeatures) {
          b.extend((f.geometry as GeoJSON.Point).coordinates as [number, number])
        }
        m.fitBounds(b, { padding: 60, duration: focusEngineer ? 400 : 0 })
        fitted.current = focusEngineer || true
      }
    }
    if (ready.current) apply()
    else m.once('data-ready', apply)
  }, [plan, dataset, changedJobs, focusEngineer, neutral])

  // ---- подсветка выбранного ----
  useEffect(() => {
    const m = map.current
    if (!m || !ready.current) return
    const dimRoutes: maplibregl.ExpressionSpecification | number = selectedEngineer
      ? ['case', ['==', ['get', 'engineer_id'], selectedEngineer], 1, 0.1]
      : 0.85
    const dimStops: maplibregl.ExpressionSpecification | number = selectedEngineer
      ? ['case', ['==', ['get', 'engineer_id'], selectedEngineer], 1, 0.15]
      : 1
    m.setPaintProperty('routes', 'line-opacity', dimRoutes)
    m.setPaintProperty('routes', 'line-width', selectedEngineer
      ? ['case', ['==', ['get', 'engineer_id'], selectedEngineer], 5, 1.5]
      : ['interpolate', ['linear'], ['zoom'], 9, 1.6, 14, 3.4])
    m.setPaintProperty('stops', 'circle-opacity', dimStops)
    m.setPaintProperty('stops', 'circle-stroke-color', selectedJob
      ? ['case', ['==', ['get', 'job_id'], selectedJob], '#ffffff', '#0e1117']
      : ['case', ['boolean', ['get', 'changed'], false], '#ffffff', '#0e1117'])
  }, [selectedEngineer, selectedJob, plan])

  return <div ref={holder} style={{ position: 'absolute', inset: 0 }} />
}

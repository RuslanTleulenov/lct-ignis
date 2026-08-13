import { useEffect, useMemo, useRef } from 'react'
import maplibregl from 'maplibre-gl'

/**
 * Выбор точки на карте.
 *
 * Используется и для дома инженера, и для адреса объекта. Геокодирования в
 * сервисе нет, а координаты нужны точные — от них считается весь маршрут,
 * поэтому точка ставится кликом, а не выводится из строки адреса.
 */

//: Границы зоны обслуживания — те же, что проверяет сервис.
export const SERVICE_AREA = { south: 55.55, west: 37.30, north: 55.94, east: 37.88 }

export function outsideArea(lat: number, lon: number): boolean {
  return lat < SERVICE_AREA.south || lat > SERVICE_AREA.north
    || lon < SERVICE_AREA.west || lon > SERVICE_AREA.east
}

export function PointPicker({ lat, lon, onPick }: {
  lat: number; lon: number; onPick: (lat: number, lon: number) => void
}) {
  const holder = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const marker = useRef<maplibregl.Marker | null>(null)
  // Обработчик читает актуальный колбэк через ref: иначе на карте навсегда
  // останется замыкание первого рендера.
  const cb = useRef(onPick)
  cb.current = onPick

  const style = useMemo<maplibregl.StyleSpecification>(() => ({
    version: 8,
    sources: {
      osm: {
        type: 'raster', tileSize: 256,
        tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
        attribution: '© OpenStreetMap',
      },
    },
    layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
  }), [])

  useEffect(() => {
    if (!holder.current || map.current) return
    const m = new maplibregl.Map({
      container: holder.current, style, center: [lon, lat], zoom: 10.5,
      attributionControl: false,
    })
    map.current = m

    const el = document.createElement('div')
    el.className = 'pin'
    marker.current = new maplibregl.Marker({ element: el, draggable: true })
      .setLngLat([lon, lat]).addTo(m)
    marker.current.on('dragend', () => {
      const p = marker.current!.getLngLat()
      cb.current(round(p.lat), round(p.lng))
    })
    m.on('click', (e) => {
      marker.current?.setLngLat(e.lngLat)
      cb.current(round(e.lngLat.lat), round(e.lngLat.lng))
    })

    // Контейнер появляется вместе с модальным окном: без явного resize
    // карта остаётся в дефолтных 400×300.
    const ro = new ResizeObserver(() => m.resize())
    ro.observe(holder.current)
    return () => { ro.disconnect(); m.remove(); map.current = null }
  }, [])

  return <div ref={holder} className="picker" />
}

const round = (v: number) => Number(v.toFixed(6))

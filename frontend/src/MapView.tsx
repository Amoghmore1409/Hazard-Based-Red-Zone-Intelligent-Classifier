import { useEffect, useMemo, useRef } from 'react'
import * as maplibregl from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { MapboxOverlay } from '@deck.gl/mapbox'
import { ArcLayer, ScatterplotLayer } from '@deck.gl/layers'
import { H3HexagonLayer } from '@deck.gl/geo-layers'
import type { PickingInfo } from '@deck.gl/core'
import { type Aoi, type CellTable, type Habitation, type PlanRow, type Site, type Zone, PRIORITY_RGB, ZONE_RGB, fmt, ramp } from './api'

// The production build does not copy MapLibre's tile worker; let Vite bundle it and point MapLibre at it,
// otherwise basemap tiles never draw on the deployed site (deck.gl layers still do).
maplibregl.setWorkerUrl(workerUrl)

const STYLES = {
  streets: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
  satellite: {
    version: 8 as const,
    sources: { esri: { type: 'raster' as const, tileSize: 256, attribution: 'Esri, Maxar, Earthstar Geographics',
      tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'] } },
    layers: [{ id: 'esri', type: 'raster' as const, source: 'esri' }],
  },
}

export interface MapProps {
  aoi: Aoi | null
  cells: CellTable | null
  layer: string                      // 'zone' | 'mhi' | hazard key
  habs: Habitation[]
  sites: Site[]
  plan: PlanRow[]
  events: { name: string; lat: number; lon: number; type: string; date: string; deaths: number }[]
  highlight: string[]
  basemap: 'streets' | 'satellite'
  showSites: boolean
  showPlan: boolean
  stormCenter: [number, number] | null
  onCell: (h3: string) => void
  onHab: (id: string) => void
  onSite: (s: Site) => void
  onMapClick?: (lngLat: [number, number]) => void
}

export default function MapView(p: MapProps) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const overlay = useRef<MapboxOverlay | null>(null)
  const clickRef = useRef(p.onMapClick)
  clickRef.current = p.onMapClick

  useEffect(() => {
    const m = new maplibregl.Map({ container: el.current!, style: STYLES.streets, center: [79.4, 30.4], zoom: 8 })
    m.addControl(new maplibregl.NavigationControl(), 'top-right')
    m.addControl(new maplibregl.ScaleControl(), 'bottom-right')
    overlay.current = new MapboxOverlay({ interleaved: false, layers: [] })
    m.addControl(overlay.current)
    m.on('click', (e: maplibregl.MapMouseEvent) => clickRef.current?.([e.lngLat.lng, e.lngLat.lat]))
    map.current = m
    return () => m.remove()
  }, [])

  useEffect(() => { map.current?.setStyle(STYLES[p.basemap] as any) }, [p.basemap])

  useEffect(() => {
    if (p.aoi && map.current) {
      const [w, s, e, n] = p.aoi.bbox
      map.current.fitBounds([[w, s], [e, n]], { padding: 40, duration: 800 })
    }
  }, [p.aoi?.id])

  const col = useMemo(() => Object.fromEntries((p.cells?.columns ?? []).map((c, i) => [c, i])), [p.cells])

  const layers = useMemo(() => {
    const L: any[] = []
    if (p.cells) {
      const key = p.layer === 'zone' ? 'zone' : p.layer === 'mhi' ? 'mhi' : `hz_${p.layer}`
      const idx = col[key]
      L.push(new H3HexagonLayer({
        id: 'cells', data: p.cells.rows, getHexagon: (d: any) => d[0], pickable: true, stroked: false,
        extruded: false, opacity: 0.6, highPrecision: false,
        getFillColor: (d: any) => {
          if (idx === undefined) return [0, 0, 0, 0]
          if (key === 'zone') return [...ZONE_RGB[d[idx] as Zone], d[idx] === 'green' ? 90 : 210]
          const v = d[idx] as number
          return [...ramp(v), v < 0.15 ? 40 : 200]
        },
        updateTriggers: { getFillColor: [key, p.cells] },
      }))
      if (col.active_red !== undefined) {
        const active = p.cells.rows.filter((r) => r[col.active_red])
        L.push(new H3HexagonLayer({
          id: 'active', data: active, getHexagon: (d: any) => d[0], filled: true, stroked: true,
          getFillColor: [120, 0, 0, 230], getLineColor: [255, 255, 255, 200], lineWidthMinPixels: 0.5,
        }))
      }
    }
    if (p.showSites && p.sites.length) {
      const flat = p.sites.flatMap((s) => s.cells.map((c) => ({ c, s })))
      L.push(new H3HexagonLayer({
        id: 'sites', data: flat, getHexagon: (d: any) => d.c, pickable: true, filled: true, stroked: true,
        getFillColor: [33, 102, 172, 170], getLineColor: [255, 255, 255, 255], lineWidthMinPixels: 1,
        onClick: (i: PickingInfo) => { if (i.object) p.onSite((i.object as any).s) },
      }))
    }
    if (p.highlight.length) {
      L.push(new H3HexagonLayer({
        id: 'highlight', data: p.highlight, getHexagon: (d: any) => d, filled: false, stroked: true,
        getLineColor: [17, 24, 39, 255], lineWidthMinPixels: 2,
      }))
    }
    if (p.showPlan && p.plan.length) {
      L.push(new ArcLayer({
        id: 'plan', data: p.plan.filter((r) => r.site_lat != null),
        getSourcePosition: (d: PlanRow) => [d.lon, d.lat], getTargetPosition: (d: PlanRow) => [d.site_lon!, d.site_lat!],
        getSourceColor: (d: PlanRow) => [...PRIORITY_RGB[d.priority], 255], getTargetColor: [33, 102, 172, 255],
        getWidth: (d: PlanRow) => Math.max(1.5, Math.sqrt(d.persons) / 8), getHeight: 0.4,
      }))
    }
    L.push(new ScatterplotLayer({
      id: 'habs', data: p.habs, pickable: true, stroked: true, radiusUnits: 'pixels',
      getPosition: (d: Habitation) => [d.lon, d.lat],
      getRadius: (d: Habitation) => (d.priority === 'monitor' ? 2 : 3 + Math.min(9, Math.sqrt(d.pop) / 12)),
      getFillColor: (d: Habitation) => [...PRIORITY_RGB[d.priority], d.priority === 'monitor' ? 120 : 240],
      getLineColor: (d: Habitation) => (d.evacuate ? [255, 0, 0, 255] : [255, 255, 255, 220]),
      getLineWidth: (d: Habitation) => (d.evacuate ? 3 : 1), lineWidthUnits: 'pixels',
      onClick: (i: PickingInfo) => { if (i.object) p.onHab((i.object as Habitation).hab_id) },
      updateTriggers: { getLineColor: [p.habs], getFillColor: [p.habs] },
    }))
    L.push(new ScatterplotLayer({
      id: 'events', data: p.events, pickable: true, stroked: true, filled: true, radiusUnits: 'pixels',
      getPosition: (d: any) => [d.lon, d.lat], getRadius: 7, getFillColor: [0, 0, 0, 0],
      getLineColor: [17, 24, 39, 255], getLineWidth: 2.5, lineWidthUnits: 'pixels',
    }))
    if (p.stormCenter) {
      L.push(new ScatterplotLayer({
        id: 'storm', data: [p.stormCenter], getPosition: (d: any) => d, getRadius: 900, radiusUnits: 'meters',
        getFillColor: [37, 99, 235, 200], stroked: true, getLineColor: [255, 255, 255], lineWidthMinPixels: 2,
      }))
    }
    return L
  }, [p.cells, p.layer, p.habs, p.sites, p.plan, p.events, p.highlight, p.showSites, p.showPlan, p.stormCenter, col])

  useEffect(() => {
    overlay.current?.setProps({
      layers,
      onClick: (i: PickingInfo) => { if (i.layer?.id === 'cells' && i.object) p.onCell((i.object as any)[0]) },
      getTooltip: ({ object, layer }: PickingInfo) => {
        if (!object || !layer) return null
        if (layer.id === 'habs') {
          const h = object as Habitation
          return { html: `<b>${h.name}</b><br/>${h.district} · pop ${fmt(h.pop)}<br/>Priority: <b>${h.priority}</b>${h.evacuate ? '<br/><b style="color:#dc2626">EVACUATION ADVISORY</b>' : ''}` }
        }
        if (layer.id === 'sites') {
          const s = (object as any).s as Site
          return { html: `<b>Safe site ${s.site_id}</b><br/>Capacity ${fmt(s.capacity)} (limit: ${s.limiting_factor})<br/>Suitability ${s.suitability}` }
        }
        if (layer.id === 'events') {
          const e = object as any
          return { html: `<b>${e.name}</b><br/>${e.type} · ${e.date}${e.deaths ? ` · ${fmt(e.deaths)} deaths` : ''}` }
        }
        if (layer.id === 'cells') {
          const r = object as any[]
          return { html: `Zone <b>${r[col.zone]}</b> · MHI ${r[col.mhi]}${col.hz_dominant !== undefined ? `<br/>Dominant: ${r[col.hz_dominant]}` : ''}` }
        }
        return null
      },
    })
  }, [layers])

  // maplibre's own CSS forces position:relative on the map div, so size it via an absolute wrapper
  return <div className="absolute inset-0"><div ref={el} className="h-full w-full" /></div>
}

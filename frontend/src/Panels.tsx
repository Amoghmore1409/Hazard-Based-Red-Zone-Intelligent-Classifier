import { useEffect, useState } from 'react'
import { type Aoi, type Habitation, type PlanRow, type Priority, type SimResult, type Site, type Summary, type Validation, type Zone, type BriefingDoc,
  HAZARD_LABEL, PRIORITY_LABEL, PRIORITY_RGB, ZONE_RGB, api, fmt } from './api'

const rgb = (c: number[]) => `rgb(${c.join(',')})`
const PRIORITIES: Priority[] = ['immediate', 'short', 'medium', 'monitor']

export function Card({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>{right}
      </div>
      {children}
    </section>
  )
}

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="rounded-md bg-slate-50 p-2">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className={`text-lg font-bold ${tone ?? 'text-slate-900'}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  )
}

function ZoneBar({ zones }: { zones: Record<Zone, number> }) {
  const total = Object.values(zones).reduce((a, b) => a + b, 0) || 1
  return (
    <div>
      <div className="flex h-3 overflow-hidden rounded">
        {(['red', 'orange', 'yellow', 'green'] as Zone[]).map((z) => (
          <div key={z} style={{ width: `${(zones[z] / total) * 100}%`, background: rgb(ZONE_RGB[z]) }} />
        ))}
      </div>
      <div className="mt-1 grid grid-cols-4 text-[11px] text-slate-600">
        {(['red', 'orange', 'yellow', 'green'] as Zone[]).map((z) => (
          <span key={z}><b className="capitalize">{z}</b> {((zones[z] / total) * 100).toFixed(1)}%</span>
        ))}
      </div>
    </div>
  )
}

export function Overview({ s, aoi, onHab }: { s: Summary | null; aoi: Aoi | null; onHab: (name: string) => void }) {
  if (!s || !aoi) return <p className="p-3 text-sm text-slate-500">Loading…</p>
  return (
    <div className="space-y-3">
      <Card title="Red Zone status (permanent)"><ZoneBar zones={s.zones} />
        <div className="mt-2 grid grid-cols-2 gap-2">
          <Stat label="People living in Red Zones" value={fmt(s.population_in_red)} sub={`of ${fmt(s.population_total)} (WorldPop 2020)`} tone="text-red-700" />
          <Stat label="Active Red (live triggers)" value={fmt(s.active_red_cells)} sub="cells escalated by rain/alerts" tone="text-red-900" />
        </div>
      </Card>
      <Card title="Relocation priority">
        <div className="space-y-1">
          {PRIORITIES.map((p) => (
            <div key={p} className="flex items-center gap-2 text-sm">
              <span className="h-3 w-3 rounded-full" style={{ background: rgb(PRIORITY_RGB[p]) }} />
              <span className="flex-1">{PRIORITY_LABEL[p]}</span>
              <b>{fmt(s.counts[p] ?? 0)}</b>
              <span className="w-24 text-right text-xs text-slate-500">{fmt(s.people[p] ?? 0)} people</span>
            </div>
          ))}
        </div>
      </Card>
      {s.evacuation.habitations > 0 && (
        <div className="rounded-lg border-2 border-red-600 bg-red-50 p-3 text-sm text-red-900">
          <b>Evacuation advisory:</b> {s.evacuation.habitations} habitations ({fmt(s.evacuation.people)} people) are inside
          Active Red Zones under the current forecast/alerts.
        </div>
      )}
      <Card title="Live triggers" right={<span className="text-[11px] text-slate-400">{s.live.refreshed_at ?? s.live.status}</span>}>
        <div className="grid grid-cols-3 gap-2">
          <Stat label="Max 24 h rain (72 h fcst)" value={`${fmt(s.live.max_r24)} mm`} />
          <Stat label="Max hourly rain" value={`${fmt(s.live.max_r1, 1)} mm`} />
          <Stat label="Official alerts" value={fmt(s.live.alerts ?? 0)} sub="NDMA SACHET" />
        </div>
        {s.live.rain_error && <p className="mt-1 text-[11px] text-amber-700" title={s.live.rain_error}>Forecast unavailable: using static zones. <span className="text-slate-500">({s.live.rain_error.slice(0, 120)})</span></p>}
        {s.alerts.map((a, i) => <p key={i} className="mt-1 text-xs text-slate-700">⚠ {a.title}</p>)}
      </Card>
      <Card title="Safe sites & plan">
        <div className="grid grid-cols-3 gap-2">
          <Stat label="Safe sites" value={fmt(s.sites)} />
          <Stat label="Carrying capacity" value={fmt(s.site_capacity)} sub="persons" />
          <Stat label="Placed by plan" value={fmt(s.plan_assigned)} sub={`${fmt(s.plan_unassigned)} unplaced`} />
        </div>
      </Card>
      {aoi.model && (
        <Card title="Model validation">
          <p className="text-sm">Landslide XGBoost · spatial 5-fold CV AUC <b>{aoi.model.auc_spatial_cv}</b> on {fmt(aoi.model.positives)} GSI-mapped landslide cells.</p>
          <p className="mt-1 text-xs text-slate-500">Top drivers: {aoi.model.importance.slice(0, 5).map((x) => x[0]).join(', ')}</p>
        </Card>
      )}
      <Card title="Top immediate cases">
        {(s as any).immediate_habitations?.map((h: any) => (
          <button key={h.name} onClick={() => onHab(h.name)} className="flex w-full justify-between py-0.5 text-left text-sm hover:bg-slate-50">
            <span>{h.name} <span className="text-xs text-slate-400">{h.district}</span></span>
            <span className="text-xs text-slate-500">{fmt(h.pop)} · {HAZARD_LABEL[h.dominant_hazard] ?? h.dominant_hazard}</span>
          </button>
        ))}
      </Card>
    </div>
  )
}

export function HabList({ habs, onSelect }: { habs: Habitation[]; onSelect: (id: string) => void }) {
  const [filter, setFilter] = useState<Priority | 'all'>('immediate')
  const [q, setQ] = useState('')
  const list = habs.filter((h) => (filter === 'all' || h.priority === filter) && h.name.toLowerCase().includes(q.toLowerCase()))
  const download = () => {
    const cols = ['hab_id', 'name', 'district', 'pop', 'priority', 'risk', 'svi', 'frac_red', 'dominant_hazard', 'lat', 'lon']
    const csv = [cols.join(','), ...list.map((h: any) => cols.map((c) => JSON.stringify(h[c] ?? '')).join(','))].join('\n')
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    a.download = `habitations_${filter}.csv`
    a.click()
  }
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1">
        {(['immediate', 'short', 'medium', 'monitor', 'all'] as const).map((p) => (
          <button key={p} onClick={() => setFilter(p)}
            className={`rounded-full px-2 py-0.5 text-xs ${filter === p ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-700'}`}>
            {p === 'all' ? 'All' : PRIORITY_LABEL[p].split(' (')[0]} ({p === 'all' ? habs.length : habs.filter((h) => h.priority === p).length})
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search habitation…"
          className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm" />
        <button onClick={download} className="rounded border border-slate-300 px-2 text-xs">CSV</button>
      </div>
      <div className="space-y-1">
        {list.slice(0, 300).map((h) => (
          <button key={h.hab_id} onClick={() => onSelect(h.hab_id)}
            className="w-full rounded border border-slate-200 bg-white p-2 text-left hover:border-slate-400">
            <div className="flex items-center justify-between">
              <span className="font-medium text-sm">{h.name}{h.evacuate && <span className="ml-1 rounded bg-red-600 px-1 text-[10px] text-white">EVACUATE</span>}</span>
              <span className="text-xs text-slate-500">risk {h.risk.toFixed(2)}</span>
            </div>
            <div className="text-xs text-slate-500">{h.district} · {fmt(h.pop)} people · {HAZARD_LABEL[h.dominant_hazard] ?? h.dominant_hazard}</div>
            {h.reasons[0] && <div className="mt-0.5 text-xs text-slate-600">{h.reasons[0]}</div>}
          </button>
        ))}
      </div>
    </div>
  )
}

export function HabDetail({ aoi, id, onClose, onSites }: { aoi: string; id: string; onClose: () => void; onSites: (s: Site[], cells: string[]) => void }) {
  const [d, setD] = useState<Awaited<ReturnType<typeof api.habitation>> | null>(null)
  useEffect(() => {
    setD(null)
    api.habitation(aoi, id).then((x) => { setD(x); onSites(x.recommended_sites, x.cells) })
  }, [aoi, id])
  if (!d) return <p className="p-3 text-sm">Loading…</p>
  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-bold">{d.name}</h2>
          <p className="text-xs text-slate-500">{d.district} · {d.place} · {d.lat.toFixed(4)}, {d.lon.toFixed(4)}</p>
        </div>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-700">✕</button>
      </div>
      <div className="rounded-md p-2 text-sm font-semibold text-white" style={{ background: rgb(PRIORITY_RGB[d.priority]) }}>
        {PRIORITY_LABEL[d.priority]}{d.evacuate && ' · EVACUATION ADVISORY (live)'}
      </div>
      <div className="grid grid-cols-3 gap-2">
        <Stat label="Population" value={fmt(d.pop)} />
        <Stat label="Risk score" value={d.risk.toFixed(2)} />
        <Stat label="Vulnerability" value={d.svi.toFixed(2)} />
      </div>
      <Card title="Why (explanation)">
        <ul className="list-disc space-y-0.5 pl-4 text-sm">{d.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
        <p className="mt-2 text-xs text-slate-500">Footprint cells: {Object.entries(d.zone_breakdown).map(([z, n]) => `${n} ${z}`).join(' · ')}</p>
      </Card>
      <CensusCard d={d as any} />
      {d.assigned && (
        <Card title="Assigned in relocation plan">
          {d.assigned.site_id ? <p className="text-sm">→ <b>{d.assigned.site_id}</b> · {d.assigned.distance_km} km by road · {fmt(d.assigned.persons)} persons</p>
            : <p className="text-sm text-red-700">No site with spare capacity within 60 km by road. Needs state-level land allotment.</p>}
        </Card>
      )}
      <Card title="Recommended safe sites">
        {d.recommended_sites.length === 0 && <p className="text-sm text-slate-500">No single site within 60 km by road can absorb the whole community.</p>}
        {d.recommended_sites.map((s) => <SiteRow key={s.site_id} s={s} />)}
      </Card>
    </div>
  )
}

function Gauge({ label, v, max, limit }: { label: string; v: number; max: number; limit: boolean }) {
  return (
    <div className="text-[11px]">
      <div className="flex justify-between"><span className={limit ? 'font-bold text-amber-700' : ''}>{label}</span><span>{fmt(v)}</span></div>
      <div className="h-1.5 rounded bg-slate-100"><div className={`h-1.5 rounded ${limit ? 'bg-amber-500' : 'bg-blue-500'}`} style={{ width: `${Math.min(100, (v / max) * 100)}%` }} /></div>
    </div>
  )
}

export function SiteRow({ s }: { s: Site }) {
  const max = Math.max(s.cap_land, s.cap_water, s.cap_services)
  return (
    <div className="mb-2 rounded border border-slate-200 p-2">
      <div className="flex justify-between text-sm">
        <b>{s.site_id}</b>
        <span className="text-xs text-slate-500">{s.distance_km != null ? `${s.distance_km.toFixed(1)} km${(s as any).by_road ? ' by road' : ''} · ` : ''}suitability {s.suitability}</span>
      </div>
      <div className="text-xs text-slate-600">{s.district} · {s.usable_ha.toFixed(0)} ha usable · slope {s.slope.toFixed(0)}° · road {fmt(s.dist_road)} m · health {fmt(s.dist_health / 1000, 1)} km</div>
      <div className="mt-1 text-sm">Carrying capacity <b>{fmt(s.capacity)}</b> persons{s.allocated ? ` · ${fmt(s.allocated)} allocated` : ''}</div>
      <div className="mt-1 grid grid-cols-3 gap-2">
        <Gauge label="Land" v={s.cap_land} max={max} limit={s.limiting_factor === 'land'} />
        <Gauge label="Water 55 LPCD" v={s.cap_water} max={max} limit={s.limiting_factor === 'water'} />
        <Gauge label="Services" v={s.cap_services} max={max} limit={s.limiting_factor === 'services'} />
      </div>
    </div>
  )
}

export function PlanPanel({ aoi, plan, sites, onChanged, canEdit }: { aoi: string; plan: { rows: PlanRow[]; assigned_people: number; unassigned_people: number; scenario: string } | null; sites: Site[]; onChanged: () => void; canEdit: boolean }) {
  const [prio, setPrio] = useState<string[]>(['immediate', 'short'])
  const [busy, setBusy] = useState(false)
  const run = async () => { setBusy(true); try { await api.optimize(aoi, prio); onChanged() } finally { setBusy(false) } }
  return (
    <div className="space-y-3">
      <Card title="Optimise relocation (MILP)">
        <p className="mb-2 text-xs text-slate-600">Assigns whole habitations to safe sites, minimising person-km <b>by road</b> (OSM network, max 60 km) and respecting carrying capacity. Immediate cases are served first; communities are not split.</p>
        <div className="mb-2 flex gap-3 text-sm">
          {(['immediate', 'short', 'medium'] as Priority[]).map((p) => (
            <label key={p} className="flex items-center gap-1">
              <input type="checkbox" checked={prio.includes(p)} onChange={(e) => setPrio(e.target.checked ? [...prio, p] : prio.filter((x) => x !== p))} />
              {PRIORITY_LABEL[p].split(' (')[0]}
            </label>
          ))}
        </div>
        {canEdit ? (
          <button disabled={busy} onClick={run} className="w-full rounded bg-blue-700 py-1.5 text-sm font-semibold text-white disabled:opacity-50">
            {busy ? 'Optimising…' : 'Run allocation'}
          </button>
        ) : <p className="text-xs text-slate-500">Read-only access: SDMA officers can re-run the allocation.</p>}
        {plan && <p className="mt-2 text-sm">Plan <b>{plan.scenario}</b>: {fmt(plan.assigned_people)} placed, <span className="text-red-700">{fmt(plan.unassigned_people)} without capacity</span></p>}
      </Card>
      <Card title={`Allocations (${plan?.rows.length ?? 0})`}>
        <div className="max-h-64 space-y-0.5 overflow-auto text-xs">
          {plan?.rows.map((r) => (
            <div key={r.hab_id} className="flex justify-between border-b border-slate-100 py-0.5">
              <span><span className="mr-1 inline-block h-2 w-2 rounded-full" style={{ background: rgb(PRIORITY_RGB[r.priority]) }} />{r.name}</span>
              <span className={r.site_id ? '' : 'text-red-700'}>{r.site_id ? `${r.site_id} · ${r.distance_km} km by road` : 'unplaced'} · {fmt(r.persons)}</span>
            </div>
          ))}
        </div>
      </Card>
      <Card title={`Top safe sites (${sites.length})`}>
        <div className="max-h-96 overflow-auto">{sites.slice(0, 25).map((s) => <SiteRow key={s.site_id} s={s} />)}</div>
      </Card>
    </div>
  )
}

export function WhatIf({ aoi, picking, setPicking, center, setCenter, onResult }: {
  aoi: Aoi; picking: boolean; setPicking: (b: boolean) => void; center: [number, number] | null; setCenter: (c: [number, number] | null) => void; onResult: (r: SimResult | null) => void
}) {
  const [rain24, setRain24] = useState(150)
  const [rain1, setRain1] = useState(aoi.id === 'uk' ? 100 : 30)
  const [radius, setRadius] = useState(15)
  const [weights, setWeights] = useState(aoi.weights)
  const [alerts, setAlerts] = useState<Record<string, number>>({})
  const [res, setRes] = useState<SimResult | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { setWeights(aoi.weights); setRes(null); onResult(null) }, [aoi.id])
  const run = async () => {
    setBusy(true)
    try {
      const r = await api.simulate(aoi.id, { rain24, rain1, radius_km: radius, weights, alerts,
        ...(center ? { lon: center[0], lat: center[1] } : {}) })
      setRes(r); onResult(r)
    } finally { setBusy(false) }
  }
  const presets: { label: string; r24: number; r1: number; c: [number, number] | null; a: Record<string, number> }[] = aoi.id === 'uk'
    ? [{ label: 'Cloudburst over Chamoli', r24: 180, r1: 110, c: [79.56, 30.55] as [number, number], a: { cloudburst: 1.3 } },
       { label: 'Monsoon extreme (district-wide)', r24: 200, r1: 40, c: null, a: { landslide: 1.15 } }]
    : [{ label: 'Severe cyclone landfall', r24: 250, r1: 50, c: null, a: { surge: 1.4, coastal: 1.25, flood: 1.2 } },
       { label: 'Brahmani–Baitarani flood', r24: 300, r1: 30, c: [86.45, 20.55], a: { flood: 1.5 } }]
  return (
    <div className="space-y-3">
      <Card title="Scenario presets">
        <div className="flex flex-wrap gap-1">
          {presets.map((p) => (
            <button key={p.label} className="rounded bg-slate-100 px-2 py-1 text-xs hover:bg-slate-200"
              onClick={() => { setRain24(p.r24); setRain1(p.r1); setAlerts(p.a); setCenter(p.c) }}>{p.label}</button>
          ))}
        </div>
      </Card>
      <Card title="Rainfall trigger">
        <label className="block text-xs">24 h rainfall: <b>{rain24} mm</b></label>
        <input type="range" min={0} max={400} value={rain24} onChange={(e) => setRain24(+e.target.value)} className="w-full" />
        <label className="block text-xs">Peak hourly rainfall: <b>{rain1} mm</b> {rain1 >= 100 && <span className="text-red-700">(cloudburst)</span>}</label>
        <input type="range" min={0} max={200} value={rain1} onChange={(e) => setRain1(+e.target.value)} className="w-full" />
        <div className="mt-1 flex items-center gap-2 text-xs">
          <button onClick={() => setPicking(!picking)} className={`rounded px-2 py-1 ${picking ? 'bg-blue-700 text-white' : 'bg-slate-100'}`}>
            {picking ? 'Click map to place storm…' : center ? 'Move storm centre' : 'Place storm centre'}
          </button>
          {center && <button onClick={() => setCenter(null)} className="text-slate-500 underline">whole region</button>}
        </div>
        {center && (<><label className="mt-1 block text-xs">Storm radius: <b>{radius} km</b></label>
          <input type="range" min={3} max={60} value={radius} onChange={(e) => setRadius(+e.target.value)} className="w-full" /></>)}
      </Card>
      <Card title="Hazard weights (AHP, editable)">
        {Object.entries(weights).map(([h, w]) => (
          <div key={h}>
            <label className="block text-xs">{HAZARD_LABEL[h]}: <b>{w.toFixed(2)}</b></label>
            <input type="range" min={0} max={1} step={0.05} value={w} onChange={(e) => setWeights({ ...weights, [h]: +e.target.value })} className="w-full" />
          </div>
        ))}
      </Card>
      <button disabled={busy} onClick={run} className="w-full rounded bg-red-700 py-2 text-sm font-semibold text-white disabled:opacity-50">
        {busy ? 'Simulating…' : 'Run what-if'}
      </button>
      {res && (
        <Card title="Scenario impact" right={<button className="text-xs underline" onClick={() => { setRes(null); onResult(null) }}>clear</button>}>
          <ZoneBar zones={res.zones} />
          <p className="mt-2 text-sm"><b className="text-red-800">{fmt(res.active_red_cells)}</b> cells newly Red · <b>{fmt(res.affected_habitations)}</b> habitations / <b>{fmt(res.affected_people)}</b> people need evacuation.</p>
          <div className="mt-1 max-h-40 overflow-auto text-xs">{res.affected.map((a) => <div key={a.hab_id}>{a.name} · {a.district} · {fmt(a.pop)}</div>)}</div>
        </Card>
      )}
    </div>
  )
}

export function AlertsPanel({ aoi }: { aoi: string }) {
  const [d, setD] = useState<Awaited<ReturnType<typeof api.alerts>> | null>(null)
  const [reports, setReports] = useState<any[]>([])
  useEffect(() => { api.alerts(aoi).then(setD); api.fieldReports(aoi).then(setReports) }, [aoi])
  if (!d) return <p className="text-sm">Loading…</p>
  return (
    <div className="space-y-3">
      <Card title="Official alerts (NDMA SACHET)">
        {d.alerts.length === 0 && <p className="text-xs text-slate-500">No active alerts mention this region.</p>}
        {d.alerts.map((a) => <div key={a.id} className="mb-1 text-xs"><b>{a.title}</b><br />{a.body}</div>)}
      </Card>
      <Card title="Notifications sent">
        {d.notifications.length === 0 && <p className="text-xs text-slate-500">None yet.</p>}
        {d.notifications.map((n) => (
          <div key={n.id} className="mb-1 border-b border-slate-100 pb-1 text-xs">
            <span className="rounded bg-slate-100 px-1">{n.channel}</span> {n.message}
            <div className="text-[10px] text-slate-400">{n.created_at} · {n.status}</div>
          </div>
        ))}
      </Card>
      <Card title={`Field reports (${reports.length})`} right={<a href="/field" target="_blank" className="text-xs text-blue-700 underline">open field app</a>}>
        {reports.map((r) => (
          <div key={r.id} className="mb-1 flex gap-2 text-xs">
            {r.photo && <img src={`/uploads/${r.photo}`} className="h-10 w-10 rounded object-cover" />}
            <div><b>{r.kind}</b> · {r.note}<div className="text-[10px] text-slate-400">{r.created_at} · {r.reporter}</div></div>
          </div>
        ))}
      </Card>
      <Card title="Refresh history">
        {d.snapshots.map((s) => <div key={s.id} className="text-[11px]">{s.run_at} · {s.source} · red {s.red} · active {s.active_red} · max24h {s.max_r24?.toFixed?.(0)} mm</div>)}
      </Card>
    </div>
  )
}

const WHEN_HI: Record<string, string> = { 'Next 24 h': 'अगले 24 घंटे', '24-72 h': '24–72 घंटे', 'This month': 'इस माह' }
const TONE = { red: 'border-red-200 bg-red-50 text-red-800', amber: 'border-amber-200 bg-amber-50 text-amber-800', blue: 'border-blue-200 bg-blue-50 text-blue-800' }
const T = {
  English: { title: 'SDMA Situation Briefing', situation: 'Situation', imm: 'Habitations for immediate relocation', name: 'Habitation', district: 'District', people: 'People', hazard: 'Main hazard', sites: 'Relocation sites & plan', weather: 'Weather & official alerts', actions: 'Recommended actions', loading: 'Preparing briefing from live platform data…', none: 'No habitation is currently in the Immediate category.' },
  Hindi: { title: 'एसडीएमए स्थिति ब्रीफ़िंग', situation: 'स्थिति', imm: 'तत्काल पुनर्वास हेतु बस्तियाँ', name: 'बस्ती', district: 'ज़िला', people: 'लोग', hazard: 'मुख्य ख़तरा', sites: 'पुनर्वास स्थल एवं योजना', weather: 'मौसम एवं आधिकारिक चेतावनियाँ', actions: 'अनुशंसित कार्रवाई', loading: 'लाइव डेटा से ब्रीफ़िंग तैयार हो रही है…', none: 'इस समय कोई बस्ती तत्काल श्रेणी में नहीं है।' },
} as const

function Section({ title, items }: { title: string; items: string[] }) {
  if (!items?.length) return null
  return (
    <section>
      <h3 className="mb-1.5 text-xs font-bold uppercase tracking-wide text-slate-500">{title}</h3>
      <ul className="space-y-1.5">
        {items.map((t, i) => <li key={i} className="flex gap-2 text-sm leading-relaxed text-slate-800"><span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" />{t}</li>)}
      </ul>
    </section>
  )
}

export function Briefing({ aoi, onClose }: { aoi: string; onClose: () => void }) {
  const [lang, setLang] = useState<'English' | 'Hindi'>('English')
  const [b, setB] = useState<BriefingDoc | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => { setB(null); setErr(''); api.briefing(aoi, lang).then(setB).catch((e) => setErr(String(e))) }, [aoi, lang])
  const t = T[lang]
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="max-h-[90vh] w-full max-w-3xl overflow-auto rounded-xl bg-white shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="sticky top-0 flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
          <div className="flex gap-1">
            {(['English', 'Hindi'] as const).map((l) => (
              <button key={l} onClick={() => setLang(l)} className={`rounded-full px-3 py-1 text-xs ${lang === l ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-700'}`}>{l === 'Hindi' ? 'हिन्दी' : l}</button>
            ))}
          </div>
          <button onClick={onClose} className="px-2 text-slate-500 hover:text-slate-900">✕</button>
        </div>
        <div className="space-y-5 px-6 py-5">
          {err && <p className="text-sm text-red-700">Could not load briefing: {err}</p>}
          {!b && !err && <p className="py-10 text-center text-sm text-slate-500">{t.loading}</p>}
          {b && (
            <>
              <header>
                <p className="text-xs font-semibold uppercase tracking-wide text-red-700">{t.title}</p>
                <h2 className="text-xl font-bold text-slate-900">{b.region}</h2>
                <p className="text-xs text-slate-500">{new Date().toLocaleString(lang === 'Hindi' ? 'hi-IN' : 'en-IN', { dateStyle: 'long', timeStyle: 'short' })}</p>
                <p className="mt-3 rounded-lg border-l-4 border-red-600 bg-slate-50 px-4 py-3 text-base font-semibold leading-snug text-slate-900">{b.headline}</p>
              </header>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {b.key_figures.map((k) => (
                  <div key={k.label} className={`rounded-lg border p-3 ${TONE[k.tone]}`}>
                    <div className="text-[11px] leading-tight opacity-80">{k.label}</div>
                    <div className="mt-1 text-lg font-bold leading-tight">{k.value}</div>
                    {k.sub && <div className="text-[11px] opacity-80">{k.sub}</div>}
                  </div>
                ))}
              </div>
              <Section title={t.situation} items={b.situation} />
              <section>
                <h3 className="mb-1.5 text-xs font-bold uppercase tracking-wide text-slate-500">{t.imm}</h3>
                {b.immediate.length === 0 ? <p className="text-sm text-slate-600">{t.none}</p> : (
                  <table className="w-full text-sm">
                    <thead className="bg-slate-900 text-left text-xs text-white">
                      <tr><th className="px-3 py-1.5">{t.name}</th><th className="px-3 py-1.5">{t.district}</th><th className="px-3 py-1.5 text-right">{t.people}</th><th className="px-3 py-1.5">{t.hazard}</th></tr>
                    </thead>
                    <tbody>
                      {b.immediate.map((h) => (
                        <tr key={h.name} className="border-b border-slate-100 even:bg-slate-50">
                          <td className="px-3 py-1.5 font-medium">{h.name}</td><td className="px-3 py-1.5">{h.district}</td>
                          <td className="px-3 py-1.5 text-right tabular-nums">{h.people}</td><td className="px-3 py-1.5">{h.hazard}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>
              <Section title={t.sites} items={b.sites} />
              <Section title={t.weather} items={b.weather} />
              <section>
                <h3 className="mb-1.5 text-xs font-bold uppercase tracking-wide text-slate-500">{t.actions}</h3>
                <ol className="space-y-2">
                  {b.actions.map((a, i) => (
                    <li key={i} className="flex gap-3 text-sm">
                      {a.when && <span className="w-24 shrink-0 rounded bg-red-700 px-2 py-0.5 text-center text-xs font-semibold text-white">{lang === 'Hindi' ? WHEN_HI[a.when] ?? a.when : a.when}</span>}
                      <span className="leading-relaxed text-slate-800">{a.action}</span>
                    </li>
                  ))}
                </ol>
              </section>
              <p className="border-t border-slate-100 pt-2 text-[11px] text-slate-400">Source: {b.source}. Figures and tables come directly from SURAKSHA data; AI only writes the sentences. Decision support only: verify on the ground.</p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

const pct = (v: number) => `${(v * 100).toFixed(v < 0.01 ? 1 : 0)}%`

function ZoneChip({ z }: { z: Zone | null }) {
  if (!z) return <span className="rounded bg-slate-100 px-1.5 text-[11px] text-slate-500">outside</span>
  return <span className="rounded px-1.5 text-[11px] font-semibold capitalize" style={{ background: rgb(ZONE_RGB[z]), color: z === 'red' ? '#fff' : '#111827' }}>{z}</span>
}

function Compare({ label, hit, base, baseLabel }: { label: string; hit: number; base: number; baseLabel: string }) {
  return (
    <div className="space-y-1 text-xs">
      <div className="flex justify-between"><span>{label}</span><b>{pct(hit)}</b></div>
      <div className="h-2 rounded bg-slate-100"><div className="h-2 rounded bg-red-600" style={{ width: pct(Math.min(1, hit)) }} /></div>
      <div className="flex justify-between text-slate-500"><span>{baseLabel}</span><span>{pct(base)}</span></div>
      <div className="h-2 rounded bg-slate-100"><div className="h-2 rounded bg-slate-400" style={{ width: pct(Math.min(1, base)) }} /></div>
    </div>
  )
}

export function ValidationPanel({ aoi }: { aoi: Aoi }) {
  const [v, setV] = useState<Validation | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => { setV(null); setErr(''); api.validation(aoi.id).then(setV).catch((e) => setErr(String(e))) }, [aoi.id])
  if (err) return <p className="text-sm text-red-700">Validation not available: {err}</p>
  if (!v) return <p className="text-sm text-slate-500">Loading…</p>
  const pointRate = v.events_point_scored ? v.events_point_hit / v.events_point_scored : 0
  return (
    <div className="space-y-3">
      <Card title="Would we have flagged past disasters?">
        <p className="mb-2 text-xs text-slate-600">
          Model-only zones: the event-history rule is switched off, so past disasters cannot mark themselves Red.
        </p>
        {v.events_point_scored > 0 ? (
          <>
            <p className="mb-2 text-sm"><b className="text-red-700">{v.events_point_hit} of {v.events_point_scored}</b> modelled disaster sites lie in a Red/Orange zone at the exact location.</p>
            <Compare label="Disaster sites in Red/Orange" hit={pointRate} base={v.surveyed_share_hot} baseLabel="Chance: share of inhabited land that is Red/Orange" />
          </>
        ) : <p className="text-sm text-amber-700">No event in this region has coordinates precise enough to score at the exact location.</p>}
        {v.events_scored > 0 && v.baseline_hit_rate != null && (
          <p className="mt-2 text-[11px] text-slate-500">Looser test: {v.events_hit}/{v.events_scored} have Red/Orange within 1–2 km, but {pct(v.baseline_hit_rate)} of random sites do too, so this alone proves little.</p>
        )}
      </Card>
      {v.gsi && (
        <Card title={`GSI landslide inventory (${fmt(v.gsi.landslide_cells)} cells)`}>
          <Compare label="Mapped landslides in Red/Orange" hit={v.gsi.share_in_hot} base={v.gsi.surveyed_share_hot} baseLabel="Other surveyed land in Red/Orange" />
          <p className="mt-2 text-sm"><b>{v.gsi.lift_hot}×</b> more likely Red/Orange · <b>{v.gsi.lift_red}×</b> more likely Red ({pct(v.gsi.share_in_red)} vs {pct(v.gsi.surveyed_share_red)}).</p>
          <p className="mt-1 text-[11px] text-slate-500">Compared within surveyed land (≤ 1.5 km of roads), because GSI did not map remote slopes, so missing landslides there are unknown, not safe. Landslide scores here are out-of-fold (spatial CV) predictions.</p>
        </Card>
      )}
      <Card title="Event-by-event">
        <div className="space-y-1.5">
          {v.events.map((e) => (
            <div key={e.name} className="border-b border-slate-100 pb-1.5 text-xs">
              <div className="flex items-start justify-between gap-2">
                <span className="font-medium">{e.name}</span>
                <span className="shrink-0">{e.hit_point === true ? '✅' : e.hit_point === false ? '❌' : '—'}</span>
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-1 text-slate-500">
                <span>{e.date.slice(0, 4)} · {e.type}{e.deaths ? ` · ${fmt(e.deaths)} deaths` : ''}</span>
                <span>· at site</span><ZoneChip z={e.zone_at_point} />
                <span>· within {e.radius_km} km</span><ZoneChip z={e.zone_nearby} />
              </div>
              {!e.modelled && <div className="text-[11px] text-amber-700">{e.type} is not a modelled hazard: handled by field reports / alerts.</div>}
              {e.modelled && !e.in_aoi && <div className="text-[11px] text-amber-700">Approximate location falls outside mapped land (offshore or outside district).</div>}
              {e.precision !== 'site' && e.in_aoi && <div className="text-[11px] text-slate-400">Location precision: {e.precision}</div>}
            </div>
          ))}
        </div>
      </Card>
      <p className="text-[11px] text-slate-500">{fmt(v.pop_share_hot * 100, 1)}% of residents and {fmt(v.area_share_hot * 100, 1)}% of land are in model-only Red/Orange zones.</p>
    </div>
  )
}

const CENSUS_ROWS: [string, string][] = [
  ['pct_kutcha', 'Kutcha / non-permanent houses'], ['pct_dilapidated', 'Dilapidated houses'],
  ['pct_scst', 'SC / ST population'], ['pct_illiterate', 'Illiterate'], ['pct_child', 'Children 0–6'],
  ['pct_marginal_workers', 'Marginal workers'], ['pct_no_assets', 'Households with no assets'],
  ['pct_no_electricity', 'No electricity'], ['pct_open_defecation', 'Open defecation'],
]
const SVI_DIMS: [string, string][] = [['svi_housing', 'Housing'], ['svi_social', 'Social'], ['svi_economic', 'Economic'],
  ['svi_services', 'Services'], ['svi_access', 'Access']]

function CensusCard({ d }: { d: Record<string, any> }) {
  if (d.pct_kutcha == null) return null
  const exact = String(d.census_source ?? '').startsWith('village')
  return (
    <Card title="Vulnerability: Census 2011" right={<span className={`rounded px-1.5 text-[10px] ${exact ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}`}>{d.census_source}</span>}>
      {d.census_village && <p className="mb-1 text-xs text-slate-500">Census village: <b>{d.census_village}</b></p>}
      <div className="mb-2 grid grid-cols-5 gap-1">
        {SVI_DIMS.map(([k, label]) => (
          <div key={k} className="text-center">
            <div className="mx-auto h-10 w-3 overflow-hidden rounded bg-slate-100"><div className="w-3 rounded bg-red-600" style={{ height: `${(d[k] ?? 0) * 100}%`, marginTop: `${(1 - (d[k] ?? 0)) * 100}%` }} /></div>
            <div className="text-[10px] text-slate-600">{label}</div>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px]">
        {CENSUS_ROWS.map(([k, label]) => (
          <div key={k} className="flex justify-between border-b border-slate-100"><span className="text-slate-600">{label}</span><b>{((d[k] ?? 0) * 100).toFixed(0)}%</b></div>
        ))}
      </div>
      <p className="mt-1 text-[10px] text-slate-400">Bars: percentile vs other habitations in this region (higher = more vulnerable). Source: Census of India 2011 PCA + Houselisting HH-14.</p>
    </Card>
  )
}

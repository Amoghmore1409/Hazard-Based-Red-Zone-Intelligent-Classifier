import { useCallback, useEffect, useState } from 'react'
import MapView from './MapView'
import { AlertsPanel, Briefing, HabDetail, HabList, Overview, PlanPanel, ValidationPanel, WhatIf } from './Panels'
import Field from './Field'
import Login from './Login'
import UsersPanel from './Users'
import { type Aoi, type CellTable, type Habitation, type SimResult, type Site, type Summary,
  type User, HAZARD_LABEL, PRIORITY_LABEL, PRIORITY_RGB, ZONE_RGB, api, can, downloadPdf, session, users } from './api'

type Tab = 'overview' | 'habitations' | 'plan' | 'whatif' | 'validation' | 'alerts'
const TABS: [Tab, string][] = [['overview', 'Overview'], ['habitations', 'Habitations'], ['plan', 'Sites & Plan'], ['whatif', 'What-if'], ['validation', 'Validation'], ['alerts', 'Alerts']]

export default function App() {
  const [, rerender] = useState(0)
  const s = session.get()
  if (!s) return <Login onLogin={() => rerender((n) => n + 1)} />
  if (location.pathname.startsWith('/field') || !can.dashboard(s.user)) return <Field user={s.user} />
  return <Dashboard user={s.user} />
}

export function logout() { session.clear(); location.reload() }

function Dashboard({ user }: { user: User }) {
  const editor = can.edit(user)
  const [aois, setAois] = useState<Aoi[]>([])
  const [aoiId, setAoiId] = useState(user.aoi ?? 'uk')
  const [mode, setMode] = useState<'static' | 'live'>('static')
  const [layer, setLayer] = useState('zone')
  const [basemap, setBasemap] = useState<'streets' | 'satellite'>('streets')
  const [tab, setTab] = useState<Tab>('overview')
  const [cells, setCells] = useState<CellTable | null>(null)
  const [sim, setSim] = useState<SimResult | null>(null)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [habs, setHabs] = useState<Habitation[]>([])
  const [sites, setSites] = useState<Site[]>([])
  const [plan, setPlan] = useState<Awaited<ReturnType<typeof api.plans>> | null>(null)
  const [events, setEvents] = useState<any[]>([])
  const [selHab, setSelHab] = useState<string | null>(null)
  const [highlight, setHighlight] = useState<string[]>([])
  const [recSites, setRecSites] = useState<Site[] | null>(null)
  const [cellInfo, setCellInfo] = useState<Record<string, any> | null>(null)
  const [picking, setPicking] = useState(false)
  const [storm, setStorm] = useState<[number, number] | null>(null)
  const [brief, setBrief] = useState(false)
  const [showUsers, setShowUsers] = useState(false)
  const [pendingUsers, setPendingUsers] = useState(0)
  useEffect(() => { if (user.role === 'admin') users.list().then((l) => setPendingUsers(l.filter((u) => u.status === 'pending').length)).catch(() => {}) }, [])
  const [busy, setBusy] = useState('')
  const aoi = aois.find((a) => a.id === aoiId) ?? null

  useEffect(() => { api.aois().then(setAois) }, [])
  const loadAll = useCallback(() => {
    api.summary(aoiId).then(setSummary)
    api.habitations(aoiId).then(setHabs)
    api.sites(aoiId).then(setSites)
    api.plans(aoiId).then(setPlan)
  }, [aoiId])
  useEffect(() => {
    setSelHab(null); setHighlight([]); setRecSites(null); setCellInfo(null); setSim(null); setStorm(null); setLayer('zone')
    api.events(aoiId).then(setEvents)
    loadAll()
  }, [aoiId])
  useEffect(() => { setCells(null); api.cells(aoiId, mode).then(setCells) }, [aoiId, mode])

  const refresh = async () => {
    setBusy('Fetching live rainfall & alerts…')
    try { await api.refresh(aoiId); setMode('live'); api.cells(aoiId, 'live').then(setCells); loadAll() } finally { setBusy('') }
  }
  const openHab = (id: string) => { setSelHab(id); setTab('habitations'); setCellInfo(null) }
  const openHabByName = (name: string) => { const h = habs.find((x) => x.name === name); if (h) openHab(h.hab_id) }

  const shownCells = sim ?? cells
  const shownSites = recSites ?? sites
  const layerOptions = ['zone', 'mhi', ...(aoi?.hazards ?? [])]

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-3 bg-slate-900 px-4 py-2 text-white">
        <div className="mr-2">
          <div className="text-lg font-black tracking-wide">SURAKSHA</div>
          <div className="text-[10px] text-slate-400">Multi-Hazard Red Zone & Relocation DSS · NDRF / DM Division</div>
        </div>
        <select value={aoiId} onChange={(e) => setAoiId(e.target.value)} className="rounded bg-slate-800 px-2 py-1 text-sm">
          {aois.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <div className="flex overflow-hidden rounded border border-slate-700 text-xs">
          {(['static', 'live'] as const).map((m) => (
            <button key={m} onClick={() => { setMode(m); setSim(null) }} className={`px-2 py-1 ${mode === m && !sim ? 'bg-red-700' : ''}`}>
              {m === 'static' ? 'Permanent Red Zones' : 'Live (rain + alerts)'}
            </button>
          ))}
        </div>
        <select value={layer} onChange={(e) => setLayer(e.target.value)} className="rounded bg-slate-800 px-2 py-1 text-xs">
          {layerOptions.map((l) => <option key={l} value={l}>{l === 'zone' ? 'Zones' : l === 'mhi' ? 'Multi-hazard index' : `${HAZARD_LABEL[l]} hazard`}</option>)}
        </select>
        <button onClick={() => setBasemap(basemap === 'streets' ? 'satellite' : 'streets')} className="rounded bg-slate-800 px-2 py-1 text-xs">
          {basemap === 'streets' ? 'Satellite' : 'Streets'}
        </button>
        <div className="ml-auto flex gap-2 text-xs">
          {editor && <button onClick={refresh} className="rounded bg-slate-700 px-3 py-1.5 hover:bg-slate-600">⟳ Refresh live</button>}
          <button onClick={() => setBrief(true)} className="rounded bg-indigo-600 px-3 py-1.5 hover:bg-indigo-500">AI Briefing</button>
          <button onClick={() => { setBusy('Generating PDF report…'); downloadPdf(aoiId).catch((e) => alert(e.message)).finally(() => setBusy('')) }}
            className="rounded bg-emerald-600 px-3 py-1.5 hover:bg-emerald-500">PDF Report</button>
          {editor && <a href="/field" target="_blank" className="rounded bg-slate-700 px-3 py-1.5 hover:bg-slate-600">Field app</a>}
          {user.role === 'admin' && (
            <button onClick={() => setShowUsers(true)} className="relative rounded bg-slate-700 px-3 py-1.5 hover:bg-slate-600">
              Users{pendingUsers > 0 && <span className="absolute -right-1.5 -top-1.5 rounded-full bg-amber-500 px-1.5 text-[10px] font-bold text-slate-900">{pendingUsers}</span>}
            </button>
          )}
          <div className="flex items-center gap-2 border-l border-slate-700 pl-2">
            <div className="text-right leading-tight"><div className="text-[11px]">{user.name}</div>
              <div className="text-[10px] uppercase text-slate-400">{user.role}{user.aoi ? ` · ${user.aoi}` : ''}</div></div>
            <button onClick={logout} className="rounded bg-slate-800 px-2 py-1.5 hover:bg-slate-700">Sign out</button>
          </div>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <aside className="flex max-h-[50vh] w-full flex-col border-r border-slate-200 bg-slate-50 md:max-h-none md:w-[400px]">
          <nav className="flex border-b border-slate-200 bg-white text-xs">
            {TABS.map(([t, l]) => (
              <button key={t} onClick={() => { setTab(t); if (t !== 'habitations') { setSelHab(null); setRecSites(null); setHighlight([]) } }}
                className={`flex-1 py-2 ${tab === t ? 'border-b-2 border-red-700 font-semibold text-red-800' : 'text-slate-600'}`}>{l}</button>
            ))}
          </nav>
          <div className="flex-1 overflow-auto p-3">
            {tab === 'overview' && <Overview s={summary} aoi={aoi} onHab={openHabByName} />}
            {tab === 'habitations' && (selHab
              ? <HabDetail aoi={aoiId} id={selHab} onClose={() => { setSelHab(null); setRecSites(null); setHighlight([]) }}
                  onSites={(s, c) => { setRecSites(s); setHighlight(c) }} />
              : <HabList habs={habs} onSelect={openHab} />)}
            {tab === 'plan' && <PlanPanel aoi={aoiId} plan={plan} sites={sites} onChanged={loadAll} canEdit={editor} />}
            {tab === 'whatif' && aoi && <WhatIf aoi={aoi} picking={picking} setPicking={setPicking} center={storm} setCenter={setStorm} onResult={setSim} />}
            {tab === 'validation' && aoi && <ValidationPanel aoi={aoi} />}
            {tab === 'alerts' && <AlertsPanel aoi={aoiId} />}
          </div>
        </aside>

        <main className="relative min-h-[50vh] flex-1">
          <MapView aoi={aoi} cells={shownCells} layer={sim ? 'zone' : layer} habs={habs} sites={shownSites}
            plan={plan?.rows ?? []} events={events} highlight={highlight} basemap={basemap}
            showSites={tab === 'plan' || !!recSites} showPlan={tab === 'plan'} stormCenter={tab === 'whatif' ? storm : null}
            onCell={(h) => { if (!picking) api.cell(aoiId, h).then(setCellInfo) }} onHab={openHab} onSite={() => setTab('plan')}
            onMapClick={picking ? (ll) => { setStorm(ll); setPicking(false) } : undefined} />
          {!shownCells && <div className="absolute left-1/2 top-4 z-10 -translate-x-1/2 rounded bg-white px-3 py-1 text-sm shadow">Loading hazard grid…</div>}
          {busy && <div className="absolute left-1/2 top-4 z-10 -translate-x-1/2 rounded bg-slate-900 px-3 py-1 text-sm text-white shadow">{busy}</div>}
          {sim && <div className="absolute left-1/2 top-4 z-10 -translate-x-1/2 rounded bg-red-700 px-3 py-1 text-sm font-semibold text-white shadow">WHAT-IF SCENARIO: not live data</div>}
          <Legend layer={sim ? 'zone' : layer} />
          {cellInfo && <CellCard c={cellInfo} hazards={aoi?.hazards ?? []} onClose={() => setCellInfo(null)} />}
        </main>
      </div>
      {brief && <Briefing aoi={aoiId} onClose={() => setBrief(false)} />}
      {showUsers && <UsersPanel onClose={() => setShowUsers(false)} onChange={setPendingUsers} />}
    </div>
  )
}

function Legend({ layer }: { layer: string }) {
  return (
    <div className="absolute bottom-8 left-3 z-10 rounded bg-white/95 p-2 text-[11px] shadow">
      {layer === 'zone' ? (['red', 'orange', 'yellow', 'green'] as const).map((z) => (
        <div key={z} className="flex items-center gap-1"><span className="h-3 w-3" style={{ background: `rgb(${ZONE_RGB[z]})` }} />
          {{ red: 'Red: unsuitable for habitation', orange: 'Orange: high hazard', yellow: 'Yellow: moderate', green: 'Green: safe' }[z]}</div>
      )) : <div><div className="h-2 w-32 bg-gradient-to-r from-[#ffffcc] via-[#f03b20] to-[#800026]" /><div className="flex justify-between"><span>0</span><span>hazard</span><span>1</span></div></div>}
      <div className="mt-1 flex items-center gap-1"><span className="h-3 w-3" style={{ background: 'rgb(120,0,0)' }} />Active Red (live trigger)</div>
      <div className="mt-1 border-t pt-1">Habitations by priority:</div>
      {(Object.keys(PRIORITY_LABEL) as (keyof typeof PRIORITY_LABEL)[]).map((p) => (
        <div key={p} className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full" style={{ background: `rgb(${PRIORITY_RGB[p]})` }} />{PRIORITY_LABEL[p]}</div>
      ))}
      <div className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full border-2 border-slate-900" />Recorded disaster event</div>
      <div className="flex items-center gap-1"><span className="h-2.5 w-2.5 bg-blue-700" />Safe relocation site</div>
    </div>
  )
}

function CellCard({ c, hazards, onClose }: { c: Record<string, any>; hazards: string[]; onClose: () => void }) {
  return (
    <div className="absolute right-3 top-14 z-10 w-72 rounded-lg bg-white p-3 text-sm shadow-lg">
      <div className="flex justify-between"><b>Hazard cell</b><button onClick={onClose}>✕</button></div>
      <p className="text-[11px] text-slate-500">{c.h3} · {c.district}</p>
      <p className="mt-1">Zone <b className="capitalize">{c.zone}</b> · MHI {c.mhi?.toFixed(2)}{c.live_zone !== c.zone && <> · live <b className="capitalize text-red-800">{c.live_zone}</b></>}</p>
      {hazards.map((h) => (
        <div key={h} className="mt-1">
          <div className="flex justify-between text-xs"><span>{HAZARD_LABEL[h]}</span><span>{c[`hz_${h}`]?.toFixed(2)}</span></div>
          <div className="h-1.5 rounded bg-slate-100"><div className="h-1.5 rounded bg-red-600" style={{ width: `${(c[`hz_${h}`] ?? 0) * 100}%` }} /></div>
        </div>
      ))}
      {c.factors?.length > 0 && <p className="mt-2 text-xs"><b>Landslide drivers (SHAP):</b> {c.factors.join(', ')}</p>}
      <p className="mt-1 text-xs text-slate-600">Elev {c.elev?.toFixed(0)} m · slope {c.slope?.toFixed(0)}° · pop {c.pop?.toFixed(0)} · {c.hist_500m} events nearby</p>
      {(c.r24 > 0 || c.r1 > 0) && <p className="text-xs text-slate-600">Forecast: {c.r24.toFixed(0)} mm/24 h, peak {c.r1.toFixed(0)} mm/h</p>}
    </div>
  )
}

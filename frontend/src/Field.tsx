import { useState } from 'react'
import { type User, authFetch, session } from './api'

const KINDS = ['Ground cracks', 'Land subsidence', 'Water seepage / springs', 'Tilted trees / poles', 'Rockfall',
  'Coastal erosion', 'Embankment breach', 'Waterlogging']

export default function Field({ user }: { user: User }) {
  const [aoi, setAoi] = useState(user.aoi ?? 'uk')
  const [kind, setKind] = useState(KINDS[0])
  const [note, setNote] = useState('')
  const [reporter, setReporter] = useState('')
  const [pos, setPos] = useState<{ lat: number; lon: number } | null>(null)
  const [photo, setPhoto] = useState<File | null>(null)
  const [msg, setMsg] = useState('')

  const locate = () => navigator.geolocation.getCurrentPosition(
    (p) => setPos({ lat: p.coords.latitude, lon: p.coords.longitude }),
    (e) => setMsg(`Location error: ${e.message}. Enter coordinates manually.`), { enableHighAccuracy: true })

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!pos) return setMsg('Location is required.')
    const fd = new FormData()
    Object.entries({ aoi, kind, note, reporter, lat: String(pos.lat), lon: String(pos.lon) }).forEach(([k, v]) => fd.append(k, v))
    if (photo) fd.append('photo', photo)
    setMsg('Submitting…')
    const r = await authFetch('/api/field-reports', { method: 'POST', body: fd })
    const j = await r.json()
    setMsg(r.ok ? `Report submitted.${j.upgraded?.length ? ` Priority upgraded to IMMEDIATE: ${j.upgraded.join(', ')}` : ''}` : `Error: ${j.detail}`)
    if (r.ok) { setNote(''); setPhoto(null) }
  }

  return (
    <div className="min-h-full bg-slate-100 p-4">
      <form onSubmit={submit} className="mx-auto max-w-md space-y-3 rounded-lg bg-white p-4 shadow">
        <div className="flex items-start justify-between">
          <h1 className="text-xl font-black">SURAKSHA · Field Report</h1>
          <button type="button" onClick={() => { session.clear(); location.reload() }} className="text-xs text-slate-500 underline">Sign out</button>
        </div>
        <p className="text-xs text-slate-600">Signed in as <b>{user.name}</b></p>
        <p className="text-xs text-slate-500">Report ground distress signs. Reports within 1.5 km raise a habitation's relocation priority for review.</p>
        <label className="block text-sm">Region
          <select value={aoi} disabled={!!user.aoi} onChange={(e) => setAoi(e.target.value)} className="mt-1 w-full rounded border p-2">
            <option value="uk">Uttarakhand – Chamoli & Rudraprayag</option><option value="od">Odisha – Kendrapara</option>
          </select></label>
        <label className="block text-sm">Observation
          <select value={kind} onChange={(e) => setKind(e.target.value)} className="mt-1 w-full rounded border p-2">
            {KINDS.map((k) => <option key={k}>{k}</option>)}
          </select></label>
        <div className="text-sm">Location
          <div className="mt-1 flex gap-2">
            <button type="button" onClick={locate} className="rounded bg-slate-900 px-3 py-2 text-white">Use GPS</button>
            <input placeholder="lat" value={pos?.lat ?? ''} onChange={(e) => setPos({ lat: +e.target.value, lon: pos?.lon ?? 0 })} className="w-24 rounded border p-2" inputMode="decimal" />
            <input placeholder="lon" value={pos?.lon ?? ''} onChange={(e) => setPos({ lat: pos?.lat ?? 0, lon: +e.target.value })} className="w-24 rounded border p-2" inputMode="decimal" />
          </div></div>
        <label className="block text-sm">Photo
          <input type="file" accept="image/*" capture="environment" onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} className="mt-1 w-full text-sm" /></label>
        <label className="block text-sm">Notes
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} className="mt-1 w-full rounded border p-2" /></label>
        <label className="block text-sm">Reporter (name / designation)
          <input value={reporter} onChange={(e) => setReporter(e.target.value)} className="mt-1 w-full rounded border p-2" /></label>
        <button className="w-full rounded bg-red-700 py-2 font-semibold text-white">Submit report</button>
        {msg && <p className="text-sm">{msg}</p>}
      </form>
    </div>
  )
}

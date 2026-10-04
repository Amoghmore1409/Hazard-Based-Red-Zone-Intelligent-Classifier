import { useEffect, useState } from 'react'
import { type AppUser, users } from './api'

export default function UsersPanel({ onClose, onChange }: { onClose: () => void; onChange: (pending: number) => void }) {
  const [list, setList] = useState<AppUser[] | null>(null)
  const [err, setErr] = useState('')
  const load = () => users.list().then((l) => { setList(l); onChange(l.filter((u) => u.status === 'pending').length) }).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  const decide = async (u: string, ok: boolean) => {
    if (!ok && !confirm(`Reject the access request from ${u}?`)) return
    try { await users.decide(u, ok); load() } catch (e) { setErr((e as Error).message) }
  }
  const pending = list?.filter((u) => u.status === 'pending') ?? []
  const others = list?.filter((u) => u.status !== 'pending') ?? []
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="max-h-[85vh] w-full max-w-3xl overflow-auto rounded-xl bg-white p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-bold">User access</h2>
          <button onClick={onClose} className="px-2 text-slate-500 hover:text-slate-900">✕</button>
        </div>
        {err && <p className="mb-2 text-sm text-red-700">{err}</p>}
        {!list ? <p className="text-sm text-slate-500">Loading…</p> : (
          <>
            <h3 className="mb-1 text-xs font-bold uppercase tracking-wide text-slate-500">Pending viewer requests ({pending.length})</h3>
            {pending.length === 0 && <p className="mb-3 text-sm text-slate-500">No pending requests.</p>}
            <div className="mb-4 space-y-2">
              {pending.map((u) => (
                <div key={u.username} className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <b>{u.name}</b> <span className="text-slate-500">@{u.username}</span>
                      <div className="text-xs text-slate-600">{u.organisation} · {u.email}</div>
                      {u.reason && <div className="mt-1 text-xs italic text-slate-600">“{u.reason}”</div>}
                      <div className="text-[11px] text-slate-400">Requested {new Date(u.created_at).toLocaleString('en-IN')}</div>
                    </div>
                    <div className="flex gap-2">
                      <button onClick={() => decide(u.username, true)} className="rounded bg-emerald-600 px-3 py-1 text-xs font-semibold text-white">Approve</button>
                      <button onClick={() => decide(u.username, false)} className="rounded bg-slate-200 px-3 py-1 text-xs font-semibold text-slate-800">Reject</button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
            <h3 className="mb-1 text-xs font-bold uppercase tracking-wide text-slate-500">All accounts</h3>
            <table className="w-full text-xs">
              <thead className="bg-slate-900 text-left text-white"><tr><th className="px-2 py-1">User</th><th className="px-2 py-1">Role</th><th className="px-2 py-1">Region</th><th className="px-2 py-1">Status</th><th className="px-2 py-1">Organisation</th></tr></thead>
              <tbody>
                {others.map((u) => (
                  <tr key={u.username} className="border-b border-slate-100">
                    <td className="px-2 py-1"><b>{u.name}</b> <span className="text-slate-400">@{u.username}</span></td>
                    <td className="px-2 py-1 uppercase">{u.role}</td><td className="px-2 py-1">{u.aoi ?? 'all'}</td>
                    <td className={`px-2 py-1 ${u.status === 'rejected' ? 'text-red-700' : 'text-emerald-700'}`}>{u.status}{u.decided_by ? ` by ${u.decided_by}` : ''}</td>
                    <td className="px-2 py-1">{u.organisation ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-3 text-[11px] text-slate-400">SDMA, field and admin accounts are created by an administrator: <code>python -m backend.app.auth add …</code></p>
          </>
        )}
      </div>
    </div>
  )
}

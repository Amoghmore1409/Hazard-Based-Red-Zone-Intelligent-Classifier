import { useState } from 'react'
import { login, registerViewer } from './api'

const input = 'mt-1 w-full rounded border border-slate-300 p-2'

export default function Login({ onLogin }: { onLogin: () => void }) {
  const [mode, setMode] = useState<'login' | 'register' | 'sent'>('login')
  return (
    <div className="flex min-h-full items-center justify-center bg-slate-900 p-4">
      <div className="w-full max-w-sm space-y-4 rounded-xl bg-white p-6 shadow-2xl">
        <div>
          <div className="text-2xl font-black tracking-wide text-slate-900">SURAKSHA</div>
          <div className="text-xs text-slate-500">Multi-Hazard Red Zone & Relocation DSS · NDRF / DM Division</div>
        </div>
        {mode === 'login' && <SignIn onLogin={onLogin} onRegister={() => setMode('register')} />}
        {mode === 'register' && <Register onDone={() => setMode('sent')} onBack={() => setMode('login')} />}
        {mode === 'sent' && (
          <div className="space-y-3">
            <p className="rounded bg-emerald-50 p-3 text-sm text-emerald-800">
              Request submitted. An NDRF administrator will review it; you can sign in once it is approved.
            </p>
            <button onClick={() => setMode('login')} className="w-full rounded bg-slate-900 py-2 text-sm font-semibold text-white">Back to sign in</button>
          </div>
        )}
      </div>
    </div>
  )
}

function SignIn({ onLogin, onRegister }: { onLogin: () => void; onRegister: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setErr('')
    try { await login(username, password); onLogin() } catch (x) { setErr((x as Error).message) } finally { setBusy(false) }
  }
  return (
    <form onSubmit={submit} className="space-y-4">
      <label className="block text-sm">Username
        <input autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} className={input} required />
      </label>
      <label className="block text-sm">Password
        <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} className={input} required />
      </label>
      {err && <p className="text-sm text-red-700" role="alert">{err}</p>}
      <button disabled={busy} className="w-full rounded bg-red-700 py-2 font-semibold text-white disabled:opacity-50">
        {busy ? 'Signing in…' : 'Sign in'}
      </button>
      <p className="text-center text-sm text-slate-600">
        No account? <button type="button" onClick={onRegister} className="font-semibold text-red-700 underline">Request viewer access</button>
      </p>
      <p className="text-[11px] text-slate-400">Authorised NDRF / SDMA personnel only. Activity is logged.</p>
    </form>
  )
}

function Register({ onDone, onBack }: { onDone: () => void; onBack: () => void }) {
  const [f, setF] = useState({ name: '', email: '', organisation: '', username: '', password: '', confirm: '', reason: '' })
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value })
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (f.password !== f.confirm) return setErr('Passwords do not match')
    setBusy(true); setErr('')
    try {
      await registerViewer({ username: f.username, name: f.name, password: f.password, email: f.email, organisation: f.organisation, reason: f.reason })
      onDone()
    } catch (x) { setErr((x as Error).message) } finally { setBusy(false) }
  }
  return (
    <form onSubmit={submit} className="space-y-3">
      <div>
        <h2 className="font-bold text-slate-900">Request viewer access</h2>
        <p className="text-xs text-slate-500">Viewers get read-only access to Red Zones, priorities, plans and what-if scenarios. Requests are approved by an NDRF administrator.</p>
      </div>
      <label className="block text-sm">Full name<input value={f.name} onChange={set('name')} className={input} required autoComplete="name" /></label>
      <label className="block text-sm">Official email<input type="email" value={f.email} onChange={set('email')} className={input} required autoComplete="email" /></label>
      <label className="block text-sm">Organisation / department<input value={f.organisation} onChange={set('organisation')} className={input} required placeholder="e.g. District Collectorate, Chamoli" /></label>
      <label className="block text-sm">Username<input value={f.username} onChange={set('username')} className={input} required autoComplete="username"
        pattern="[a-z0-9][a-z0-9._\-]{2,31}" title="3–32 lowercase letters, digits, '.', '_' or '-'" /></label>
      <label className="block text-sm">Password<input type="password" value={f.password} onChange={set('password')} className={input} required minLength={10} autoComplete="new-password" />
        <span className="text-[11px] text-slate-500">At least 10 characters, with an uppercase letter and a digit.</span></label>
      <label className="block text-sm">Confirm password<input type="password" value={f.confirm} onChange={set('confirm')} className={input} required autoComplete="new-password" /></label>
      <label className="block text-sm">Why do you need access? <span className="text-slate-400">(optional)</span>
        <textarea value={f.reason} onChange={set('reason')} rows={2} className={input} maxLength={500} /></label>
      {err && <p className="text-sm text-red-700" role="alert">{err}</p>}
      <button disabled={busy} className="w-full rounded bg-red-700 py-2 font-semibold text-white disabled:opacity-50">{busy ? 'Submitting…' : 'Submit request'}</button>
      <button type="button" onClick={onBack} className="w-full text-sm text-slate-600 underline">Back to sign in</button>
    </form>
  )
}

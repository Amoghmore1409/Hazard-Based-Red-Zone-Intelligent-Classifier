export type Zone = 'red' | 'orange' | 'yellow' | 'green'
export type Priority = 'immediate' | 'short' | 'medium' | 'monitor'

export interface Aoi {
  id: string; name: string; state: string; districts: string[]; hazards: string[]
  weights: Record<string, number>; center: [number, number]; bbox: [number, number, number, number]
  cells: number; model: { auc_spatial_cv: number; positives: number; importance: [string, number][] } | null
}
export interface CellTable { columns: string[]; rows: (string | number | boolean)[][] }
export interface Habitation {
  hab_id: string; name: string; place: string; district: string; lat: number; lon: number; pop: number
  priority: Priority; risk: number; mhi: number; svi: number; frac_red: number; frac_orange: number
  dominant_hazard: string; dist_health: number; dist_road: number; hist: number; nearest_event: string
  nearest_event_km: number; field_reports: number; evacuate: boolean; reasons: string[]
}
export interface Site {
  site_id: string; district: string; lat: number; lon: number; area_ha: number; usable_ha: number; slope: number
  capacity: number; cap_land: number; cap_water: number; cap_services: number; limiting_factor: string
  suitability: number; dist_road: number; dist_health: number; dist_river: number; cells: string[]
  allocated?: number; distance_km?: number
}
export interface PlanRow {
  hab_id: string; site_id: string | null; persons: number; distance_km: number | null; priority: Priority
  name: string; lat: number; lon: number; site_lat: number | null; site_lon: number | null
}
export interface Summary {
  region: string; zones: Record<Zone, number>; live_zones: Record<Zone, number>; active_red_cells: number
  population_total: number; population_in_red: number; habitations: number
  counts: Partial<Record<Priority, number>>; people: Partial<Record<Priority, number>>
  evacuation: { habitations: number; people: number }; sites: number; site_capacity: number
  plan_assigned: number; plan_unassigned: number
  live: { max_r24: number; max_r1: number; refreshed_at?: string; rain_error?: string; alerts?: number; status?: string }
  alerts: { title: string; source: string; issued_at: string }[]
}
export interface ValidationEvent {
  name: string; type: string; date: string; deaths: number; precision: string; radius_km: number
  modelled: boolean; in_aoi: boolean; zone_at_point: Zone | null; zone_nearby: Zone | null
  hit: boolean | null; hit_point: boolean | null; hazard_score: number | null; baseline: number
}
export interface Validation {
  method: string; events: ValidationEvent[]; events_point_scored: number; events_point_hit: number
  surveyed_share_hot: number; area_share_hot: number; events_scored: number; events_hit: number
  baseline_hit_rate: number | null; pop_share_hot: number
  gsi?: { landslide_cells: number; share_in_hot: number; surveyed_share_hot: number; lift_hot: number
    share_in_red: number; surveyed_share_red: number; lift_red: number }
}
export interface BriefingDoc {
  source: string; lang: string; region: string; headline: string
  key_figures: { label: string; value: string; sub?: string; tone: 'red' | 'amber' | 'blue' }[]
  situation: string[]; sites: string[]; weather: string[]
  actions: { when: string; action: string }[]
  immediate: { name: string; district: string; people: string; hazard: string }[]
}
export interface SimResult extends CellTable {
  zones: Record<Zone, number>; active_red_cells: number; affected_people: number; affected_habitations: number
  affected: { hab_id: string; name: string; district: string; pop: number }[]
}

export type Role = 'admin' | 'sdma' | 'viewer' | 'field'
export interface User { username: string; name: string; role: Role; aoi: string | null }
const KEY = 'suraksha_session'
export const session = {
  get(): { token: string; user: User } | null {
    try { return JSON.parse(localStorage.getItem(KEY) ?? 'null') } catch { return null }
  },
  set(v: { token: string; user: User }) { try { localStorage.setItem(KEY, JSON.stringify(v)) } catch { /* private mode */ } },
  clear() { try { localStorage.removeItem(KEY) } catch { /* ignore */ } },
}
export const can = {
  edit: (u: User | null) => u?.role === 'admin' || u?.role === 'sdma',
  dashboard: (u: User | null) => !!u && u.role !== 'field',
}

/** fetch with the session token; a 401 ends the session and returns to the login screen. */
export async function authFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const s = session.get()
  const headers = new Headers(init.headers)
  if (s) headers.set('Authorization', `Bearer ${s.token}`)
  const r = await fetch(url, { ...init, headers })
  if (r.status === 401 && s) { session.clear(); location.reload() }
  return r
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await authFetch(url, init)
  if (!r.ok) {
    const body = await r.text()
    let msg = body
    try { msg = JSON.parse(body).detail ?? body } catch { /* not json */ }
    throw new Error(msg)
  }
  return r.json()
}

export async function login(username: string, password: string): Promise<User> {
  const r = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }) })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(j.detail ?? 'Login failed')
  session.set(j)
  return j.user
}

export async function downloadPdf(aoi: string) {
  const r = await authFetch(`/api/report/${aoi}.pdf`)
  if (!r.ok) throw new Error(`Report failed (${r.status})`)
  const a = document.createElement('a')
  a.href = URL.createObjectURL(await r.blob())
  a.download = `SURAKSHA_${aoi}_report.pdf`
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 5000)
}
const post = <T,>(url: string, body: unknown) =>
  req<T>(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const api = {
  aois: () => req<Aoi[]>('/api/aois'),
  cells: (aoi: string, mode: 'static' | 'live') => req<CellTable>(`/api/cells/${aoi}?mode=${mode}`),
  cell: (aoi: string, h3: string) => req<Record<string, any>>(`/api/cell/${aoi}/${h3}`),
  summary: (aoi: string) => req<Summary>(`/api/summary/${aoi}`),
  habitations: (aoi: string) => req<Habitation[]>(`/api/habitations/${aoi}`),
  habitation: (aoi: string, id: string) => req<Habitation & { recommended_sites: Site[]; assigned: PlanRow | null; zone_breakdown: Record<string, number>; cells: string[] }>(`/api/habitations/${aoi}/${id}`),
  sites: (aoi: string) => req<Site[]>(`/api/sites/${aoi}`),
  plans: (aoi: string) => req<{ rows: PlanRow[]; assigned_people: number; unassigned_people: number; scenario: string }>(`/api/plans/${aoi}`),
  optimize: (aoi: string, priorities: string[]) => post<any>(`/api/plans/${aoi}/optimize`, { priorities }),
  simulate: (aoi: string, body: object) => post<SimResult>(`/api/simulate/${aoi}`, body),
  refresh: (aoi: string) => post<any>(`/api/refresh/${aoi}`, {}),
  alerts: (aoi: string) => req<{ alerts: any[]; notifications: any[]; snapshots: any[]; live: any }>(`/api/alerts/${aoi}`),
  validation: (aoi: string) => req<Validation>(`/api/validation/${aoi}`),
  events: (aoi: string) => req<{ name: string; type: string; date: string; lat: number; lon: number; deaths: number }[]>(`/api/events/${aoi}`),
  fieldReports: (aoi: string) => req<any[]>(`/api/field-reports/${aoi}`),
  briefing: (aoi: string, lang: string) => req<BriefingDoc>(`/api/briefing/${aoi}?lang=${lang}`),
}

export const ZONE_RGB: Record<Zone, [number, number, number]> = {
  red: [215, 48, 31], orange: [252, 141, 89], yellow: [253, 212, 158], green: [102, 189, 99],
}
export const PRIORITY_RGB: Record<Priority, [number, number, number]> = {
  immediate: [127, 0, 0], short: [230, 85, 13], medium: [253, 174, 107], monitor: [148, 163, 184],
}
export const PRIORITY_LABEL: Record<Priority, string> = {
  immediate: 'Immediate (0–3 mo)', short: 'Short-term (3–12 mo)', medium: 'Medium-term (1–3 yr)', monitor: 'Monitor',
}
export const HAZARD_LABEL: Record<string, string> = {
  landslide: 'Landslide', flood: 'Flood', cloudburst: 'Cloudburst', coastal: 'Coastal erosion', surge: 'Storm surge',
}
export const fmt = (n: number | undefined | null, d = 0) =>
  n == null || Number.isNaN(n) ? '–' : n.toLocaleString('en-IN', { maximumFractionDigits: d })

/** 0–1 -> yellow→red ramp */
export function ramp(v: number): [number, number, number] {
  const t = Math.max(0, Math.min(1, v))
  const stops: [number, number, number][] = [[255, 255, 204], [254, 178, 76], [240, 59, 32], [128, 0, 38]]
  const x = t * (stops.length - 1), i = Math.min(stops.length - 2, Math.floor(x)), f = x - i
  return stops[i].map((c, k) => Math.round(c + (stops[i + 1][k] - c) * f)) as [number, number, number]
}

export interface AppUser {
  username: string; name: string; role: Role; aoi: string | null; status: 'active' | 'pending' | 'rejected'
  email: string | null; organisation: string | null; reason: string | null; created_at: string
  decided_by: string | null; decided_at: string | null
}

export async function registerViewer(body: { username: string; name: string; password: string; email: string; organisation: string; reason: string }) {
  const r = await fetch('/api/auth/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : 'Request failed')
  return j
}

export const users = {
  list: () => req<AppUser[]>('/api/auth/users'),
  decide: (username: string, approve: boolean) =>
    req<{ ok: boolean }>(`/api/auth/users/${encodeURIComponent(username)}/${approve ? 'approve' : 'reject'}`, { method: 'POST' }),
}

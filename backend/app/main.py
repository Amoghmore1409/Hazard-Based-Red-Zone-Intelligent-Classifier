"""SURAKSHA API: Red Zones, habitation priorities, safe sites, allocation, live triggers, reports."""
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text

from . import feeds
from .allocate import MAX_ROAD_KM, allocate
from .auth import authorize, decide, list_users, login, register
from .briefing import briefing
from .db import SCHEMA, engine
from .engine import WEIGHTS, apply_triggers, classify
from .notify import notify
from .priority import prioritise

ROOT = Path(__file__).resolve().parents[2]
UPLOADS = ROOT / "data" / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)
AOI_META = {
    "uk": {"name": "Uttarakhand – Chamoli & Rudraprayag", "state": "Uttarakhand", "districts": ["Chamoli", "Rudraprayag"],
           "hazards": ["landslide", "flood", "cloudburst"]},
    "od": {"name": "Odisha – Kendrapara", "state": "Odisha", "districts": ["Kendrapara"],
           "hazards": ["flood", "coastal", "surge"]},
}
S: dict = {}  # in-memory state per AOI: cells, live, habs, sites, plan, live_meta, evac

app = FastAPI(title="SURAKSHA – Multi-Hazard Red Zone DSS", version="1.1", dependencies=[Depends(authorize)])
# The deployed dashboard is served from this same origin, so CORS is only needed for the Vite dev server.
CORS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=CORS, allow_methods=["*"], allow_headers=["*"])
app.mount("/uploads", StaticFiles(directory=UPLOADS), name="uploads")


def _q(sql, **kw):
    return pd.read_sql(text(sql), engine, params=kw)


CELL_KEEP = ["h3", "lat", "lon", "pop", "hist_500m", "zone", "mhi", "hz_max", "hz_dominant",
             "hz_landslide", "hz_flood", "hz_cloudburst", "hz_coastal", "hz_surge"]


def _compact(df: pd.DataFrame) -> pd.DataFrame:
    """float64 -> float32, repeated strings -> category: ~3x less memory (fits a 512 MB free host)."""
    for c in df.columns:
        if df[c].dtype == "float64":
            df[c] = df[c].astype("float32")
        elif df[c].dtype == object or str(df[c].dtype) in ("str", "string"):
            if df[c].nunique() < len(df) / 2:
                df[c] = df[c].astype("category")
    return df


def _no_geom(table: str, aoi: str, h3: str | None = None) -> pd.DataFrame:
    """All columns except the geometry (the API never needs it; skipping it halves start-up transfer)."""
    cols = _q("SELECT column_name FROM information_schema.columns WHERE table_name = :t AND table_schema = 'public' "
              "AND column_name <> 'geometry' ORDER BY ordinal_position", t=table).column_name
    where = "aoi = :a" + (" AND h3 = :h" if h3 else "")
    return _q(f"SELECT {', '.join(f'{chr(34)}{c}{chr(34)}' for c in cols)} FROM {table} WHERE {where}", a=aoi, h=h3)


def load_state():
    with engine.begin() as con:
        con.execute(text(SCHEMA))
    for aoi in AOI_META:
        cells = _q(f"SELECT {', '.join(CELL_KEEP)} FROM h3_cells WHERE aoi = :a",
                   a=aoi).dropna(axis=1, how="all").pipe(_compact).set_index("h3")
        habs = _no_geom("habitations", aoi)
        habs["reasons"] = habs.reasons.map(json.loads)
        habs["cells"] = habs.cells.map(json.loads)
        sites = _no_geom("candidate_sites", aoi)
        sites["cells"] = sites.cells.map(json.loads)
        plan = _q("SELECT * FROM relocation_plans WHERE aoi = :a", a=aoi)
        try:
            road = _compact(_q("SELECT hab_id, site_id, road_km FROM road_distances WHERE aoi = :a", a=aoi))
        except Exception:  # table not built yet (run pipeline/p05b_roads.py + p06_load.py)
            road = None
        S[aoi] = {"cells": cells, "live": cells.copy(), "habs": habs, "sites": sites, "plan": plan, "road": road,
                  "live_meta": {"status": "not refreshed yet"}, "evac": set()}
        apply_field_reports(aoi, notify_upgrades=False)


def refresh(aoi: str, source="scheduler"):
    st = S[aoi]
    cells = st["cells"]
    meta = {"refreshed_at": datetime.now().isoformat(timespec="seconds"), "source": source}
    try:
        rain, rmeta = feeds.rainfall(cells)
        meta.update(rmeta)
    except Exception as e:
        rain = None
        meta["rain_error"] = str(e)[:200]
    try:
        alerts = feeds.sachet_alerts(AOI_META[aoi]["districts"], AOI_META[aoi]["state"])
    except Exception as e:
        alerts = []
        meta["alerts_error"] = str(e)[:200]
    meta["alerts"] = len(alerts)
    live = classify(apply_triggers(cells, aoi, rain, feeds.merge_multipliers(alerts)), WEIGHTS[aoi])
    live["active_red"] = (live.zone == "red") & (cells.zone != "red")
    st["live"], st["live_meta"] = live, meta
    _store_alerts(aoi, alerts)
    _update_evacuation(aoi)
    zc = live.zone.value_counts()
    with engine.begin() as con:
        con.execute(text("INSERT INTO zone_snapshots(aoi, source, red, orange, yellow, green, active_red, max_r24, max_r1) "
                         "VALUES (:a,:s,:r,:o,:y,:g,:ar,:m24,:m1)"),
                    dict(a=aoi, s=source, r=int(zc.get("red", 0)), o=int(zc.get("orange", 0)), y=int(zc.get("yellow", 0)),
                         g=int(zc.get("green", 0)), ar=int(live.active_red.sum()), m24=meta.get("max_r24"),
                         m1=meta.get("max_r1")))


def _store_alerts(aoi, alerts):
    with engine.begin() as con:
        for a in alerts:
            exists = con.execute(text("SELECT 1 FROM alerts WHERE aoi=:a AND title=:t"), dict(a=aoi, t=a["title"])).first()
            if not exists:
                con.execute(text("INSERT INTO alerts(aoi, source, severity, title, body, hazards) "
                                 "VALUES (:a,'SACHET (NDMA)','warning',:t,:b,:h)"),
                            dict(a=aoi, t=a["title"], b=a["body"], h=json.dumps(a["hazards"])))
                notify(aoi, f"Official alert for {aoi.upper()}: {a['title']}")


def _evac_ids(live: pd.DataFrame, habs: pd.DataFrame, min_frac=0.25) -> set:
    """Habitations with >= 25% of residents inside Active Red cells."""
    if "active_red" not in live:
        return set()
    active_pop = (live["pop"] * live.active_red).to_dict()
    pop = live["pop"].to_dict()
    out = set()
    for h in habs.itertuples():
        tot = sum(pop.get(c, 0) for c in h.cells)
        if tot and sum(active_pop.get(c, 0) for c in h.cells) / tot >= min_frac:
            out.add(h.hab_id)
    return out


def _update_evacuation(aoi):
    st = S[aoi]
    evac = _evac_ids(st["live"], st["habs"])
    new = evac - st["evac"]
    if new:
        h = st["habs"].set_index("hab_id").loc[sorted(new)]
        names = ", ".join(h.name.head(10))
        notify(aoi, f"EVACUATION ADVISORY ({aoi.upper()}): {len(new)} habitation(s), {int(h['pop'].sum()):,} people "
                    f"now inside Active Red Zones: {names}")
    st["evac"] = evac


def apply_field_reports(aoi, notify_upgrades=True):
    rep = _q("SELECT ST_Y(geom) lat, ST_X(geom) lon FROM field_reports "
             "WHERE aoi=:a AND created_at > now() - interval '30 days'", a=aoi)
    habs = S[aoi]["habs"]
    counts = np.zeros(len(habs), int)
    for r in rep.itertuples():
        d = _haversine(habs.lat.values, habs.lon.values, r.lat, r.lon)
        counts[d <= 1.5] += 1
    before = habs.priority.copy()
    habs["field_reports"] = counts
    habs = prioritise(habs)
    S[aoi]["habs"] = habs
    up = habs[(habs.priority == "immediate") & (before != "immediate")]
    if notify_upgrades and len(up):
        notify(aoi, f"Priority upgraded to IMMEDIATE after field reports: {', '.join(up.name)}")
    with engine.begin() as con:
        for h in habs.itertuples():
            con.execute(text("UPDATE habitations SET priority=:p WHERE hab_id=:i"), dict(p=h.priority, i=h.hab_id))
    return up


def _haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


def _aoi(aoi):
    if aoi not in S:
        raise HTTPException(404, f"unknown AOI {aoi}")
    return S[aoi]


def _clean(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.round(4).to_json(orient="records"))


@app.on_event("startup")
def startup():
    load_state()
    sched = BackgroundScheduler()
    for aoi in S:
        sched.add_job(refresh, "interval", minutes=60, args=[aoi], max_instances=1, coalesce=True)
    threading.Thread(target=lambda: [refresh(a, "startup") for a in list(S)], daemon=True).start()  # one at a time
    sched.start()


# ---------------------------------------------------------------- auth
class LoginReq(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def auth_login(req: LoginReq):
    return login(req.username, req.password)


@app.get("/api/auth/me")
def auth_me(request: Request):
    return request.state.user


class RegisterReq(BaseModel):
    username: str
    name: str
    password: str
    email: str
    organisation: str
    reason: str = ""


@app.post("/api/auth/register")
def auth_register(req: RegisterReq):
    return register(req.username, req.name, req.password, req.email, req.organisation, req.reason)


@app.get("/api/auth/users")
def auth_users():
    return json.loads(json.dumps(list_users(), default=str))


@app.post("/api/auth/users/{username}/approve")
def auth_approve(username: str, request: Request):
    return decide(username, True, request.state.user["sub"])


@app.post("/api/auth/users/{username}/reject")
def auth_reject(username: str, request: Request):
    return decide(username, False, request.state.user["sub"])


# ---------------------------------------------------------------- read endpoints
@app.get("/api/aois")
def aois(request: Request):
    allowed = request.state.user.get("aoi")
    metrics_path = ROOT / "backend" / "ml_artifacts" / "landslide_uk_metrics.json"
    metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else None
    out = []
    for k, m in AOI_META.items():
        if allowed and k != allowed:
            continue
        c = S[k]["cells"]
        out.append({**m, "id": k, "weights": WEIGHTS[k], "center": [float(c.lat.mean()), float(c.lon.mean())],
                    "bbox": [float(v) for v in (c.lon.min(), c.lat.min(), c.lon.max(), c.lat.max())], "cells": len(c),
                    "model": metrics if k == "uk" else None})
    return out


CELL_FIELDS = ["zone", "mhi", "hz_max", "hz_dominant", "pop"]


@app.get("/api/cells/{aoi}")
def cells(aoi: str, mode: str = "static"):
    st = _aoi(aoi)
    df = st["live"] if mode == "live" else st["cells"]
    hz = [f"hz_{h}" for h in AOI_META[aoi]["hazards"]]
    cols = CELL_FIELDS + hz + (["active_red"] if mode == "live" and "active_red" in df else [])
    d = df[cols].copy()
    d[["mhi", "hz_max", *hz]] = d[["mhi", "hz_max", *hz]].round(3)
    d["pop"] = d["pop"].round(0)
    return {"columns": ["h3", *cols], "rows": [[i, *r] for i, r in zip(d.index, d.values.tolist())]}


@app.get("/api/cell/{aoi}/{cell}")
def cell(aoi: str, cell: str):
    st = _aoi(aoi)
    if cell not in st["cells"].index:
        raise HTTPException(404, "cell not in AOI")
    r = _no_geom("h3_cells", aoi, h3=cell).iloc[0]  # full row (all ~30 columns) fetched on demand
    lv = st["live"].loc[cell]
    out = {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in r.items() if k != "aoi"}
    out["factors"] = json.loads(r["ls_factors"]) if isinstance(r.get("ls_factors"), str) else []
    out["live_zone"], out["live_mhi"] = lv.zone, float(lv.mhi)
    out["r24"], out["r1"] = float(lv.get("r24", 0) or 0), float(lv.get("r1", 0) or 0)
    return json.loads(pd.Series(out).to_json())


HAB_LIST = ["hab_id", "name", "place", "district", "lat", "lon", "pop", "priority", "risk", "mhi", "svi", "frac_red",
            "frac_orange", "dominant_hazard", "dist_health", "dist_road", "hist", "nearest_event", "nearest_event_km",
            "field_reports"]


@app.get("/api/habitations/{aoi}")
def habitations(aoi: str, priority: str | None = None):
    st = _aoi(aoi)
    h = st["habs"]
    if priority:
        h = h[h.priority.isin(priority.split(","))]
    out = h[HAB_LIST].assign(evacuate=h.hab_id.isin(st["evac"]), reasons=h.reasons)
    return _clean(out.sort_values("risk", ascending=False))


@app.get("/api/habitations/{aoi}/{hab_id}")
def habitation(aoi: str, hab_id: str):
    st = _aoi(aoi)
    h = st["habs"].set_index("hab_id")
    if hab_id not in h.index:
        raise HTTPException(404)
    r = h.loc[hab_id]
    s = st["sites"].copy()
    road = st.get("road")
    if road is not None and len(road):
        s["distance_km"] = s.site_id.map(road[road.hab_id == hab_id].set_index("site_id").road_km)
        s["by_road"] = True
        s = s[s.distance_km.notna() & (s.distance_km <= MAX_ROAD_KM) & (s.capacity >= r["pop"])]
    else:
        s["distance_km"] = _haversine(s.lat.values, s.lon.values, r.lat, r.lon)
        s["by_road"] = False
        s = s[(s.distance_km <= 40) & (s.capacity >= r["pop"])]
    s["score"] = s.suitability - 0.01 * s.distance_km
    rec = s.sort_values("score", ascending=False).head(5)
    plan = st["plan"][st["plan"].hab_id == hab_id]
    cells_df = st["cells"].loc[[c for c in r.cells if c in st["cells"].index]]
    return {**json.loads(r.drop(["cells"]).to_json()), "hab_id": hab_id, "evacuate": hab_id in st["evac"],
            "cells": r.cells, "zone_breakdown": cells_df.zone.value_counts().to_dict(),
            "recommended_sites": _clean(rec.drop(columns=["aoi"], errors="ignore")),
            "assigned": _clean(plan)[0] if len(plan) else None}


@app.get("/api/sites/{aoi}")
def sites(aoi: str):
    s = _aoi(aoi)["sites"]
    used = _aoi(aoi)["plan"].groupby("site_id").persons.sum()
    return _clean(s.drop(columns=["aoi"], errors="ignore").assign(allocated=s.site_id.map(used).fillna(0)))


@app.get("/api/plans/{aoi}")
def plans(aoi: str):
    st = _aoi(aoi)
    p = st["plan"]
    h = st["habs"].set_index("hab_id")[["name", "lat", "lon", "district"]]
    s = st["sites"].set_index("site_id")[["lat", "lon"]].rename(columns={"lat": "site_lat", "lon": "site_lon"})
    out = p.join(h, on="hab_id").join(s, on="site_id")
    return {"rows": _clean(out), "assigned_people": int(p[p.site_id.notna()].persons.sum()),
            "unassigned_people": int(p[p.site_id.isna()].persons.sum()),
            "scenario": p.scenario.iat[0] if len(p) else None}


class OptimizeReq(BaseModel):
    priorities: list[str] = ["immediate", "short"]


@app.post("/api/plans/{aoi}/optimize")
def optimize(aoi: str, req: OptimizeReq):
    st = _aoi(aoi)
    plan = allocate(st["habs"], st["sites"], tuple(req.priorities), road=st.get("road")).assign(
        aoi=aoi, scenario="+".join(req.priorities))
    st["plan"] = plan
    with engine.begin() as con:
        con.execute(text("DELETE FROM relocation_plans WHERE aoi=:a"), dict(a=aoi))
    plan.to_sql("relocation_plans", engine, if_exists="append", index=False)
    return plans(aoi)


def _summary(aoi):
    st = _aoi(aoi)
    c, lv, h, s, p = st["cells"], st["live"], st["habs"], st["sites"], st["plan"]
    pop_red = c.loc[c.zone == "red", "pop"].sum()
    return {
        "aoi": aoi, "region": AOI_META[aoi]["name"],
        "zones": c.zone.value_counts().reindex(["red", "orange", "yellow", "green"]).fillna(0).astype(int).to_dict(),
        "live_zones": lv.zone.value_counts().reindex(["red", "orange", "yellow", "green"]).fillna(0).astype(int).to_dict(),
        "active_red_cells": int(lv.get("active_red", pd.Series(dtype=bool)).sum()),
        "population_total": int(c["pop"].sum()), "population_in_red": int(pop_red),
        "habitations": len(h), "counts": h.priority.value_counts().to_dict(),
        "people": h.groupby("priority")["pop"].sum().round(0).astype(int).to_dict(),
        "people_immediate": int(h.loc[h.priority == "immediate", "pop"].sum()),
        "immediate_habitations": _clean(h[h.priority == "immediate"].nlargest(15, "risk")[["name", "district", "pop", "dominant_hazard"]]),
        "evacuation": {"habitations": len(st["evac"]), "people": int(h[h.hab_id.isin(st["evac"])]["pop"].sum())},
        "sites": len(s), "site_capacity": int(s.capacity.sum()),
        "plan_assigned": int(p[p.site_id.notna()].persons.sum()) if len(p) else 0,
        "plan_unassigned": int(p[p.site_id.isna()].persons.sum()) if len(p) else 0,
        "live": {"max_r24": st["live_meta"].get("max_r24", 0) or 0, "max_r1": st["live_meta"].get("max_r1", 0) or 0,
                 **st["live_meta"]},
        "alerts": _clean(_q("SELECT title, source, issued_at FROM alerts WHERE aoi=:a ORDER BY issued_at DESC LIMIT 10", a=aoi)),
    }


@app.get("/api/summary/{aoi}")
def summary(aoi: str):
    return _summary(aoi)


# ---------------------------------------------------------------- live / what-if
@app.post("/api/refresh/{aoi}")
def refresh_now(aoi: str):
    _aoi(aoi)
    refresh(aoi, "manual")
    return S[aoi]["live_meta"]


class SimReq(BaseModel):
    rain24: float = 0           # mm in 24 h
    rain1: float = 0            # mm in the peak hour
    lat: float | None = None    # optional storm centre
    lon: float | None = None
    radius_km: float = 15
    weights: dict[str, float] | None = None
    alerts: dict[str, float] | None = None   # e.g. {"surge": 1.4}


@app.post("/api/simulate/{aoi}")
def simulate(aoi: str, req: SimReq):
    st = _aoi(aoi)
    c = st["cells"]
    if req.lat is not None and req.lon is not None:
        d = _haversine(c.lat.values, c.lon.values, req.lat, req.lon)
        fall = np.clip(1 - d / req.radius_km, 0, 1)  # linear decay from the storm centre
    else:
        fall = np.ones(len(c))
    rain = pd.DataFrame({"r24": req.rain24 * fall, "r1": req.rain1 * fall}, index=c.index)
    weights = req.weights or WEIGHTS[aoi]
    alerts = {h: 1 + (m - 1) * fall for h, m in (req.alerts or {}).items()}  # alerts fade with the storm footprint
    sim = classify(apply_triggers(c, aoi, rain, alerts), weights)
    sim["active_red"] = (sim.zone == "red") & (c.zone != "red")
    affected_ids = _evac_ids(sim, st["habs"])
    aff = st["habs"][st["habs"].hab_id.isin(affected_ids)]
    zc = sim.zone.value_counts().reindex(["red", "orange", "yellow", "green"]).fillna(0).astype(int).to_dict()
    return {"zones": zc, "active_red_cells": int(sim.active_red.sum()),
            "affected": _clean(aff.nlargest(50, "pop")[["hab_id", "name", "district", "pop", "lat", "lon"]]),
            "affected_people": int(aff["pop"].sum()), "affected_habitations": len(aff),
            "columns": ["h3", "zone", "mhi", "active_red"],
            "rows": [[i, z, round(m, 3), bool(a)] for i, z, m, a in zip(sim.index, sim.zone, sim.mhi, sim.active_red)]}


@app.get("/api/alerts/{aoi}")
def alerts(aoi: str):
    _aoi(aoi)
    return {"alerts": _clean(_q("SELECT * FROM alerts WHERE aoi=:a ORDER BY issued_at DESC LIMIT 50", a=aoi)),
            "notifications": _clean(_q("SELECT * FROM notifications WHERE aoi=:a ORDER BY created_at DESC LIMIT 50", a=aoi)),
            "snapshots": _clean(_q("SELECT * FROM zone_snapshots WHERE aoi=:a ORDER BY run_at DESC LIMIT 24", a=aoi)),
            "live": S[aoi]["live_meta"]}


@app.get("/api/validation/{aoi}")
def validation(aoi: str):
    _aoi(aoi)
    p = ROOT / "backend" / "ml_artifacts" / f"validation_{aoi}.json"
    if not p.exists():
        raise HTTPException(404, "run pipeline/p07_validation.py first")
    return json.loads(p.read_text(encoding="utf-8"))


@app.get("/api/events/{aoi}")
def events(aoi: str):
    return _clean(_q("SELECT aoi, name, type, date, lat, lon, deaths, precision, source FROM hazard_events WHERE aoi=:a", a=aoi))


# ---------------------------------------------------------------- field reports
@app.post("/api/field-reports")
async def field_report(request: Request, aoi: str = Form(...), kind: str = Form(...), lat: float = Form(...),
                       lon: float = Form(...), note: str = Form(""), reporter: str = Form(""),
                       photo: UploadFile | None = File(None)):
    user = request.state.user
    if user.get("aoi") and user["aoi"] != aoi:
        raise HTTPException(403, "You can only report in your own region")
    reporter = reporter or f"{user['name']} ({user['sub']})"
    c = _aoi(aoi)["cells"]
    m = 0.05  # ~5 km margin around the region
    if not (c.lat.min() - m <= lat <= c.lat.max() + m and c.lon.min() - m <= lon <= c.lon.max() + m):
        raise HTTPException(400, f"location {lat:.4f}, {lon:.4f} is outside {AOI_META[aoi]['name']}; "
                                 "check the region and coordinates")
    fname = None
    if photo and photo.filename:
        ext = Path(photo.filename).suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
            raise HTTPException(400, "photo must be an image")
        data = await photo.read()
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(400, "photo larger than 10 MB")
        fname = f"{uuid.uuid4().hex}{ext}"
        (UPLOADS / fname).write_bytes(data)
    with engine.begin() as con:
        con.execute(text("INSERT INTO field_reports(aoi, kind, note, photo, reporter, geom) "
                         "VALUES (:a,:k,:n,:p,:r, ST_SetSRID(ST_MakePoint(:lon,:lat),4326))"),
                    dict(a=aoi, k=kind, n=note[:1000], p=fname, r=reporter[:100], lon=lon, lat=lat))
    up = apply_field_reports(aoi)
    return {"ok": True, "upgraded": up.name.tolist()}


@app.get("/api/field-reports/{aoi}")
def field_reports(aoi: str):
    return _clean(_q("SELECT id, kind, note, photo, reporter, created_at, ST_Y(geom) lat, ST_X(geom) lon "
                     "FROM field_reports WHERE aoi=:a ORDER BY created_at DESC", a=aoi))


# ---------------------------------------------------------------- briefing & report
def _facts(aoi):
    f = _summary(aoi)
    f["zone_cell_counts_not_people"] = f.pop("zones")
    f["habitation_counts_by_priority"] = f.pop("counts")
    f["people_by_priority"] = f.pop("people")
    f.pop("live_zones")
    f["top_sites"] = _clean(S[aoi]["sites"].head(5)[["site_id", "district", "capacity", "limiting_factor", "suitability"]])
    return f


@app.get("/api/briefing/{aoi}")
def get_briefing(aoi: str, lang: str = "English"):
    return briefing(_facts(aoi), lang)


@app.get("/api/report/{aoi}.pdf")
def report(aoi: str, lang: str = "English"):
    from .report import build_pdf
    st = _aoi(aoi)
    facts = _summary(aoi)
    brief = briefing(_facts(aoi), "English")  # ponytail: built-in PDF fonts lack Devanagari; embed a Noto font for Hindi PDFs
    pdf = build_pdf(facts, brief, st["cells"], st["habs"], st["sites"], st["plan"])
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="SURAKSHA_{aoi}_{datetime.now():%Y%m%d}.pdf"'})


# ---------------------------------------------------------------- built dashboard (production: one container)
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        f = (DIST / path).resolve()
        if path and f.is_file() and DIST.resolve() in f.parents:  # no path traversal outside dist/
            return FileResponse(f)
        return FileResponse(DIST / "index.html")  # client-side routes such as /field

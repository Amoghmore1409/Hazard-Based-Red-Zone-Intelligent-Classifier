# SURAKSHA: Multi-Hazard Red Zone & Relocation Decision Support System

SIH, MHA / NDRF DM Division. An AI + GIS platform that maps hazard Red Zones, prioritises vulnerable habitations for relocation, and finds safe sites with enough carrying capacity. See [PLAN.md](PLAN.md) for the full design.

**Pilot regions:** Uttarakhand (Chamoli + Rudraprayag) for landslide, cloudburst and flash flood · Odisha (Kendrapara) for coastal erosion, storm surge and flood.

## Run it

Prerequisites: Docker Desktop, Python 3.12, Node 20+. Earth Engine access is needed only to rebuild the data.

```bash
docker compose up -d                                   # PostGIS on :5433
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
cd frontend && npm install && cd ..
```

`.env` (git-ignored):
```
GEE_PROJECT=<your-ee-project>
GROQ_API_KEY=<optional, for AI briefing>
# optional notifications
SMTP_HOST=smtp.gmail.com  SMTP_USER=...  SMTP_PASSWORD=<app password>  ALERT_EMAIL_TO=...
TWILIO_SID=...  TWILIO_TOKEN=...  TWILIO_FROM=...  ALERT_SMS_TO=...
```

### Data pipeline (once, about 15 minutes)
```bash
cd pipeline
python p01_gee_export.py      # Earth Engine -> data/processed/{aoi}_stack.tif
python p02_osm.py             # OSM roads, rivers, health, schools, settlements
python p03_features.py        # pixels -> H3 res-9 feature table
python p04_hazards.py         # landslide XGBoost + hazard indices + Red Zones
python p05c_census.py         # Census 2011 village PCA + houselisting (needs data/raw/census/)
python p05_habitations_sites.py  # habitations, Census-based vulnerability, priority, safe sites, capacity
python p05b_roads.py          # road-network distances habitation -> site (OSM graph, Dijkstra)
python p06_load.py            # -> PostGIS + baseline allocation
python p07_validation.py      # back-test vs past disasters + GSI (Validation tab)
```
`data/raw/gsi/landslide_polygon.shp` (the GSI landslide inventory) is required for the landslide model.

### Users (once)
```bash
.venv\Scripts\python -m backend.app.auth seed   # JWT secret + 5 demo users; passwords written to .env (DEMO_PASSWORD_*)
.venv\Scripts\python -m backend.app.auth add <username> <admin|sdma|viewer|field> [uk|od] "Full Name"
```
Roles: **admin** (NDRF, all regions, all actions) · **sdma** (own region; plans, refresh, field reports) · **viewer** (read-only, what-if allowed) · **field** (own region, field reports only). Anyone can **request viewer access** from the login page; the account stays pending until an admin approves it in the dashboard's **Users** panel (rejected requests cannot sign in). SDMA, field and admin accounts are created only with the `add` command. Checks (API running): `python backend/check_auth.py`, `python backend/check_register.py`.

### App
```bash
.venv\Scripts\python -m uvicorn backend.app.main:app --port 8000
npm --prefix frontend run dev        # http://localhost:5173  (field app: /field)
```
API docs: http://localhost:8000/docs

### Deploy (free)
Supabase (database) + Render (API + dashboard, one Docker container from `Dockerfile` / `render.yaml`): step by step in [DEPLOY.md](DEPLOY.md).

## How it works

| Step | Method |
|---|---|
| Grid | Uber H3 resolution 9 (~0.1 km²), about 85k cells in UK and 21k in OD |
| Landslide | XGBoost on 21 terrain, rainfall and land-cover features; labels from 3,315 GSI polygons. GSI mapping is road-biased (median 336 m from a road vs 3 km for all cells), so the model trains only inside the surveyed domain (≤ 1.5 km from roads) without road distance. **Spatial 5-fold CV AUC 0.775** (H3 res-6 blocks), with honest out-of-fold scores. SHAP drivers per cell |
| Flood | HAND (MERIT Hydro) × upstream area + JRC historical water occurrence + river proximity |
| Cloudburst | orographic belt (1–3.5 km), steep confined valleys, CHIRPS 99th-percentile monsoon rain |
| Coastal erosion | Landsat MNDWI shoreline change 1990→2024, coast proximity, low elevation, mangrove buffer |
| Storm surge | elevation < 7 m with inland decay from the coastline |
| Red Zone | AHP-weighted multi-hazard index **or** any single hazard ≥ 0.9 **or** ≥ 3 recorded events within ~350 m |
| Live | Every hour, the Open-Meteo 72 h forecast (24 h accumulation, peak hourly rain) scales landslide, flood and cloudburst scores, and NDMA SACHET / IMD / CWC alerts scale surge and coastal scores. Cells that turn Red only because of these are **Active Red Zones** (evacuation), kept separate from **Permanent Red Zones** (relocation). An evacuation advisory fires when ≥ 25% of a village's residents are in Active Red |
| Habitation | OSM villages (plus population clusters where OSM is sparse) ← WorldPop 2020 cells within 4 km |
| Vulnerability | **Census 2011** village PCA + Houselisting HH-14: 5 equal dimensions (housing: kutcha/dilapidated · social: SC/ST, illiterate, children 0–6 · economic: marginal workers, no assets · services: no electricity, open defecation · access: distance to health care and roads). Kendrapara joins exactly via DataMeet village boundaries (ODbL); Uttarakhand by village-name match (exact/fuzzy), hamlets borrow the nearest matched village, else district average. `census_source` records which |
| Distances | Habitation → site distances follow the OSM road network (SciPy Dijkstra; median 2.65× straight-line in the hills, 1.7× in the delta); relocation limited to 60 km by road |
| Priority | Immediate: ≥ 50% of residents in Red AND (top-10% risk OR fatal event within 3 km since 2010), OR field reports of ground distress (2, or 1 in an Orange/Red footprint). Short-term: ≥ 25% in Red. Medium-term: Orange exposure or recurring events |
| Carrying capacity | min(land: usable ha × 40% × 200 p/ha · water: 55 LPCD (JJM) source proxy · services: IPHS PHC/school spare capacity) |
| Allocation | MILP (PuLP/CBC): whole habitations go to one site each (communities stay together), minimise person-km, respect capacity, Immediate cases first |

## Demo script (5 min)
1. **Permanent Red Zones**, Uttarakhand. Click a red hexagon to see the hazard breakdown and its SHAP landslide drivers.
2. **Habitations → Immediate**. Open a village to see the explanation, zone breakdown and recommended sites with land, water and services gauges.
3. **Sites & Plan → Run allocation**. Arcs show habitation → site, with people who could not be placed listed separately.
4. **What-if → "Cloudburst over Chamoli"**. Active Red cells appear and the list of affected habitations updates.
5. Switch to **Odisha**. Coastal erosion near Satabhaya, then the **"Severe cyclone landfall"** scenario.
6. **Field app** (`/field`) on a phone: report ground cracks and watch the village get upgraded to Immediate.
7. **AI Briefing** (English/Hindi) and **PDF Report**.

## Known limitations (be upfront with judges)
- Census data is from 2011. Uttarakhand has no open village boundaries, so ~60% of habitations (mostly hamlets) borrow the nearest name-matched village's indicators; SHRUG village polygons (CC BY-NC-SA, needs terms acceptance) would make this exact. Elderly share is not in the village PCA.
- Road distances ignore travel time and road condition; OSM gaps are bridged up to 150 m.
- Uploaded field photos are served from unguessable URLs without a login check.
- Water capacity uses a distance-to-source proxy, not measured yield. Road distances use OSM geometry (no travel time / road condition).
- OSM has few schools mapped in these districts, so the service score leans on health facilities.
- This is decision support: Red Zones and priorities must be verified on the ground before any action.

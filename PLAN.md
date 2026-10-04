# SURAKSHA: Multi-Hazard Red Zone & Relocation Decision Support System
**SIH problem statement:** Intelligent Identification of Hazard-Based Red Zones, Carrying Capacity Assessment, and Immediate Relocation Needs for Vulnerable Habitations (MHA / NDRF, DM Division)

---

## 1. What we are building

An AI-driven GIS web platform for **State/District Disaster Management Authorities (SDMA/DDMA)** that answers four questions:

| # | Question | Module | Output |
|---|----------|--------|--------|
| 1 | **Where is it unsafe to live?** | Multi-Hazard Red Zone engine | Red / Orange / Yellow / Green zone map, updated when rainfall forecasts or alerts change |
| 2 | **Who is at risk?** | Habitation vulnerability & risk scoring | A risk score for every village or settlement, with the reasons behind it |
| 3 | **Who must move, and how soon?** | Relocation prioritisation | Each habitation tagged **Immediate (0–3 months)**, **Short-term (3–12 months)**, **Medium-term (1–3 years)** or **Monitor** |
| 4 | **Where can they go?** | Site suitability, carrying capacity and allocation | Ranked safe sites, how many people each can hold, and an optimised habitation-to-site allocation |

Plus: a what-if simulator (change rainfall or weights and watch the zones change), explainable AI (why a village is red), PDF action reports for SDMA, and a live alert feed.

### Pilot regions (confirmed)
| AOI | Area | Hazards | Why |
|---|---|---|---|
| **Himalayan: Uttarakhand (Chamoli + Rudraprayag)** | ~10,000 km² | landslide, cloudburst, flash flood | Kedarnath 2013, Joshimath 2023 subsidence, Rishiganga 2021 |
| **Coastal: Odisha (Kendrapara district)** | ~2,650 km² | coastal erosion, cyclone storm surge, flood | **Satabhaya**: villages swallowed by the sea and relocated to Bagapatia, a real-world validation case for our relocation logic |

The architecture does not depend on the region. Adding a state means adding an AOI polygon and re-running the pipeline.

---

## 2. Core concepts and methodology

### 2.1 Spatial unit: H3 hexagonal grid
Everything is computed on **Uber H3 hexagons at resolution 9 (~0.1 km² each)**. A district has roughly 50k–100k cells, which is small enough for a laptop. Hexagons aggregate cleanly, render fast on the web, and avoid raster-handling complexity in the app. Raster processing happens only in offline preprocessing.

### 2.2 Hazard models (one score from 0 to 1 per cell per hazard)

| Hazard | Static susceptibility (slow-changing) | Dynamic trigger (real-time) | Method |
|---|---|---|---|
| **Landslide** | slope, aspect, curvature, TWI, lithology, distance to faults/rivers/roads, LULC, NDVI, soil | 1/3/7-day antecedent + forecast rainfall vs. intensity–duration thresholds | **XGBoost / Random Forest** trained on the GSI landslide inventory (positive) vs. sampled stable cells (negative). SHAP for explanations. |
| **Flood / flash flood** | HAND (Height Above Nearest Drainage), distance to river, stream order, historical water occurrence (JRC) | forecast rainfall over the upstream catchment | Weighted HAND model, calibrated against historical flood extents (ML optional) |
| **Cloudburst** | orographic factors (elevation band, slope, valley confinement), historical cloudburst density | forecast hourly rainfall ≥ 100 mm/h proxy; IMD/SACHET alerts | Rule plus historical-density model, mostly a dynamic layer |
| **Coastal erosion** (coastal AOI) | shoreline change rate (computed in GEE from Landsat NDWI shorelines, 1990 → 2024), elevation, distance to coast, LULC (mangrove buffer lowers risk) | cyclone alerts | Weighted index; validated against Satabhaya |
| **Cyclone / storm surge** (coastal AOI) | elevation < 5 m, distance to coast, historical cyclone track density (IBTrACS) | IMD cyclone warning (SACHET), forecast wind/precip | Weighted index + bathtub inundation for the surge-height scenario |

### 2.3 Multi-hazard index and Red Zone classification
```
Static MHI  = Σ wᵢ · Hᵢ_static   (weights from AHP, editable by SDMA)
Dynamic MHI = Static MHI × trigger multiplier (rainfall/alert based)
Zone        = max-rule + thresholds:
  RED     → MHI ≥ 0.75  OR any single hazard ≥ 0.85  OR ≥ 2 recorded damaging events within 500 m
  ORANGE  → 0.5–0.75
  YELLOW  → 0.3–0.5
  GREEN   → < 0.3
```
- **Permanent Red Zone:** red under static conditions, so the land is unsuitable for permanent habitation.
- **Active Red Zone:** red only under the current trigger, so it calls for an evacuation alert, not relocation.

The platform keeps these two apart, and the distinction is central to the problem statement.

### 2.4 Habitation vulnerability (Social Vulnerability Index)
For each habitation (Census village or OSM/Google Open Buildings settlement cluster):
- **Exposure:** population (WorldPop 2020 + Census 2011), building count, fraction of buildings inside red/orange cells
- **Sensitivity:** % kutcha houses, % elderly and children, % SC/ST, literacy, disability (Census HH tables)
- **Coping capacity (inverse):** distance to hospital/PHC, road access, distance to a shelter, mobile connectivity
- **History:** count of past events within the buffer, deaths, recurrence

SVI is built with PCA or AHP-weighted normalised indicators.

### 2.5 Relocation priority
```
Risk = Hazard(MHI over habitation footprint) × Exposure × Vulnerability × History factor
```
| Category | Rule (configurable) |
|---|---|
| **Immediate** | in a Permanent Red Zone AND (Risk ≥ P90 OR fatal event in the last 5 years OR active ground cracks/subsidence reported) |
| **Short-term** | in a Red Zone, or Orange with high SVI |
| **Medium-term** | Orange, or Yellow with recurring events |
| **Monitor** | the rest |

Each decision comes with an **explanation card**, for example: "Slope 38°, 4 landslides within 1 km since 2013, 62% kutcha houses, 14 km to the nearest PHC".

### 2.6 Safe site suitability and carrying capacity
1. **Candidate sites:** contiguous Green cells within 25 km road distance of the source habitation (same block or district where possible, to keep communities and livelihoods intact), area ≥ minimum.
2. **Exclusions:** forests and protected areas (WDPA), water bodies, slope > 20°, any hazard > 0.3, high-risk buffer zones.
3. **Suitability (MCDA/AHP):** slope, road access, distance to water source, distance to school/PHC/market, land cover (barren or agriculture preferred), soil stability.
4. **Carrying capacity = min of:**
   - Land: usable area × planning density norm (e.g. 150–250 persons/ha for hill rural housing)
   - Water: available supply ÷ 55 LPCD (Jal Jeevan Mission norm)
   - Services: spare capacity of nearby schools and PHCs (IPHS norms)
   - Sanitation/road: optional limits
5. **Allocation optimisation** (PuLP/OR-Tools): assign habitations to sites to minimise distance plus community splitting, subject to site capacity and Immediate cases going first. Communities are not split unless necessary.

---

## 3. Tech stack

| Layer | Choice | Why |
|---|---|---|
| **Language (backend/ML)** | Python 3.11 | best geospatial and ML ecosystem |
| **API** | **FastAPI** + Uvicorn, Pydantic | fast, async, auto Swagger docs (good for judges) |
| **Geospatial processing** | GeoPandas, Shapely 2, Rasterio, rioxarray, **WhiteboxTools** (terrain: slope, TWI, HAND), **h3-py**, OSMnx | all free and pip-installable |
| **ML** | scikit-learn, **XGBoost**, **SHAP**, imbalanced-learn | accurate, explainable, CPU-only |
| **Optimisation** | **PuLP** (CBC solver) or Google OR-Tools | allocation of people to sites |
| **Scheduler** | APScheduler (in the API process) | periodic rainfall/alert refresh, no Celery needed |
| **Database** | **PostgreSQL 16 + PostGIS 3** (+ h3-pg extension optional) | spatial queries, single source of truth |
| **Map tiles** | **Martin** vector tile server (from PostGIS), or GeoJSON for small layers | smooth maps with 100k hexagons |
| **Frontend** | **React + Vite + TypeScript**, **MapLibre GL JS** + **deck.gl** (H3HexagonLayer), Tailwind + shadcn/ui, Recharts, TanStack Query | free (no Mapbox key), fast, modern UI |
| **Basemaps** | OSM / CARTO / ESRI satellite tiles, with optional ISRO **Bhuvan** WMS | free; Bhuvan adds an Indian-government touch |
| **Reports** | WeasyPrint or ReportLab (PDF), plus CSV/GeoJSON/KML export | SDMA-ready output |
| **Auth** | JWT with roles (State admin, District officer, Viewer) | simple and adequate |
| **Optional AI assistant** | an LLM API (e.g. Claude) for natural-language "SDMA briefing" summaries and queries | a standout feature for judges, kept out of the core logic |
| **Optional field app** | PWA form for ground-truth reports (cracks, seepage, photos with GPS) | feeds the "Immediate" trigger and model retraining |
| **DevOps** | **Docker Compose** (postgis, martin, api, web) | one command to run the demo |

---

## 4. System architecture

```
                ┌──────────────────────── OFFLINE / BATCH ────────────────────────┐
 Raw data  ──►  │ ingest/ scripts → clip to AOI → terrain derivatives → H3 grid   │
 (DEM, LULC,    │ feature table → train hazard models → static scores → PostGIS   │
 inventories,   └─────────────────────────────────────────────────────────────────┘
 census...)                                   │
                                              ▼
 Live feeds ──► Scheduler (every 1–3 h) ─► Dynamic trigger ─► Recompute MHI/zones ─► alerts table
 (Open-Meteo,                                 │
  SACHET CAP,                                 ▼
  GDACS, USGS)          ┌──────────── PostgreSQL + PostGIS ────────────┐
                        │ h3_cells, habitations, events, sites,        │
                        │ zone_snapshots, relocation_plans, alerts     │
                        └──────────┬──────────────────────┬────────────┘
                                   │                      │
                             FastAPI (REST)         Martin (vector tiles)
                                   │                      │
                                   └────────┬─────────────┘
                                            ▼
                         React + MapLibre + deck.gl dashboard
           (Map · Red Zones · Priority list · Sites · What-if · Reports · Alerts)
```

---

## 5. Data sources (external requirements)

### 5.1 Static datasets (download once, preprocess offline)
| Data | Source | Access |
|---|---|---|
| DEM 30 m | Copernicus GLO-30 / SRTM (NASA Earthdata) / CartoDEM (Bhuvan) | free; Earthdata/Bhuvan need login |
| Landslide inventory | **GSI Bhukosh** National Landslide Susceptibility Mapping inventory; NASA Global Landslide Catalog | free (Bhukosh login) |
| Geology / lithology / faults | GSI Bhukosh | free |
| Land cover 10 m | ESA WorldCover 2021 | free, no login |
| NDVI | Sentinel-2 (Copernicus Browser or GEE) | free |
| Soil | SoilGrids (ISRIC) / NBSS&LUP | free |
| Rivers / drainage | HydroSHEDS + derived from DEM; OSM waterways | free |
| Historical floods | JRC Global Surface Water (occurrence), Global Flood Database, Bhuvan flood hazard atlas | free |
| Coastal erosion | NCCR shoreline change atlas; or derived from Landsat | free/public |
| Population | **WorldPop 2020 100 m** (India), GHSL; Census 2011 village PCA & HH tables | free |
| Buildings | **Google Open Buildings** / OSM / Microsoft footprints | free |
| Roads, hospitals, schools | OpenStreetMap (Geofabrik India extract), data.gov.in facility lists | free |
| Village boundaries | Census/SOI village shapefiles (data.gov.in / community repos) | free |
| Protected areas | WDPA (Protected Planet) | free |
| Disaster events | NDMA reports, EM-DAT, GSI event records, DesInventar, curated news (Kedarnath 2013, Joshimath 2023, etc.) | free; partly manual curation |

### 5.2 Real-time feeds
| Feed | Use | Key needed? |
|---|---|---|
| **Open-Meteo Forecast API** (hourly precipitation, 7-day) | landslide/cloudburst/flood triggers | **No** |
| **NDMA SACHET CAP alerts** (sachet.ndma.gov.in RSS/CAP) | official IMD/CWC warnings to escalate zones | No |
| GPM IMERG near-real-time | observed antecedent rainfall | NASA Earthdata login |
| GDACS / USGS earthquake feed | earthquake-triggered landslide escalation | No |
| CWC flood forecast (if scrapable) | river-level flood trigger | No |

### 5.3 Accounts / software to set up
- **NASA Earthdata** account (SRTM, IMERG): free, instant
- **GSI Bhukosh** registration: free
- **Bhuvan (NRSC)** registration: free, optional
- **Google Earth Engine** (optional, speeds up NDVI/JRC water extraction): noncommercial sign-up, can take a day or two
- LLM API key (optional, only for the AI briefing feature)
- Twilio / SMTP (optional, for SMS/email alerts)

### 5.4 Local machine
- Windows 11 with **Docker Desktop** (WSL2), **Python 3.11**, **Node 20 LTS**, Git
- 16 GB RAM recommended; ~15–25 GB disk for the raw data of the pilot districts
- QGIS (recommended, for checking layers visually during preprocessing)

### 5.5 Fallback for the demo
A **seed demo dataset** (preprocessed GeoParquet + SQL dump) ships with the repo, so the demo runs **offline** even if the venue internet fails. Live feeds degrade to the last cached snapshot or a scripted "cloudburst scenario".

---

## 6. Database schema (core tables)

```
aoi(id, name, state, geom)
h3_cells(h3 PK, aoi_id, geom, elev, slope, aspect, curvature, twi, hand, lulc, ndvi,
         lithology, dist_fault, dist_river, dist_road, soil, pop,
         hz_landslide, hz_flood, hz_cloudburst, hz_coastal, mhi_static)
zone_snapshots(id, run_at, trigger_source, h3, mhi_dynamic, zone, zone_type[permanent|active])
habitations(id, name, census_code, block, district, geom, pop, households, pct_kutcha,
            pct_elderly, pct_children, pct_scst, dist_phc, dist_road, svi, risk,
            priority[immediate|short|medium|monitor], explanation jsonb)
hazard_events(id, type, date, geom, deaths, damage, source)
candidate_sites(id, geom, area_ha, suitability, cap_land, cap_water, cap_services,
                carrying_capacity, nearest_road_km, notes)
relocation_plans(id, created_at, scenario jsonb, habitation_id, site_id, persons, distance_km)
alerts(id, source, severity, area geom, issued_at, expires_at, payload jsonb)
model_runs(id, model, version, metrics jsonb, created_at)      -- audit / reproducibility
field_reports(id, geom, type, photo_url, note, created_at)     -- optional PWA
users(id, name, role, district)
```

---

## 7. Project structure

```
suraksha/
├─ docker-compose.yml
├─ data/                     # raw/ (gitignored), processed/, seed/
├─ pipeline/                 # offline preprocessing & training (Python)
│  ├─ 01_download.py
│  ├─ 02_terrain.py          # slope, aspect, curvature, TWI, HAND (WhiteboxTools)
│  ├─ 03_h3_features.py      # zonal stats → H3 feature table
│  ├─ 04_train_landslide.py  # XGBoost + SHAP, metrics → model_runs
│  ├─ 05_flood_cloudburst_coastal.py
│  ├─ 06_habitations_svi.py
│  ├─ 07_sites_capacity.py
│  └─ 08_load_postgis.py
├─ backend/
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ api/               # zones, habitations, sites, plans, alerts, reports, scenarios, auth
│  │  ├─ services/          # hazard_engine, risk_engine, site_engine, allocator, feeds, explain
│  │  ├─ models/            # SQLAlchemy/GeoAlchemy2 ORM
│  │  ├─ jobs/              # APScheduler refresh jobs
│  │  └─ reports/           # PDF templates
│  └─ ml_artifacts/         # trained models (.json/.pkl)
├─ frontend/
│  └─ src/ pages/ (Dashboard, RedZones, Habitations, Sites, WhatIf, Reports, Alerts)
│         components/ (MapView, LayerPanel, PriorityTable, ExplainCard, CapacityGauge...)
└─ docs/ (architecture, methodology, data sources, demo script)
```

---

## 8. Implementation flow (phases)

| Phase | Work | Deliverable |
|---|---|---|
| **0. Setup** | repo, Docker Compose (PostGIS, Martin), FastAPI skeleton, React+MapLibre skeleton, AOI definition | `docker compose up` shows an empty map |
| **1. Data acquisition** | download and clip all static layers to AOI; curate an event history CSV | `data/processed/*` |
| **2. Terrain + H3 features** | terrain derivatives, HAND, distance rasters → H3 res-9 feature table | `h3_features.parquet` |
| **3. Hazard models** | train landslide XGBoost (spatial CV, AUC target ≥ 0.85); flood/cloudburst/coastal indices; static MHI | hazard columns + metrics |
| **4. Habitations + SVI + risk** | habitation layer, census join, SVI, risk, priority rules, explanations | prioritised habitation table |
| **5. Sites + capacity + allocation** | candidate site extraction, MCDA, capacity, PuLP allocation | sites and plan tables |
| **6. Load + API** | load to PostGIS; REST endpoints; Martin tiles | Swagger at `/docs` |
| **7. Real-time engine** | Open-Meteo + SACHET pollers → dynamic MHI → zone snapshots → alerts | zones update automatically |
| **8. Dashboard** | map layers, zone toggle (permanent/active), priority table, explain cards, site panel with capacity gauges, allocation flow lines | working UI |
| **9. What-if + reports** | rainfall/weight sliders → recompute; PDF district action report; exports | scenario and report features |
| **10. Polish + demo** | seed dataset, demo script, validation slide (AUC, comparison with GSI maps), optional AI briefing and PWA | demo-ready build |

### Key API endpoints
```
GET  /api/zones?aoi=&mode=static|live          → zone layer (or tiles via Martin)
GET  /api/cells/{h3}                            → hazard breakdown + SHAP explanation
GET  /api/habitations?priority=immediate        → prioritised list
GET  /api/habitations/{id}                      → risk card + history + recommended sites
GET  /api/sites?near={habitation_id}            → ranked safe sites with capacity
POST /api/plans/optimize                        → run allocation for a district
POST /api/scenarios/simulate                    → what-if (rainfall mm, weights)
GET  /api/alerts/live                           → active alerts
GET  /api/reports/district/{id}.pdf             → SDMA action report
POST /api/field-reports                         → ground observations (optional)
```

---

## 9. Demo storyline (≈5 minutes)
1. Open the Chamoli map: Permanent Red Zones appear with their hazard breakdown.
2. Click a village: the explanation card shows slope, past landslides, SVI, and the verdict **Immediate relocation**.
3. Open the priority dashboard: N habitations listed as Immediate, Short-term and Medium-term, with affected population.
4. Click **Find safe sites**: 3 ranked sites appear with carrying-capacity gauges (land / water / services) and the allocation draws flow lines.
5. **What-if:** set a 150 mm/24 h cloudburst forecast. The Active Red Zones expand live and alerts fire.
6. Click **Generate SDMA report**: a PDF with maps, priority list and the relocation plan.
7. Validation slide: landslide model AUC, overlap with GSI susceptibility maps and historical events.

---

## 10. Risks and mitigations
| Risk | Mitigation |
|---|---|
| Data downloads slow or require approval | start Phase 1 immediately; keep a seed dataset; OSM/WorldPop/ESA need no approval |
| Census data dated (2011) | combine with WorldPop 2020 and building footprints |
| Few labelled events for flood/cloudburst | index-based models calibrated on historical extents, not pure ML |
| Laptop compute limits | restrict to 1–2 districts; H3 res 9; precompute offline |
| Judges question accuracy | spatial cross-validation, AUC, comparison with GSI/NDMA maps, SHAP explanations |
| Internet failure during demo | everything cached locally; scripted scenario mode |

## 11. Alignment and differentiators
- Proactive rather than reactive: Permanent vs Active red zones, with relocation planned before the disaster.
- Explainable: every red zone and every priority comes with reasons (SHAP plus rules), so officers can defend decisions.
- Configurable: SDMA can tune AHP weights and thresholds per state.
- Aligned with **NDMA landslide/flood guidelines**, the **Sendai Framework**, **JJM water norms** and **IPHS** service norms.
- Built entirely on free and open-source tools and Indian public data (GSI, Bhuvan, SACHET, Census), so it is deployable in government environments.

---

## 12. Final decisions (locked for the 48-hour build)

### 12.1 Scope changes because of the 2-day deadline
| Original | Decision for 48 h | Reason |
|---|---|---|
| Manual downloads from many portals | **Google Earth Engine does almost all raster data prep** (`earthengine-api` + `geedim` for tiled downloads) | one script per AOI, no portal logins, minutes instead of hours |
| WhiteboxTools for HAND/TWI | **MERIT Hydro `hnd` band in GEE** for HAND; slope/aspect/TWI via `ee.Terrain` | removes the hardest terrain step |
| Martin vector tile server | **dropped**: FastAPI returns compact H3 JSON (`{h3, zone, mhi...}`) and deck.gl renders the hexagons | ~120k cells ≈ a few MB, one less service |
| H3 res 9 everywhere | **res 9** for the coastal AOI, **res 8 (~0.74 km²) for an overview + res 9 for zoom-in** in Uttarakhand | keeps rendering fast |
| Full GSI inventory required | GSI Bhukosh inventory **if downloaded in time**; otherwise **NASA Global Landslide Catalog + Bhuvan landslide points + curated events** | the build never waits on manual downloads |
| Field PWA as a separate app | **a mobile-responsive `/field` route** in the same React app | no extra project |

### 12.2 Environment check (done)
Python 3.12.10 ✔ · Node 25.6 ✔ · Docker 29.6 ✔ · Git 2.53 ✔ · GEE noncommercial access ✔

### 12.3 GEE datasets used
| Layer | GEE asset |
|---|---|
| DEM 30 m | `COPERNICUS/DEM/GLO30` |
| HAND, upstream area | `MERIT/Hydro/v1_0_1` (`hnd`, `upa`) |
| Land cover 10 m | `ESA/WorldCover/v200` |
| NDVI | `COPERNICUS/S2_SR_HARMONIZED` (median, monsoon-free months) |
| Surface water history | `JRC/GSW1_4/GlobalSurfaceWater` (`occurrence`) |
| Population 100 m | `WorldPop/GP/100m/pop` (2020) |
| Buildings | `GOOGLE/Research/open-buildings/v3/polygons` (counts per cell) |
| Rainfall climatology | `UCSB-CHG/CHIRPS/DAILY` (extremes: p99 daily rainfall, heavy-rain-day count) |
| Shoreline change | `LANDSAT/LT05`, `LE07`, `LC08`, `LC09` C2 L2: NDWI shorelines 1990 / 2000 / 2010 / 2024 |
| Soil | `projects/soilgrids-isric/clay_mean`, `sand_mean` |
| Admin boundaries | `FAO/GAUL/2015/level2` (districts), refined with Census village polygons where available |

Non-GEE: OSM (roads, hospitals, schools, rivers) via Overpass/OSMnx · IBTrACS cyclone tracks (CSV) · NASA Global Landslide Catalog (CSV) · GSI Bhukosh (manual, optional) · curated `events.csv` (Kedarnath 2013, Rishiganga 2021, Joshimath 2023, Satabhaya erosion, cyclones Phailin 2013 / Fani 2019 / Yaas 2021, etc.).

### 12.4 Optional features (all included)
| Feature | Implementation | External need |
|---|---|---|
| **AI SDMA briefing** | `/api/briefing/{district}` sends structured stats (zones, priorities, sites, alerts) to an LLM, which writes a 1-page plain-language briefing (English + Hindi). Grounded only on our numbers. | LLM API key (e.g. Anthropic). Without a key it falls back to a template-generated briefing |
| **Field reporting** | `/field` mobile page: GPS + photo + type (crack, seepage, subsidence, erosion). Reports in the last 30 days raise the nearby habitation to **Immediate** for review | none |
| **SMS / email alerts** | when a habitation enters an Active Red Zone or Immediate, notify the district officer. Twilio (trial accounts send only to verified numbers) or SMTP (Gmail app password); **always logged in-app**, so the demo works with no credentials | Twilio trial or Gmail app password (optional) |

---

## 13. 48-hour execution plan

Claude writes the code. Your job is to run the GEE authentication, provide optional keys, check outputs in the map, and prepare the presentation.

### Day 1: data + models (backend-heavy)
| Hours | Task | Output |
|---|---|---|
| 0–1 | Project scaffold, `docker-compose` (PostGIS), Python venv, React+Vite app, AOI GeoJSONs | everything boots |
| 1–4 | **GEE export pipeline** for both AOIs → GeoTIFFs; OSM + IBTrACS + landslide catalog + `events.csv` | `data/processed/` |
| 4–7 | H3 feature builder (zonal stats per cell); distance features | `features_{aoi}.parquet` |
| 7–10 | Landslide XGBoost (spatial CV, SHAP) · flood/HAND · cloudburst · coastal erosion · storm surge · static MHI · zones | hazard columns + `model_runs` |
| 10–13 | Habitations (Census villages / building clusters), SVI, risk, priority + explanation JSON | habitation table |
| 13–16 | Candidate sites, MCDA suitability, carrying capacity, PuLP allocation; load all into PostGIS | sites + plans |

**Checkpoint at the end of Day 1:** all layers are in PostGIS and visually checked in QGIS. A seed dump has been exported.

### Day 2: API + dashboard + polish
| Hours | Task | Output |
|---|---|---|
| 16–20 | FastAPI endpoints, live-feed jobs (Open-Meteo, SACHET), dynamic zone engine, what-if simulator | Swagger working |
| 20–28 | Dashboard: map + layers, permanent/active toggle, AOI switcher, priority table, explain cards, site panel with capacity gauges, allocation flow lines, alerts panel | main UI |
| 28–32 | What-if sliders, PDF report, AI briefing, `/field` page, notifications | all features |
| 32–36 | Seed dataset + one-command demo, demo scenario buttons ("Cloudburst over Chamoli", "Cyclone landfall Kendrapara") | offline demo |
| 36–40 | Validation numbers (AUC, Satabhaya / Joshimath back-tests), README, architecture diagram | judge-ready |
| 40–48 | Buffer for bugs, presentation, rehearsal | — |

### MVP cut line
If time runs short, these are dropped in this order: SMS → Hindi briefing → `/field` photos → res-8/9 dual grid → storm-surge scenario.
The **must-haves** are: both AOIs' red zones, the priority list with explanations, sites with carrying capacity and allocation, the live rainfall update, and the PDF report.

### What you must do right away (before or while I scaffold)
1. Run `earthengine authenticate` once (I will give you the exact command) and tell me your **GEE Cloud project ID**.
2. *(Optional, in parallel)* Download the GSI Bhukosh landslide inventory for Uttarakhand (Chamoli and Rudraprayag) and place it in `data/raw/gsi/`.
3. *(Optional)* LLM API key, Twilio trial or Gmail app password, all placed in `.env` later. The app works without them.

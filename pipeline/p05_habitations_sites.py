"""Habitations (OSM settlements + WorldPop cells) -> vulnerability, risk, relocation priority.
Safe candidate sites -> suitability + carrying capacity. Writes {aoi}_habitations / {aoi}_sites parquet.
"""
import difflib
import json
import re
import unicodedata
import sys

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

from common import AOIS, H3_RES, PROC, ROOT

sys.path.insert(0, str(ROOT))
from backend.app.priority import prioritise  # noqa: E402

CELL_HA = h3.average_hexagon_area(H3_RES, "km^2") * 100
EARTH_KM = 6371.0


def pct(s):
    return s.rank(pct=True)


def add_clusters(places, populated):
    """Where OSM has no named settlement, add one point per populated H3 res-7 block (~5 km², village-sized)."""
    blocks = populated.assign(b=[h3.cell_to_parent(c, 7) for c in populated.h3]).groupby("b")["pop"].sum()
    blocks = blocks[blocks >= 300]
    ll = np.array([h3.cell_to_latlng(b) for b in blocks.index])
    tree = BallTree(np.radians(np.c_[places.geometry.y, places.geometry.x]), metric="haversine")
    d, i = tree.query(np.radians(ll), k=1)
    gap = d[:, 0] * EARTH_KM > 2
    if not gap.any():
        return places
    names = [f"Settlement cluster {n + 1} near {places.name[j]}" for n, j in enumerate(i[gap, 0])]
    extra = gpd.GeoDataFrame({"name": names, "place": "cluster"},
                             geometry=gpd.points_from_xy(ll[gap, 1], ll[gap, 0]), crs=places.crs)
    return pd.concat([places, extra], ignore_index=True)


CENSUS_COLS = ["pct_kutcha", "pct_dilapidated", "pct_scst", "pct_illiterate", "pct_child", "pct_marginal_workers",
               "pct_no_assets", "pct_no_electricity", "pct_open_defecation"]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", s)


def attach_census(key, h, populated):
    """Census 2011 village indicators per habitation + `census_source` saying how they were joined.

    od: cells -> DataMeet village polygons (exact), population-weighted over the habitation's cells; unnamed
        clusters take the name of their most populous census village.
    uk: no open village polygons -> OSM name matched to Census name within the district (exact, then fuzzy ≥ 0.88);
        unmatched habitations borrow the nearest matched habitation within 5 km, else the district average.
    """
    cen = pd.read_parquet(PROC / f"{key}_census.parquet")
    h = h.copy()
    h["census_source"] = None
    h["census_village"] = None
    dist_avg = {d: g[CENSUS_COLS].mul(g.tot_p, axis=0).sum() / g.tot_p.sum() for d, g in cen.groupby("district")}

    if key == "od":
        cen = gpd.read_parquet(PROC / f"{key}_census.parquet")
        pts = gpd.GeoDataFrame(populated[["h3", "hab", "pop"]], crs=4326,
                               geometry=gpd.points_from_xy(populated.lon, populated.lat))
        j = gpd.sjoin(pts, cen[cen.geometry.notna()], predicate="within", how="inner")
        j = j[j.hab.isin(h.index)]
        w = j["pop"].clip(lower=1e-6)
        vals = j[CENSUS_COLS].mul(w, axis=0).groupby(j.hab).sum().div(w.groupby(j.hab).sum(), axis=0)
        h.loc[vals.index, CENSUS_COLS] = vals.values
        h.loc[vals.index, "census_source"] = "village boundaries (exact)"
        top = j.sort_values("pop").groupby("hab").census_name.last()
        h.loc[top.index, "census_village"] = top.values
        clusters = h.place.eq("cluster") & h.census_village.notna()
        h.loc[clusters, "name"] = h.loc[clusters, "census_village"]
        dup = h.name.duplicated(keep=False)  # several clusters can sit in one big revenue village
        h.loc[dup, "name"] = h.loc[dup, "name"] + " (" + (h[dup].groupby("name").cumcount() + 1).astype(str) + ")"
    else:
        cen["key"] = cen.census_name.map(_norm)
        for i, r in h[h.place != "cluster"].iterrows():
            c = cen[cen.district == r.district]
            k = _norm(r["name"])
            hit = c[c.key == k]
            how = "village name match"
            if hit.empty:
                close = difflib.get_close_matches(k, c.key.tolist(), n=1, cutoff=0.88)
                hit, how = c[c.key == close[0]] if close else hit, "village name match (fuzzy)"
            if len(hit):
                best = hit.loc[hit.tot_p.idxmax()]  # same name twice in a district: take the larger village
                h.loc[i, CENSUS_COLS] = best[CENSUS_COLS].values
                h.loc[i, ["census_source", "census_village"]] = how, best.census_name
        matched = h[h.census_source.notna()]
        if len(matched):
            tree = BallTree(np.radians(matched[["lat", "lon"]]), metric="haversine")
            rest = h.census_source.isna()
            d, j = tree.query(np.radians(h.loc[rest, ["lat", "lon"]]), k=1)
            near = (d[:, 0] * EARTH_KM) <= 5
            idx = h.index[rest][near]
            src = matched.iloc[j[near, 0]]
            h.loc[idx, CENSUS_COLS] = src[CENSUS_COLS].values
            h.loc[idx, "census_source"] = "nearest matched village (≤ 5 km)"
            h.loc[idx, "census_village"] = src.census_village.values
    rest = h.census_source.isna()
    for i in h.index[rest]:
        h.loc[i, CENSUS_COLS] = dist_avg.get(h.district[i], pd.concat(dist_avg.values(), axis=1).mean(axis=1)).values
    h.loc[rest, "census_source"] = "district average"
    h[CENSUS_COLS] = h[CENSUS_COLS].astype(float)
    print(f"[{key}] census join:", h.census_source.value_counts().to_dict())
    return h


def habitations(key, cells):
    places = gpd.read_file(PROC / f"{key}_osm_places.geojson")
    places = places[places.name.notna()].drop_duplicates("name").reset_index(drop=True)
    populated = cells[cells["pop"] >= 1].copy()
    places = add_clusters(places, populated)
    tree = BallTree(np.radians(np.c_[places.geometry.y, places.geometry.x]), metric="haversine")
    d, i = tree.query(np.radians(populated[["lat", "lon"]]), k=1)
    populated["hab"] = i[:, 0]
    populated = populated[d[:, 0] * EARTH_KM <= 4]  # ponytail: straight-line 4 km catchment; village polygons later

    w = populated["pop"]
    populated["w"] = w
    zone_pop = populated.pivot_table(index="hab", columns="zone", values="pop", aggfunc="sum", fill_value=0)
    for z in ("red", "orange", "yellow", "green"):
        if z not in zone_pop:
            zone_pop[z] = 0

    def wavg(col):
        return (populated[col] * w).groupby(populated.hab).sum() / w.groupby(populated.hab).sum()

    h = pd.DataFrame({
        "pop": w.groupby(populated.hab).sum(),
        "n_cells": populated.groupby("hab").size(),
        "mhi": wavg("mhi"), "hz_max": populated.groupby("hab").hz_max.max(),
        "dist_health": wavg("dist_health"), "dist_road": wavg("dist_road"), "dist_school": wavg("dist_school"),
        "elev": wavg("elev"), "slope": wavg("slope"), "built_frac": wavg("built_frac"),
        "hist": populated.groupby("hab").hist_500m.max(),
        "district": populated.groupby("hab").district.agg(lambda s: s.mode().iat[0]),
        "cells": populated.groupby("hab").h3.agg(list),
    })
    for z in ("red", "orange"):
        h[f"frac_{z}"] = zone_pop[z].reindex(h.index).fillna(0) / h["pop"]
    if "dist_coast" in populated:
        h["dist_coast"] = wavg("dist_coast")
    dom = populated.sort_values("mhi").groupby("hab").hz_dominant.last()
    h["dominant_hazard"] = dom
    h = h[h["pop"] >= 20].copy()
    h["name"] = places.name.reindex(h.index).values
    h["place"] = places.place.reindex(h.index).values
    h["lat"], h["lon"] = places.geometry.y.reindex(h.index).values, places.geometry.x.reindex(h.index).values

    h = attach_census(key, h, populated)
    # Social Vulnerability Index (0–1): five equally weighted dimensions, each the mean percentile of its indicators.
    dims = {
        "housing": ["pct_kutcha", "pct_dilapidated"],
        "social": ["pct_scst", "pct_illiterate", "pct_child"],
        "economic": ["pct_marginal_workers", "pct_no_assets"],
        "services": ["pct_no_electricity", "pct_open_defecation"],
        "access": ["dist_health", "dist_road"],
    }
    for dim, cols in dims.items():
        h[f"svi_{dim}"] = sum(pct(h[c]) for c in cols) / len(cols)
    h["svi"] = sum(h[f"svi_{d}"] for d in dims) / len(dims)

    ev = pd.read_csv(ROOT / "data" / "seed" / "events.csv")
    ev = ev[ev.aoi == key]
    et = BallTree(np.radians(ev[["lat", "lon"]]), metric="haversine")
    dist_km, idx = et.query(np.radians(h[["lat", "lon"]]), k=1)
    near = ev.iloc[idx[:, 0]].reset_index(drop=True)
    h["nearest_event"] = near.name.values
    h["nearest_event_km"] = dist_km[:, 0] * EARTH_KM
    h["nearest_event_deaths"] = near.deaths.values
    h["nearest_event_year"] = pd.to_datetime(near.date).dt.year.values

    exposure = (np.log10(h["pop"] + 1) / np.log10(h["pop"].max() + 1)).clip(0, 1)
    h["risk"] = (h.mhi * (0.4 + 0.6 * exposure) * (0.5 + 0.5 * h.svi) * (1 + 0.1 * h["hist"].clip(upper=5))).clip(0, 1)
    h["risk"] = h.risk / h.risk.max()
    h = prioritise(h)
    h["hab_id"] = [f"{key}-{i}" for i in h.index]
    return h.reset_index(drop=True)


def components(cell_set):
    """Connected components of H3 cells (BFS over grid neighbours)."""
    seen, comps = set(), []
    for c in cell_set:
        if c in seen:
            continue
        stack, comp = [c], []
        seen.add(c)
        while stack:
            x = stack.pop()
            comp.append(x)
            for n in h3.grid_ring(x, 1):
                if n in cell_set and n not in seen:
                    seen.add(n)
                    stack.append(n)
        comps.append(comp)
    return comps


def sites(key, cells):
    c = cells.set_index("h3")
    ok = ((c.zone == "green") & (c.slope < 25) & (c.lc_tree < 0.6) & (c.lc_water + c.lc_snow + c.lc_wetland < 0.2)
          & (c.dist_road < 3000))
    if key == "od":
        ok &= c.dist_coast > 5000
    rows = []
    for comp in components(set(c.index[ok])):
        s = c.loc[comp]
        usable = (CELL_HA * (1 - s.lc_tree - s.lc_water - s.lc_built - s.lc_snow).clip(0, 1)).sum()
        existing = s["pop"].sum()
        # Land: 40% developable, 200 persons/ha (rural hill/plains housing norm with roads & open space).
        cap_land = usable * 0.4 * 200
        dr = s.dist_river.min()
        # Water @ 55 LPCD (JJM): perennial river < 1 km ≈ unconstrained at this scale; springs/borewells otherwise.
        cap_water = 15000 if dr < 1000 else 4000 if dr < 3000 else 1000
        dh, ds = s.dist_health.min(), s.dist_school.min()
        # Services (IPHS): PHC covers 20k in hills/30k plains; spare capacity assumed 25%.
        cap_services = 6000 if (dh < 5000 and ds < 3000) else 2500 if dh < 10000 else 1000
        cap = max(0, min(cap_land, cap_water, cap_services) - existing * 0.1)
        rows.append({
            "cells": comp, "n_cells": len(comp), "area_ha": len(comp) * CELL_HA, "usable_ha": usable,
            "lat": s.lat.mean(), "lon": s.lon.mean(), "district": s.district.mode().iat[0],
            "slope": s.slope.mean(), "mhi": s.mhi.mean(), "dist_road": s.dist_road.min(), "dist_river": dr,
            "dist_health": dh, "dist_school": ds, "elev": s.elev.mean(), "existing_pop": existing,
            "cap_land": cap_land, "cap_water": cap_water, "cap_services": cap_services, "capacity": int(cap),
            "limiting_factor": min((("land", cap_land), ("water", cap_water), ("services", cap_services)),
                                   key=lambda t: t[1])[0],
        })
    s = pd.DataFrame(rows)
    s = s[s.capacity >= 200].copy()
    s["suitability"] = (0.25 * (1 - (s.slope / 25).clip(0, 1)) + 0.2 * np.exp(-s.dist_road / 1000)
                        + 0.2 * np.exp(-s.dist_health / 5000) + 0.1 * np.exp(-s.dist_school / 3000)
                        + 0.15 * np.exp(-s.dist_river / 2000) + 0.1 * (1 - s.mhi / 0.35).clip(0, 1)).round(3)
    s = s.sort_values("suitability", ascending=False).head(400).reset_index(drop=True)
    s["site_id"] = [f"{key}-S{i + 1}" for i in range(len(s))]
    return s


def run(key):
    cells = pd.read_parquet(PROC / f"{key}_cells.parquet")
    h = habitations(key, cells)
    s = sites(key, cells)
    for df, name in ((h, "habitations"), (s, "sites")):
        df = df.copy()
        df["cells"] = df.cells.map(json.dumps)
        df.to_parquet(PROC / f"{key}_{name}.parquet")
    print(f"[{key}] habitations {len(h)} {h.priority.value_counts().to_dict()} | sites {len(s)}, "
          f"capacity {s.capacity.sum():,}")


if __name__ == "__main__":
    for k in sys.argv[1:] or list(AOIS):
        run(k)

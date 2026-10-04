"""Back-test Red Zones against real disasters -> backend/ml_artifacts/validation_{aoi}.json

Uses the *model-only* zone (history rule removed) so past events cannot flag themselves.
Headline: zone at the exact event location vs the Red/Orange share of surveyed/inhabited land (≤ 1.5 km
from a road, where events and GSI mapping happen). Secondary: Red/Orange within a radius vs random sites.
"""
import json
import sys

import h3
import numpy as np
import pandas as pd

from common import AOIS, PROC, ROOT

sys.path.insert(0, str(ROOT))
from backend.app.engine import WEIGHTS, classify  # noqa: E402

HAZARD_OF = {"landslide": "landslide", "cloudburst": "cloudburst", "flood": "flood", "coastal": "coastal",
             "cyclone": "surge"}  # subsidence / earthquake are not modelled
RING = {"site": 3, "approx": 6, "landfall": 6, "epicentre": 6}  # res-9 rings: 3 ≈ 1 km, 6 ≈ 2 km
HOT = {"red", "orange"}


def neighbourhood_hot(zone: pd.Series, k: int) -> pd.Series:
    """For every cell: does any cell within ring k sit in Red/Orange?"""
    hot = set(zone.index[zone.isin(HOT)])
    return pd.Series([any(n in hot for n in h3.grid_disk(c, k)) for c in zone.index], index=zone.index)


def run(key):
    cells = pd.read_parquet(PROC / f"{key}_cells.parquet").set_index("h3")
    model = classify(cells.drop(columns=["hist_500m"]), WEIGHTS[key])  # model-only zones
    zone = model.zone
    ev = pd.read_csv(ROOT / "data" / "seed" / "events.csv")
    ev = ev[ev.aoi == key]
    baseline = {k: float(neighbourhood_hot(zone, k).mean()) for k in sorted(set(RING.values()))}

    rows = []
    for e in ev.itertuples():
        k = RING[e.precision]
        c = h3.latlng_to_cell(e.lat, e.lon, 9)
        disk = [n for n in h3.grid_disk(c, k) if n in zone.index]
        hz = HAZARD_OF.get(e.type)
        row = {"name": e.name, "type": e.type, "date": e.date, "deaths": int(e.deaths), "lat": e.lat, "lon": e.lon,
               "precision": e.precision, "radius_km": round(k * 0.35, 1), "modelled": hz is not None,
               "in_aoi": bool(disk), "zone_at_point": zone.get(c), "zone_nearby": None, "hit": None,
               "hazard_score": None, "baseline": round(baseline[k], 3), "hit_point": None}
        if hz and c in zone.index:
            row["hit_point"] = zone[c] in HOT
        if disk:
            nz = zone.loc[disk]
            row["zone_nearby"] = max(nz, key=lambda z: ["green", "yellow", "orange", "red"].index(z))
            if hz and f"hz_{hz}" in model:
                row["hazard_score"] = round(float(model.loc[disk, f"hz_{hz}"].max()), 3)
            if hz:
                row["hit"] = row["zone_nearby"] in HOT
        rows.append(row)

    scored = [r for r in rows if r["hit"] is not None]
    point = [r for r in rows if r["hit_point"] is not None]
    surveyed = cells.dist_road < 1500
    out = {
        "aoi": key, "method": "model-only zones (event-history rule removed)",
        "events": rows,
        "events_point_scored": len(point), "events_point_hit": sum(r["hit_point"] for r in point),
        "surveyed_share_hot": round(float(zone[surveyed].isin(HOT).mean()), 3),
        "area_share_hot": round(float(zone.isin(HOT).mean()), 3),
        "events_scored": len(scored), "events_hit": sum(r["hit"] for r in scored),
        "baseline_hit_rate": round(float(np.mean([r["baseline"] for r in scored])), 3) if scored else None,
        "pop_share_hot": round(float(cells["pop"][zone.isin(HOT)].sum() / cells["pop"].sum()), 3),
    }
    if "ls_frac" in cells:  # GSI inventory vs other surveyed cells (unsurveyed land has no labels either way)
        ls = cells.ls_frac > 0
        dom = surveyed | ls
        hit, base = float(zone[ls].isin(HOT).mean()), float(zone[dom & ~ls].isin(HOT).mean())
        red, red_base = float((zone[ls] == "red").mean()), float((zone[dom & ~ls] == "red").mean())
        out["gsi"] = {"landslide_cells": int(ls.sum()), "share_in_hot": round(hit, 3), "surveyed_share_hot": round(base, 3),
                      "lift_hot": round(hit / base, 1), "share_in_red": round(red, 3),
                      "surveyed_share_red": round(red_base, 3), "lift_red": round(red / red_base, 1)}
    path = ROOT / "backend" / "ml_artifacts" / f"validation_{key}.json"
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"[{key}] at point {out['events_point_hit']}/{out['events_point_scored']} vs surveyed-land "
          f"{out['surveyed_share_hot']} | within radius {out['events_hit']}/{out['events_scored']} vs "
          f"{out['baseline_hit_rate']} | gsi {out.get('gsi')}")
    for r in rows:
        print(f"   {r['name'][:45]:45} {str(r['zone_at_point']):7} nearby={str(r['zone_nearby']):7} hit={r['hit']}")


if __name__ == "__main__":
    for k in sys.argv[1:] or list(AOIS):
        run(k)

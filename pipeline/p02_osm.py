"""Fetch roads, waterways, health/education facilities and settlements from OSM (Overpass) per AOI."""
import json
import sys
import time

import geopandas as gpd
import requests

from common import AOIS, PROC

OVERPASS = ["https://overpass-api.de/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
HEADERS = {"User-Agent": "SURAKSHA-SIH/0.1 (disaster-risk research)", "Accept": "*/*"}
QUERIES = {
    "roads": 'way["highway"~"^(trunk|primary|secondary|tertiary|unclassified|residential|track)$"]',
    "waterways": 'way["waterway"~"^(river|stream|canal)$"]',
    "health": 'nwr["amenity"~"^(hospital|clinic|doctors)$"]',
    "schools": 'nwr["amenity"~"^(school|college)$"]',
    "places": 'node["place"~"^(city|town|village|hamlet)$"]',
}


def to_features(elements):
    feats = []
    for e in elements:
        if e["type"] == "node":
            geom = {"type": "Point", "coordinates": [e["lon"], e["lat"]]}
        elif "geometry" in e:
            coords = [[p["lon"], p["lat"]] for p in e["geometry"]]
            geom = {"type": "LineString", "coordinates": coords} if len(coords) > 1 else None
        elif "center" in e:
            geom = {"type": "Point", "coordinates": [e["center"]["lon"], e["center"]["lat"]]}
        else:
            geom = None
        if geom:
            t = e.get("tags", {})
            feats.append({"type": "Feature", "geometry": geom,
                          "properties": {"osm_id": e["id"], "name": t.get("name"), **{k: t.get(k) for k in
                                         ("highway", "waterway", "amenity", "place", "population")}}})
    return feats


def fetch(key):
    d = gpd.read_file(PROC / f"{key}_districts.geojson")
    w, s, e, n = d.buffer(0.03).total_bounds  # ~3 km in degrees
    for name, q in QUERIES.items():
        out = PROC / f"{key}_osm_{name}.geojson"
        if out.exists():
            continue
        body = f"[out:json][timeout:180];({q}({s},{w},{n},{e}););out geom;" if name in ("roads", "waterways") \
            else f"[out:json][timeout:180];({q}({s},{w},{n},{e}););out center;"
        for attempt in range(4):
            try:
                r = requests.post(OVERPASS[attempt % 2], data={"data": body}, headers=HEADERS, timeout=300)
                if r.ok:
                    break
                print(f"  {OVERPASS[attempt % 2]} -> {r.status_code}", flush=True)
            except requests.RequestException as e:
                print(f"  {OVERPASS[attempt % 2]} -> {e.__class__.__name__}", flush=True)
            time.sleep(15 * (attempt + 1))
        r.raise_for_status()
        feats = to_features(r.json()["elements"])
        out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
        print(f"[{key}] {name}: {len(feats)}", flush=True)


if __name__ == "__main__":
    for k in sys.argv[1:] or list(AOIS):
        fetch(k)

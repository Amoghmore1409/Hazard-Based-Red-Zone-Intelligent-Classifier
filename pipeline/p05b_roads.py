"""Road-network distances habitation -> candidate site, from the OSM road graph (scipy shortest paths).

Writes data/processed/{aoi}_road_dist.parquet (loaded into PostGIS by p06_load.py). Run after p05.
road_km = off-road access (straight-line x 1.3) + network distance + off-road egress at the site.
"""
import json
import sys

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from sklearn.neighbors import BallTree

from common import AOIS, PROC, ROOT

sys.path.insert(0, str(ROOT))
R = 6371000.0
MAX_ROAD_KM = 80     # search radius per site
STITCH_M = 150       # join dangling OSM fragments to the main network within this gap
OFFROAD = 1.3        # walking/track detour factor for the snap legs


def hav(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def build_graph(key):
    feats = json.loads((PROC / f"{key}_osm_roads.geojson").read_text())["features"]
    idx, coords, src, dst = {}, [], [], []
    for f in feats:
        prev = None
        for lon, lat in f["geometry"]["coordinates"]:
            k = (round(lon, 7), round(lat, 7))  # shared OSM nodes have identical coordinates
            if k not in idx:
                idx[k] = len(coords)
                coords.append((lat, lon))
            i = idx[k]
            if prev is not None and prev != i:
                src.append(prev)
                dst.append(i)
            prev = i
    xy = np.array(coords)
    src, dst = np.array(src), np.array(dst)
    w = hav(xy[src, 0], xy[src, 1], xy[dst, 0], xy[dst, 1])

    n = len(xy)
    g = coo_matrix((w, (src, dst)), shape=(n, n)).tocsr()
    ncomp, lab = connected_components(g, directed=False)
    main = np.bincount(lab).argmax()
    # Stitch: every node outside the main component links to its nearest main-component node if close enough.
    off = np.where(lab != main)[0]
    on = np.where(lab == main)[0]
    tree = BallTree(np.radians(xy[on]), metric="haversine")
    d, j = tree.query(np.radians(xy[off]), k=1)
    d = d[:, 0] * R
    ok = d <= STITCH_M
    src = np.concatenate([src, off[ok]])
    dst = np.concatenate([dst, on[j[ok, 0]]])
    w = np.concatenate([w, d[ok] * OFFROAD])
    g = coo_matrix((w, (src, dst)), shape=(n, n)).tocsr()
    ncomp2, lab2 = connected_components(g, directed=False)
    share = np.bincount(lab2).max() / n
    print(f"[{key}] nodes {n:,} edges {len(w):,} | components {ncomp} -> {ncomp2} after stitching, "
          f"main component {share:.1%} of nodes", flush=True)
    return g, xy, lab2, np.bincount(lab2).argmax()


def snap(xy, lab, main, lat, lon):
    """Nearest node on the main connected network, and off-road distance to it (m)."""
    on = np.where(lab == main)[0]
    d, j = BallTree(np.radians(xy[on]), metric="haversine").query(np.radians(np.c_[lat, lon]), k=1)
    return on[j[:, 0]], d[:, 0] * R


def run(key):
    g, xy, lab, main = build_graph(key)
    habs = pd.read_parquet(PROC / f"{key}_habitations.parquet")
    sites = pd.read_parquet(PROC / f"{key}_sites.parquet")
    hn, hd = snap(xy, lab, main, habs.lat.values, habs.lon.values)
    sn, sd = snap(xy, lab, main, sites.lat.values, sites.lon.values)

    # One Dijkstra per site (sites << habitations), limited to MAX_ROAD_KM.
    dist = dijkstra(g, directed=False, indices=sn, limit=MAX_ROAD_KM * 1000)
    net = dist[:, hn]  # sites x habitations, metres (inf = beyond limit)
    rows = []
    for si in range(len(sites)):
        reach = np.isfinite(net[si])
        for hi in np.where(reach)[0]:
            km = (net[si, hi] + OFFROAD * (hd[hi] + sd[si])) / 1000
            if km <= MAX_ROAD_KM:
                rows.append((habs.hab_id.iat[hi], sites.site_id.iat[si], round(km, 2)))
    rd = pd.DataFrame(rows, columns=["hab_id", "site_id", "road_km"])
    straight = hav(habs.set_index("hab_id").loc[rd.hab_id, "lat"].values, habs.set_index("hab_id").loc[rd.hab_id, "lon"].values,
                   sites.set_index("site_id").loc[rd.site_id, "lat"].values, sites.set_index("site_id").loc[rd.site_id, "lon"].values) / 1000
    rd["straight_km"] = straight.round(2)
    rd = rd.assign(aoi=key)
    rd.to_parquet(PROC / f"{key}_road_dist.parquet")
    pd.DataFrame({"hab_id": habs.hab_id, "snap_m": hd.round(0)}).to_parquet(PROC / f"{key}_hab_snap.parquet")
    ratio = (rd.road_km / rd.straight_km.clip(lower=0.5)).median()
    reached = rd.hab_id.nunique()
    print(f"[{key}] pairs {len(rd):,} | habitations with ≥1 site by road: {reached}/{len(habs)} | "
          f"median road/straight ratio {ratio:.2f} | median village snap {np.median(hd):.0f} m", flush=True)
    return rd


if __name__ == "__main__":
    for k in sys.argv[1:] or list(AOIS):
        run(k)

"""Relocation allocation: assign whole habitations to safe sites (MILP, PuLP/CBC).

min Σ km·pop·y + penalty·unassigned, s.t. each habitation -> at most one site, site capacity respected.
Immediate cases get a 10x larger unassigned penalty so they are served first. Communities are never split.
Distances are by road (OSM network, pipeline/p08_roads.py) when available, else straight-line.
"""
import numpy as np
import pandas as pd
import pulp
from sklearn.neighbors import BallTree

PENALTY = {"immediate": 1e7, "short": 1e6, "medium": 1e5}
K_NEAREST = 20         # candidate sites considered per habitation
MAX_ROAD_KM = 60       # longest acceptable relocation trip by road
MAX_STRAIGHT_KM = 40   # fallback when no road distances are loaded
OTHER_DISTRICT = 1.5   # distance multiplier when moving across district lines


def _pairs(h: pd.DataFrame, s: pd.DataFrame, road: pd.DataFrame | None) -> pd.DataFrame:
    """Candidate (habitation row i, site row j, km, by_road) pairs: K nearest sites per habitation."""
    if road is not None and len(road):
        p = road.merge(h[["hab_id"]].reset_index(names="i"), on="hab_id").merge(
            s[["site_id"]].reset_index(names="j"), on="site_id")
        p = p[p.road_km <= MAX_ROAD_KM].rename(columns={"road_km": "km"}).assign(by_road=True)
        return p.sort_values("km").groupby("i").head(K_NEAREST)[["i", "j", "km", "by_road"]]
    d, idx = BallTree(np.radians(s[["lat", "lon"]]), metric="haversine").query(
        np.radians(h[["lat", "lon"]]), k=min(K_NEAREST, len(s)))
    p = pd.DataFrame({"i": np.repeat(h.index, idx.shape[1]), "j": idx.ravel(), "km": d.ravel() * 6371.0})
    return p[p.km <= MAX_STRAIGHT_KM].assign(by_road=False)


def allocate(habs: pd.DataFrame, sites: pd.DataFrame, priorities=("immediate", "short"),
             road: pd.DataFrame | None = None) -> pd.DataFrame:
    h = habs[habs.priority.isin(priorities)].reset_index(drop=True)
    if h.empty or sites.empty:
        return pd.DataFrame(columns=["hab_id", "site_id", "persons", "distance_km", "by_road", "priority"])
    s = sites.reset_index(drop=True)
    pairs = _pairs(h, s, road)

    prob = pulp.LpProblem("relocation", pulp.LpMinimize)
    u = {i: pulp.LpVariable(f"u{i}", cat="Binary") for i in h.index}
    y = {(p.i, p.j): pulp.LpVariable(f"y{p.i}_{p.j}", cat="Binary") for p in pairs.itertuples()}
    km = {(p.i, p.j): p.km for p in pairs.itertuples()}
    prob += pulp.lpSum(PENALTY[h.priority[i]] * u[i] for i in h.index) + pulp.lpSum(
        k * (OTHER_DISTRICT if s.district[j] != h.district[i] else 1) * h["pop"][i] * y[i, j] for (i, j), k in km.items())
    by_h, by_s = {}, {}
    for (i, j), v in y.items():
        by_h.setdefault(i, []).append(v)
        by_s.setdefault(j, []).append(h["pop"][i] * v)
    for i in h.index:
        prob += pulp.lpSum(by_h.get(i, [])) + u[i] == 1
    for j, terms in by_s.items():
        prob += pulp.lpSum(terms) <= int(s.capacity[j])
    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=20, gapRel=0.01))  # near-optimal fast enough for the UI

    by_road = bool(pairs.by_road.any()) if len(pairs) else False
    rows = [{"hab_id": h.hab_id[i], "site_id": s.site_id[j], "persons": int(round(h["pop"][i])),
             "distance_km": round(float(km[i, j]), 1), "by_road": by_road, "priority": h.priority[i]}
            for (i, j), v in y.items() if v.value() and v.value() > 0.5]
    rows += [{"hab_id": h.hab_id[i], "site_id": None, "persons": int(round(h["pop"][i])), "distance_km": None,
              "by_road": by_road, "priority": h.priority[i]} for i, v in u.items() if v.value() and v.value() > 0.5]
    return pd.DataFrame(rows)


if __name__ == "__main__":
    habs = pd.DataFrame({"hab_id": ["a", "b", "c"], "priority": ["immediate", "short", "immediate"],
                         "pop": [500, 400, 300], "lat": [30.0, 30.01, 30.5], "lon": [79.0, 79.0, 79.5],
                         "district": ["X"] * 3})
    sites = pd.DataFrame({"site_id": ["s1", "s2"], "lat": [30.02, 30.45], "lon": [79.01, 79.5],
                          "capacity": [600, 1000], "district": ["X", "X"]})
    out = allocate(habs, sites).set_index("hab_id")
    assert out.site_id["a"] == "s1" and out.site_id["c"] == "s2", out
    assert pd.isna(out.site_id["b"]), out  # s1 full after a, s2 > 40 km straight-line
    # By road: b can reach s2 within 60 km road, a only s1.
    road = pd.DataFrame({"hab_id": ["a", "b", "c", "b"], "site_id": ["s1", "s1", "s2", "s2"], "road_km": [3, 3, 9, 55]})
    out = allocate(habs, sites, road=road).set_index("hab_id")
    assert out.site_id["a"] == "s1" and out.site_id["b"] == "s2" and out.distance_km["b"] == 55 and out.by_road.all(), out
    print("allocate ok\n", out)

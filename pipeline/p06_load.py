"""Load processed parquet outputs into PostGIS (replaces tables) and run the baseline allocation."""
import json
import sys

import geopandas as gpd
import h3
import pandas as pd
from shapely.geometry import Point, Polygon
from sqlalchemy import create_engine, text

from common import AOIS, PROC, ROOT

sys.path.insert(0, str(ROOT))
from backend.app.allocate import allocate  # noqa: E402
from backend.app.db import DB_URL, SCHEMA  # noqa: E402

CELL_COLS = ["h3", "district", "lat", "lon", "elev", "slope", "hand", "ndvi", "pop", "dist_road", "dist_river",
             "dist_health", "hist_500m", "mhi", "hz_max", "hz_dominant", "zone", "ls_factors",
             "hz_landslide", "hz_flood", "hz_cloudburst", "hz_coastal", "hz_surge", "jrc_occ", "rain_p99",
             "dist_coast", "erosion_density"]


def hexagon(c):
    return Polygon([(lon, lat) for lat, lon in h3.cell_to_boundary(c)])


def main():
    eng = create_engine(DB_URL)
    with eng.begin() as con:
        con.execute(text(SCHEMA))
    cells_all, habs_all, sites_all, plans, roads_all = [], [], [], [], []
    for key in AOIS:
        if not (PROC / f"{key}_cells.parquet").exists():
            print(f"skip {key}: not processed")
            continue
        c = pd.read_parquet(PROC / f"{key}_cells.parquet")
        c = c[[col for col in CELL_COLS if col in c]].assign(aoi=key)
        cells_all.append(gpd.GeoDataFrame(c, geometry=[hexagon(x) for x in c.h3], crs=4326))
        h = pd.read_parquet(PROC / f"{key}_habitations.parquet").assign(aoi=key)
        s = pd.read_parquet(PROC / f"{key}_sites.parquet").assign(aoi=key)
        rp = PROC / f"{key}_road_dist.parquet"
        road = pd.read_parquet(rp) if rp.exists() else None
        if road is not None:
            roads_all.append(road)
        plans.append(allocate(h, s, road=road).assign(aoi=key))
        h["reasons"] = h.reasons.map(lambda r: json.dumps(list(r)))
        habs_all.append(gpd.GeoDataFrame(h, geometry=[Point(xy) for xy in zip(h.lon, h.lat)], crs=4326))
        s_geom = [Polygon(hexagon(json.loads(cs)[0])) if len(json.loads(cs)) == 1
                  else gpd.GeoSeries([hexagon(x) for x in json.loads(cs)]).union_all() for cs in s.cells]
        sites_all.append(gpd.GeoDataFrame(s, geometry=s_geom, crs=4326))
        d = gpd.read_file(PROC / f"{key}_districts.geojson")[["shapeName", "geometry"]].rename(columns={"shapeName": "name"})
        d.assign(aoi=key).to_postgis("districts", eng, if_exists="append" if key != list(AOIS)[0] else "replace")

    pd.concat(cells_all).pipe(gpd.GeoDataFrame).to_postgis("h3_cells", eng, if_exists="replace", index=False)
    pd.concat(habs_all).pipe(gpd.GeoDataFrame).to_postgis("habitations", eng, if_exists="replace", index=False)
    pd.concat(sites_all).pipe(gpd.GeoDataFrame).to_postgis("candidate_sites", eng, if_exists="replace", index=False)
    if roads_all:
        pd.concat(roads_all).to_sql("road_distances", eng, if_exists="replace", index=False, chunksize=20000)
    plan = pd.concat(plans)
    plan.assign(scenario="baseline").to_sql("relocation_plans", eng, if_exists="replace", index=False)
    ev = pd.read_csv(ROOT / "data" / "seed" / "events.csv")
    gpd.GeoDataFrame(ev, geometry=[Point(xy) for xy in zip(ev.lon, ev.lat)], crs=4326) \
        .to_postgis("hazard_events", eng, if_exists="replace", index=False)
    with eng.begin() as con:
        con.execute(text("CREATE INDEX IF NOT EXISTS h3_cells_aoi ON h3_cells(aoi); "
                         "CREATE INDEX IF NOT EXISTS hab_aoi ON habitations(aoi);"))
    print("loaded; plan rows:", len(plan), "assigned:", plan.site_id.notna().sum())


if __name__ == "__main__":
    main()

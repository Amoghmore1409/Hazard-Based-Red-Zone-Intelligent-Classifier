"""Raster stack + OSM + GSI inventory -> per-H3-cell feature table (data/processed/{aoi}_features.parquet)."""
import sys
import warnings

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.features import rasterize
from scipy import ndimage

from common import AOIS, H3_RES, PROC, RAW

warnings.filterwarnings("ignore")
BANDS = ["elev", "slope", "aspect", "tpi", "hand", "upa", "twi", "lulc", "ndvi", "jrc_occ", "pop_km2", "built_frac",
         "heavy_days", "rain_annual", "rain_p99", "clay", "water_1990", "water_2024"]
LULC = {10: "tree", 20: "shrub", 30: "grass", 40: "crop", 50: "built", 60: "bare", 70: "snow",
        80: "water", 90: "wetland", 95: "mangrove", 100: "moss"}


def burn(gdf, shape, transform, crs, all_touched=True):
    if gdf is None or gdf.empty:
        return np.zeros(shape, bool)
    geoms = gdf.to_crs(crs).geometry
    return rasterize(((g, 1) for g in geoms if g is not None and not g.is_empty), out_shape=shape,
                     transform=transform, all_touched=all_touched, dtype="uint8").astype(bool)


def dist_to(mask, px):
    """Distance in metres from every pixel to the nearest True pixel."""
    if not mask.any():
        return np.full(mask.shape, 1e5, "float32")
    return (ndimage.distance_transform_edt(~mask) * px).astype("float32")


def osm(key, name):
    p = PROC / f"{key}_osm_{name}.geojson"
    return gpd.read_file(p) if p.exists() else None


def build(key):
    cfg = AOIS[key]
    with rasterio.open(PROC / f"{key}_stack.tif") as src:
        arr = src.read().astype("float32")
        names = list(src.descriptions)
        if None in names:  # some geedim downloads drop descriptions; fall back to p01 export order
            names = BANDS[:src.count]
        tr, crs, shape = src.transform, src.crs, (src.height, src.width)
    px = abs(tr.a)
    b = {n: arr[i] for i, n in enumerate(names)}

    districts = gpd.read_file(PROC / f"{key}_districts.geojson")
    dist_idx = rasterize(((g, i + 1) for i, g in enumerate(districts.to_crs(crs).geometry)),
                         out_shape=shape, transform=tr, dtype="uint8")
    inside = dist_idx > 0

    roads, rivers = osm(key, "roads"), osm(key, "waterways")
    motor = roads[roads.highway != "track"] if roads is not None else None
    f = {
        "elev": b["elev"], "slope": b["slope"], "tpi": b["tpi"], "hand": b["hand"], "upa": b["upa"],
        "twi": b["twi"], "ndvi": b["ndvi"], "jrc_occ": b["jrc_occ"], "pop_km2": b["pop_km2"],
        "built_frac": b["built_frac"], "heavy_days": b["heavy_days"], "rain_annual": b["rain_annual"],
        "rain_p99": b["rain_p99"], "clay": b["clay"],
        "aspect_sin": np.sin(np.radians(b["aspect"])), "aspect_cos": np.cos(np.radians(b["aspect"])),
        "dist_road": dist_to(burn(motor, shape, tr, crs), px),
        "dist_river": dist_to(burn(rivers, shape, tr, crs) | (b["upa"] > 50), px),
        "dist_health": dist_to(burn(osm(key, "health"), shape, tr, crs), px),
        "dist_school": dist_to(burn(osm(key, "schools"), shape, tr, crs), px),
    }
    for code, nm in LULC.items():
        f[f"lc_{nm}"] = (b["lulc"] == code).astype("float32")

    gsi = RAW / "gsi" / "landslide_polygon.shp"
    if key == "uk" and gsi.exists():
        f["ls_frac"] = burn(gpd.read_file(gsi, encoding="ISO-8859-1"), shape, tr, crs).astype("float32")

    if cfg["kind"] == "coastal":
        w90, w24 = b["water_1990"] > 0.5, b["water_2024"] > 0.5
        # Open water ≥ ~1 km wide (rivers/creeks removed), connected to the east/south raster edge = sea.
        wide = ndimage.binary_opening(w24, iterations=int(500 / px))
        lab, _ = ndimage.label(wide)
        edge = np.unique(np.concatenate([lab[:, -1], lab[-1, :]]))
        edge = edge[edge > 0]
        sea = np.isin(lab, edge[np.argmax(ndimage.sum(wide, lab, edge))]) if len(edge) else wide
        f["dist_coast"] = dist_to(sea, px)
        erosion = (~w90) & w24 & (f["dist_coast"] < 3000)  # shoreline retreat, not river-bank migration
        win = int(2000 / px)
        f["erosion_density"] = ndimage.uniform_filter(erosion.astype("float32"), win)
        f["accretion_density"] = ndimage.uniform_filter((w90 & ~w24).astype("float32"), win)

    # Pixel centres -> lat/lon -> H3
    rows, cols = np.nonzero(inside)
    xs, ys = rasterio.transform.xy(tr, rows, cols)
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(np.asarray(xs), np.asarray(ys))
    cells = [h3.latlng_to_cell(a, o, H3_RES) for a, o in zip(lat, lon)]

    df = pd.DataFrame({k: np.where(np.isfinite(v[rows, cols]), v[rows, cols], np.nan) for k, v in f.items()})
    df["h3"] = cells
    df["district"] = districts.shapeName.values[dist_idx[rows, cols] - 1]
    g = df.groupby("h3").agg(**{k: (k, "mean") for k in f}, slope_max=("slope", "max"),
                             district=("district", lambda s: s.mode().iat[0]))
    zero_fill = ["pop_km2", "built_frac", "jrc_occ", "ls_frac", "erosion_density", "accretion_density"]
    g = g.fillna({c: 0 for c in zero_fill if c in g}).fillna(g.median(numeric_only=True))  # nodata (clouds, gaps)
    area_km2 = h3.average_hexagon_area(H3_RES, "km^2")
    g["pop"] = g.pop_km2 * area_km2
    g["lat"], g["lon"] = zip(*[h3.cell_to_latlng(c) for c in g.index])
    g = g.reset_index()
    g.to_parquet(PROC / f"{key}_features.parquet")
    print(f"[{key}] {len(g)} cells, pop={g['pop'].sum():,.0f}", flush=True)
    return g


if __name__ == "__main__":
    for k in sys.argv[1:] or list(AOIS):
        build(k)

"""Export one multi-band GeoTIFF per AOI from Google Earth Engine + district boundaries.

Usage: python pipeline/p01_gee_export.py [uk|od ...]
"""
import json
import sys
import warnings

import geedim  # noqa: F401  (registers the .gd accessor)

from common import AOIS, PROC, ee_init

warnings.filterwarnings("ignore")
ee = ee_init()


def districts_fc(names):
    return ee.FeatureCollection("WM/geoLab/geoBoundaries/600/ADM2").filter(ee.Filter.inList("shapeName", names))


def water_mask(start, end, region):
    """MNDWI water mask from a Landsat C2 L2 median composite."""
    def prep(img):
        sr = img.select("SR_B.*").multiply(0.0000275).add(-0.2)
        return sr.updateMask(img.select("QA_PIXEL").bitwiseAnd(0b11000).eq(0))
    col = ee.ImageCollection("LANDSAT/LT05/C02/T1_L2")
    if start >= "2013":
        col = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2").merge(ee.ImageCollection("LANDSAT/LC09/C02/T1_L2"))
        green, swir = "SR_B3", "SR_B6"
    else:
        green, swir = "SR_B2", "SR_B5"
    med = col.filterBounds(region).filterDate(start, end).map(prep).median()
    return med.normalizedDifference([green, swir]).gt(0).unmask(0)


def build_stack(region, kind):
    glo = ee.ImageCollection("COPERNICUS/DEM/GLO30").filterBounds(region)
    dem = glo.select("DEM").mosaic().setDefaultProjection(glo.first().select("DEM").projection()).rename("elev")
    terrain = ee.Terrain.products(dem)
    slope = terrain.select("slope")
    tpi = dem.subtract(dem.focalMean(300, "circle", "meters")).rename("tpi")

    merit = ee.Image("MERIT/Hydro/v1_0_1")
    hand = merit.select("hnd").rename("hand")
    upa = merit.select("upa").rename("upa")  # km² upstream
    twi = upa.multiply(1e6).divide(slope.multiply(3.14159 / 180).tan().max(0.001)).log().rename("twi")

    lulc = ee.Image(ee.ImageCollection("ESA/WorldCover/v200").first()).rename("lulc")
    ndvi = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(region)
            .filterDate("2023-10-01", "2024-03-31").filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
            .median().normalizedDifference(["B8", "B4"]).rename("ndvi"))
    jrc = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").unmask(0).rename("jrc_occ")

    # Density layers (per km²) so resampling to 60 m keeps them meaningful.
    pop_col = (ee.ImageCollection("WorldPop/GP/100m/pop").filter(ee.Filter.eq("country", "IND"))
               .filter(ee.Filter.eq("year", 2020)))
    # pixelArea must be taken at the source's native 100 m grid (mosaic() drops it), not the 60 m export grid.
    pop_density = (pop_col.mosaic().divide(ee.Image.pixelArea().reproject(pop_col.first().projection()))
                   .multiply(1e6).rename("pop_km2"))
    built = ee.Image("JRC/GHSL/P2023A/GHS_BUILT_S/2020").select("built_surface")
    built_frac = built.divide(ee.Image.pixelArea().reproject(built.projection())).rename("built_frac")

    chirps = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").filterDate("2000-01-01", "2024-01-01").filterBounds(region)
    native = chirps.first().projection()  # ~5.5 km; compute climatology there, not at 60 m
    heavy_days = chirps.map(lambda i: i.gt(50)).sum().divide(24).reproject(native).rename("heavy_days")
    rain_annual = chirps.sum().divide(24).reproject(native).rename("rain_annual")
    monsoon = chirps.filter(ee.Filter.calendarRange(6, 9, "month"))
    rain_p99 = monsoon.reduce(ee.Reducer.percentile([99])).reproject(native).rename("rain_p99")

    clay = ee.Image("projects/soilgrids-isric/clay_mean").select("clay_0-5cm_mean").rename("clay")

    stack = ee.Image.cat([dem, slope, terrain.select("aspect"), tpi, hand, upa, twi, lulc, ndvi, jrc,
                          pop_density, built_frac, heavy_days, rain_annual, rain_p99, clay])
    if kind == "coastal":
        stack = stack.addBands(water_mask("1988-01-01", "1992-12-31", region).rename("water_1990"))
        stack = stack.addBands(water_mask("2022-01-01", "2024-12-31", region).rename("water_2024"))
    return stack.toFloat()


def main(keys):
    for key in keys:
        cfg = AOIS[key]
        fc = districts_fc(cfg["districts"])
        geojson = fc.getInfo()
        (PROC / f"{key}_districts.geojson").write_text(json.dumps(geojson))
        region = fc.geometry().buffer(3000).bounds()
        out = PROC / f"{key}_stack.tif"
        print(f"[{key}] exporting {out.name} at {cfg['scale']} m ...", flush=True)
        img = build_stack(region, cfg["kind"]).clip(region)
        img.gd.prepareForExport(crs=cfg["crs"], region=region, scale=cfg["scale"], dtype="float32") \
            .gd.toGeoTIFF(out, overwrite=True, max_tile_size=1, max_requests=8)
        print(f"[{key}] done", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:] or list(AOIS))

"""Census 2011 village indicators (PCA + Houselisting HH-14) -> data/processed/{aoi}_census.parquet

Source: Office of the Registrar General & Census Commissioner, India, Census 2011 (censusindia.gov.in NADA).
Kendrapara village polygons: DataMeet indian_village_boundaries (ODbL), linked via 2001->2011 code table.
Indicators are shares (0-1) of people or households in each village.
"""
import sys

import geopandas as gpd
import pandas as pd

from common import PROC, RAW

C = RAW / "census"
FILES = {  # aoi -> [(pca file, hh-14 file)]
    "uk": [("PCA_TV_Chamoli_DDW_PCA0502_2011.xlsx", "HLPCA-05057-2011_H14_Chamoli.xlsx"),
           ("PCA_TV_Rudraprayag_DDW_PCA0503_2011.xlsx", "HLPCA-05058-2011_H14_Rudraprayag.xlsx")],
    "od": [("PCA_TV_Kendrapara_DDW_PCA2110_2011.xlsx", "HLPCA-21379-2011_H14_Kendrapara.xlsx")],
}
# HH-14 column positions (1-based) -> indicator, all "% of households"
HH = {14: "pct_dilapidated", 85: "pct_electricity", 102: "pct_open_defecation", 139: "pct_no_assets",
      141: "pct_semi_permanent", 142: "pct_temporary"}


def pca(path):
    p = pd.read_excel(C / path)
    sub = p[(p.Level == "SUB-DISTRICT") & (p.TRU == "Total")].set_index("Subdistt").Name
    v = p[(p.Level == "VILLAGE") & (p.TOT_P > 0)].copy()
    out = pd.DataFrame({
        "village_code": v["Town/Village"].astype(int).astype(str).str.zfill(6),
        "census_name": v.Name.str.strip(), "district": p.Name.iat[0].strip(),
        "subdistrict": v.Subdistt.map(sub).str.strip(), "tot_p": v.TOT_P, "no_hh": v.No_HH,
        "pct_scst": (v.P_SC + v.P_ST) / v.TOT_P, "pct_child": v.P_06 / v.TOT_P,
        "pct_illiterate": v.P_ILL / v.TOT_P, "pct_marginal_workers": v.MARGWORK_P / v.TOT_P,
        "pct_non_workers": v.NON_WORK_P / v.TOT_P,
    })
    return out


def hh14(path):
    h = pd.read_excel(C / path, sheet_name="Sheet1", header=None).iloc[7:]
    h = h[(h[6].astype(str).str.zfill(6) != "000000") & (h[9].astype(str).str.strip() == "Rural")]
    out = pd.DataFrame({"village_code": h[6].astype(int).astype(str).str.zfill(6)})
    for col, name in HH.items():
        out[name] = pd.to_numeric(h[col - 1], errors="coerce").values / 100
    out["pct_kutcha"] = out.pct_semi_permanent + out.pct_temporary  # non-permanent structures
    out["pct_no_electricity"] = 1 - out.pct_electricity
    return out.drop(columns=["pct_electricity"])


def polygons_od(codes: pd.Series) -> gpd.GeoDataFrame:
    d = C / "datameet_village_boundaries_odisha"
    keys = pd.read_csv(d / "or.csv.csv", dtype=str)
    keys["village_code"] = keys.village_code_2011.str.zfill(6)
    keys = keys[keys.village_code.isin(codes)]
    bbox = (86.1, 20.15, 87.2, 20.95)  # Kendrapara + margin
    g = pd.concat([gpd.read_file(d / f, bbox=bbox) for f in ("or1.geojson", "or2.geojson")])
    g = g[g.CEN_2001.isin(keys.CEN_2001)].merge(keys[["CEN_2001", "village_code"]], on="CEN_2001")
    return g[["village_code", "geometry"]].dissolve("village_code").reset_index()


def run(key):
    v = pd.concat([pca(p).merge(hh14(h), on="village_code", how="left") for p, h in FILES[key]])
    if key == "od":
        poly = polygons_od(v.village_code)
        v = gpd.GeoDataFrame(v.merge(poly, on="village_code", how="left"), geometry="geometry", crs=4326)
        print(f"[od] villages with polygons: {v.geometry.notna().sum()}/{len(v)}")
        v.to_parquet(PROC / f"{key}_census.parquet")
    else:
        v.to_parquet(PROC / f"{key}_census.parquet")
    hh_ok = v.pct_kutcha.notna().mean()
    print(f"[{key}] {len(v)} inhabited villages, {int(v.tot_p.sum()):,} people; housing data for {hh_ok:.0%}")
    print(v.drop(columns=["geometry"], errors="ignore").describe().loc[["mean", "max"]].round(3).to_string())


if __name__ == "__main__":
    for k in sys.argv[1:] or list(FILES):
        run(k)

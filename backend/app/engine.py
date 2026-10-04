"""Hazard combination, Red Zone classification and dynamic triggers.

Shared by the offline pipeline and the live API so the same rules apply everywhere.
"""
import numpy as np
import pandas as pd

# AHP-derived default weights (editable from the dashboard what-if panel).
WEIGHTS = {
    "uk": {"landslide": 0.5, "flood": 0.25, "cloudburst": 0.25},
    "od": {"flood": 0.35, "coastal": 0.35, "surge": 0.30},
}
# zone: (min MHI, min single-hazard) — any condition met puts the cell in that zone.
THRESHOLDS = {"red": (0.75, 0.90), "orange": (0.55, 0.75), "yellow": (0.35, 0.50)}
HIST_RED = 3  # ≥ 3 recorded events within ~350 m (recurring failure) -> red regardless of model score
ZONE_RANK = {"green": 0, "yellow": 1, "orange": 2, "red": 3}


def mhi(df: pd.DataFrame, weights: dict) -> pd.Series:
    w = pd.Series(weights, dtype=float)
    w = w / w.sum()
    return sum(df[f"hz_{h}"] * v for h, v in w.items())


def classify(df: pd.DataFrame, weights: dict, thresholds=THRESHOLDS, hist_col="hist_500m") -> pd.DataFrame:
    """Adds `mhi`, `hz_max`, `zone` columns (operates on hz_<hazard> columns)."""
    out = df.copy()
    hz = out[[f"hz_{h}" for h in weights]]
    out["mhi"] = mhi(out, weights).clip(0, 1)
    out["hz_max"] = hz.max(axis=1)
    out["hz_dominant"] = hz.idxmax(axis=1).str[3:]
    zone = np.full(len(out), "green", dtype=object)
    for z in ("yellow", "orange", "red"):  # ascending, later overwrites
        m, s = thresholds[z]
        zone[(out.mhi >= m) | (out.hz_max >= s)] = z
    if hist_col in out:
        zone[out[hist_col] >= HIST_RED] = "red"
    out["zone"] = zone
    return out


def apply_triggers(df: pd.DataFrame, aoi: str, rain: pd.DataFrame | None = None, alerts: dict | None = None) -> pd.DataFrame:
    """Scale static hazard scores with forecast rainfall and official alerts.

    rain: per-cell columns `r24` (max 24 h accumulation, mm) and `r1` (max hourly, mm).
    alerts: {hazard: multiplier} for the AOI (e.g. from SACHET), applied on top.
    """
    out = df.copy()
    if rain is not None:
        r24 = rain["r24"].reindex(out.index).fillna(0).to_numpy()
        r1 = rain["r1"].reindex(out.index).fillna(0).to_numpy()
        # 50 mm/24 h starts to matter, 200 mm/24 h = 1.4x (Himalayan ID thresholds are ~ in this band).
        rain_mult = 1 + 0.4 * np.clip((r24 - 50) / 150, 0, 1)
        for h in ("landslide", "flood"):  # surge/coastal are driven by cyclone alerts, not rain
            if f"hz_{h}" in out:
                out[f"hz_{h}"] = out[f"hz_{h}"] * rain_mult
        if "hz_cloudburst" in out:
            # ≥ 100 mm/h is the IMD cloudburst definition; scale from 30 mm/h upwards.
            out["hz_cloudburst"] = out["hz_cloudburst"] * (1 + 0.8 * np.clip((r1 - 30) / 70, 0, 1))
        out["r24"], out["r1"] = r24, r1
    for h, mult in (alerts or {}).items():
        if f"hz_{h}" in out:
            out[f"hz_{h}"] = out[f"hz_{h}"] * mult
    for c in [f"hz_{h}" for h in ("landslide", "flood", "cloudburst", "coastal", "surge") if f"hz_{h}" in out]:
        out[c] = out[c].clip(0, 1)
    return out


if __name__ == "__main__":
    d = pd.DataFrame({"hz_landslide": [0.95, 0.6, 0.1, 0.2], "hz_flood": [0.1, 0.6, 0.1, 0.1],
                      "hz_cloudburst": [0.1, 0.6, 0.1, 0.1], "hist_500m": [0, 0, 0, 3]})
    z = classify(d, WEIGHTS["uk"]).zone.tolist()
    assert z == ["red", "orange", "green", "red"], z
    t = apply_triggers(d, "uk", pd.DataFrame({"r24": [0, 200, 0, 0], "r1": [0, 100, 0, 0]}))
    assert classify(t, WEIGHTS["uk"]).zone.tolist()[1] == "red"
    print("engine ok")

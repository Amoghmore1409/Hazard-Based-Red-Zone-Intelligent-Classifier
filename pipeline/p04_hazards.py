"""Per-cell hazard scores (0–1), static MHI and Red Zones -> data/processed/{aoi}_cells.parquet

Landslide: XGBoost trained on the GSI inventory, spatial CV (H3 res-6 blocks), out-of-fold scores
mapped to susceptibility percentiles; SHAP top factors from xgboost's built-in pred_contribs.
Flood / cloudburst / coastal / surge: transparent index models (documented formulas below).
"""
import json
import sys
from datetime import datetime

import h3
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

from common import AOIS, PROC, RAW, ROOT

sys.path.insert(0, str(ROOT))
from backend.app.engine import WEIGHTS, classify  # noqa: E402

LS_FEATURES = ["elev", "slope", "slope_max", "tpi", "twi", "hand", "ndvi", "clay", "aspect_sin", "aspect_cos",
               "dist_river", "heavy_days", "rain_p99", "rain_annual",
               "lc_tree", "lc_shrub", "lc_grass", "lc_crop", "lc_built", "lc_bare", "lc_snow"]
LABELS = {"elev": "elevation", "slope": "slope", "slope_max": "max slope", "tpi": "terrain position",
          "twi": "wetness index", "hand": "height above drainage", "ndvi": "vegetation", "clay": "clay content",
          "aspect_sin": "aspect (E-W)", "aspect_cos": "aspect (N-S)", "dist_road": "road cutting proximity",
          "dist_river": "river toe-cutting proximity", "heavy_days": "heavy-rain days", "rain_p99": "extreme rainfall",
          "rain_annual": "annual rainfall", "lc_tree": "forest cover", "lc_shrub": "shrub cover",
          "lc_grass": "grassland", "lc_crop": "cultivation", "lc_built": "built-up", "lc_bare": "barren land",
          "lc_snow": "snow/ice"}
MODELS = ROOT / "backend" / "ml_artifacts"
MODELS.mkdir(parents=True, exist_ok=True)


def pct(s):
    return s.rank(pct=True)


def landslide(df):
    # GSI landslides are mapped mostly along roads (median 336 m vs 3 km for all cells). To avoid learning
    # "where surveyors went", train only inside the surveyed domain and drop road distance as a feature.
    domain = (df.dist_road < 1500) | (df.ls_frac > 0)
    d = df[domain]
    X, y = d[LS_FEATURES], (d.ls_frac > 0).astype(int)
    groups = [h3.cell_to_parent(c, 6) for c in d.h3]
    params = dict(n_estimators=400, max_depth=6, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
                  scale_pos_weight=(1 - y.mean()) / y.mean(), eval_metric="auc", n_jobs=-1)
    oof = np.zeros(len(d))
    aucs = []
    for tr, te in GroupKFold(5).split(X, y, groups):
        m = xgb.XGBClassifier(**params).fit(X.iloc[tr], y.iloc[tr])
        oof[te] = m.predict_proba(X.iloc[te])[:, 1]
        aucs.append(roc_auc_score(y.iloc[te], oof[te]))
    model = xgb.XGBClassifier(**params).fit(X, y)
    score = pd.Series(model.predict_proba(df[LS_FEATURES])[:, 1], index=df.index)
    score[domain] = oof  # honest out-of-fold scores wherever we have labels
    model.save_model(MODELS / "landslide_uk.json")

    X = df[LS_FEATURES]
    contrib = model.get_booster().predict(xgb.DMatrix(X), pred_contribs=True)[:, :-1]
    top = np.argsort(-contrib, axis=1)[:, :3]
    df["ls_factors"] = [json.dumps([LABELS[LS_FEATURES[j]] for j in row if contrib[i, j] > 0])
                        for i, row in enumerate(top)]
    df["hz_landslide"] = pct(score)
    imp = sorted(zip(LS_FEATURES, model.feature_importances_.round(4).tolist()), key=lambda t: -t[1])
    metrics = {"model": "landslide_xgboost", "aoi": "uk", "auc_spatial_cv": round(float(np.mean(aucs)), 3),
               "auc_folds": [round(a, 3) for a in aucs], "positives": int(y.sum()), "cells": len(df), "training_domain_cells": int(domain.sum()),
               "importance": imp[:10], "created_at": datetime.now().isoformat(timespec="seconds")}
    (MODELS / "landslide_uk_metrics.json").write_text(json.dumps(metrics, indent=2))
    print(f"[uk] landslide spatial-CV AUC {metrics['auc_spatial_cv']} {metrics['auc_folds']}")
    return df


def flood(df):
    # Low height-above-drainage + large upstream area + observed historical water = flood-prone.
    hand_s = np.exp(-df.hand.clip(lower=0) / 6)
    upa_s = (np.log10(df.upa.clip(lower=0) + 1) / 3.5).clip(0, 1)
    jrc_s = (df.jrc_occ / 30).clip(0, 1)
    river_s = np.exp(-df.dist_river / 800)
    return (0.5 * hand_s * (0.5 + 0.5 * upa_s) + 0.2 * river_s + 0.3 * jrc_s).clip(0, 1)


def cloudburst(df):
    # Orographic belt (~1–3.5 km), steep confined valleys and historically intense rain.
    band = np.exp(-((df.elev - 2000) / 1000) ** 2)
    steep = (df.slope / 40).clip(0, 1)
    valley = (-df.tpi / 60).clip(0, 1)
    return (0.35 * band + 0.25 * steep + 0.2 * valley + 0.2 * pct(df.rain_p99)).clip(0, 1)


def coastal(df):
    eros = (df.erosion_density / 0.04).clip(0, 1)
    near = np.exp(-df.dist_coast / 2500)
    low = ((5 - df.elev) / 5).clip(0, 1)
    return ((0.55 * eros + 0.3 * near + 0.15 * low) * (1 - 0.5 * df.lc_mangrove)).clip(0, 1)


def surge(df):
    # Bathtub proxy: Odisha surges of 3–7 m penetrate 10–20 km inland on flat deltas.
    low = ((7 - df.elev) / 7).clip(0, 1)
    return (low * np.exp(-df.dist_coast / 12000)).clip(0, 1)


def history(df, key):
    """hist_500m: recorded events within ~350 m (cell + H3 ring 1). GSI landslides + curated event list."""
    counts = {}
    pts = []
    if key == "uk" and (RAW / "gsi" / "landslide_polygon.shp").exists():
        import geopandas as gpd
        g = gpd.read_file(RAW / "gsi" / "landslide_polygon.shp", encoding="ISO-8859-1")
        rp = g.geometry.representative_point()
        pts += list(zip(rp.y, rp.x))
    ev = pd.read_csv(ROOT / "data" / "seed" / "events.csv")
    pts += list(ev[(ev.aoi == key) & (ev.precision == "site")][["lat", "lon"]].itertuples(index=False, name=None))
    for lat, lon in pts:
        c = h3.latlng_to_cell(lat, lon, 9)
        for n in h3.grid_disk(c, 1):
            counts[n] = counts.get(n, 0) + 1
    return df.h3.map(counts).fillna(0).astype(int)


def run(key):
    df = pd.read_parquet(PROC / f"{key}_features.parquet")
    if key == "uk":
        df = landslide(df)
        df["hz_cloudburst"] = cloudburst(df)
    else:
        df["hz_coastal"] = coastal(df)
        df["hz_surge"] = surge(df)
    df["hz_flood"] = flood(df)
    df["hist_500m"] = history(df, key)
    df = classify(df, WEIGHTS[key])
    df.to_parquet(PROC / f"{key}_cells.parquet")
    print(f"[{key}] zones:", df.zone.value_counts().to_dict())


if __name__ == "__main__":
    for k in sys.argv[1:] or list(AOIS):
        run(k)

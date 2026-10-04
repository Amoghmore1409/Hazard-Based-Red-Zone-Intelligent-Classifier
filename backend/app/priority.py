"""Relocation priority rules + plain-language explanations (shared by pipeline and API)."""
import pandas as pd

HAZARD_NAMES = {"landslide": "landslide", "flood": "flood", "cloudburst": "cloudburst",
                "coastal": "coastal erosion", "surge": "storm surge"}


def _reasons(r) -> list[str]:
    out = []
    if r.frac_red > 0:
        out.append(f"{r.frac_red:.0%} of residents live inside a Red Zone (dominant: {HAZARD_NAMES.get(r.dominant_hazard, r.dominant_hazard)})")
    if r.frac_orange > 0.2:
        out.append(f"{r.frac_orange:.0%} of residents in Orange Zone")
    if r.hist >= 2:
        out.append(f"{int(r.hist)} recorded landslides/events within ~350 m")
    if r.nearest_event_km <= 5:
        out.append(f"{r.nearest_event_km:.1f} km from {r.nearest_event} ({int(r.nearest_event_year)})")
    if r.dist_health > 10000:
        out.append(f"{r.dist_health / 1000:.0f} km to nearest health facility")
    if r.dist_road > 2000:
        out.append(f"{r.dist_road / 1000:.1f} km from a motorable road")
    for col, cut, text in (("pct_kutcha", 0.5, "households in kutcha / non-permanent houses"),
                           ("pct_dilapidated", 0.1, "houses dilapidated"),
                           ("pct_scst", 0.4, "of residents SC/ST"),
                           ("pct_illiterate", 0.4, "of residents illiterate"),
                           ("pct_no_assets", 0.35, "households own none of the basic assets"),
                           ("pct_no_electricity", 0.4, "households without electricity")):
        v = getattr(r, col, None)
        if v is not None and v == v and v >= cut:
            out.append(f"{v:.0%} {text} (Census 2011)")
    if getattr(r, "field_reports", 0):
        out.append(f"{int(r.field_reports)} field report(s) of ground distress in last 30 days")
    if r.slope > 30:
        out.append(f"average slope {r.slope:.0f}°")
    return out


def prioritise(h: pd.DataFrame) -> pd.DataFrame:
    """Adds `priority` (immediate|short|medium|monitor) and `reasons` (list[str])."""
    h = h.copy()
    p90 = h.risk.quantile(0.9)
    fatal_recent = (h.nearest_event_km <= 3) & (h.nearest_event_deaths > 0) & (h.nearest_event_year >= 2010)
    reports = h.get("field_reports", pd.Series(0, index=h.index))
    red, orange = h.frac_red, h.frac_red + h.frac_orange
    pr = pd.Series("monitor", index=h.index)
    pr[(orange >= 0.25) | (h["hist"] >= 3)] = "medium"
    # Observed ground distress (cracks, subsidence) is evidence even where models are silent (e.g. Joshimath).
    pr[(red >= 0.25) | ((orange >= 0.5) & (h.svi >= 0.6)) | (reports >= 1)] = "short"
    pr[((red >= 0.5) & ((h.risk >= p90) | fatal_recent)) | ((reports >= 1) & (orange >= 0.25)) | (reports >= 2)] = "immediate"
    h["priority"] = pr
    h["reasons"] = [_reasons(r) for r in h.itertuples()]
    return h

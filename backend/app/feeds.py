"""Live feeds: Open-Meteo rainfall forecast (no key) and NDMA SACHET CAP alerts (RSS)."""
import re
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import requests
from sklearn.neighbors import BallTree

SACHET_RSS = "https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml"
# Alert keyword -> hazard multipliers.
ALERT_RULES = [
    (r"cyclone|storm surge|depression", {"surge": 1.4, "coastal": 1.25, "flood": 1.2}),
    (r"cloud ?burst", {"cloudburst": 1.4, "landslide": 1.25, "flood": 1.2}),
    (r"landslide", {"landslide": 1.35}),
    (r"flood|inundation", {"flood": 1.3}),
    (r"heavy rain|very heavy|extremely heavy|thunderstorm", {"landslide": 1.2, "flood": 1.15, "cloudburst": 1.2}),
]


def rainfall(cells: pd.DataFrame, step=0.1) -> tuple[pd.DataFrame, dict]:
    """Per-cell max 24 h accumulation (r24) and max hourly rain (r1) over the next 3 days."""
    lat = np.arange(cells.lat.min(), cells.lat.max() + step, step)
    lon = np.arange(cells.lon.min(), cells.lon.max() + step, step)
    grid = np.array([(a, o) for a in lat for o in lon]).round(3)
    r24, r1 = [], []
    for chunk in np.array_split(grid, max(1, len(grid) // 100)):
        res = requests.get("https://api.open-meteo.com/v1/forecast", timeout=30, params={
            "latitude": ",".join(map(str, chunk[:, 0])), "longitude": ",".join(map(str, chunk[:, 1])),
            "hourly": "precipitation", "forecast_days": 3, "timezone": "Asia/Kolkata"}).json()
        for loc in res if isinstance(res, list) else [res]:
            p = pd.Series(loc["hourly"]["precipitation"]).fillna(0)
            r24.append(p.rolling(24, min_periods=1).sum().max())
            r1.append(p.max())
    _, idx = BallTree(np.radians(grid), metric="haversine").query(np.radians(cells[["lat", "lon"]]), k=1)
    out = pd.DataFrame({"r24": np.array(r24)[idx[:, 0]], "r1": np.array(r1)[idx[:, 0]]}, index=cells.index)
    return out, {"max_r24": float(max(r24)), "max_r1": float(max(r1)), "points": len(grid)}


def sachet_alerts(districts: list[str], state: str, max_fetch=30) -> list[dict]:
    """Active SACHET CAP alerts whose area mentions one of the districts (or the whole state)."""
    root = ET.fromstring(requests.get(SACHET_RSS, timeout=20).content)
    found = []
    for item in list(root.iter("item"))[:300]:
        author = item.findtext("author") or ""
        if state.lower() not in author.lower() and not re.search(r"\b(imd|cwc)\b", author, re.I):
            continue
        if max_fetch == 0:
            break
        max_fetch -= 1
        try:
            cap = ET.fromstring(requests.get(item.findtext("link"), timeout=15).content)
        except Exception:
            continue
        info = {el.tag.split("}")[1]: (el.text or "") for el in cap.iter() if "}" in el.tag}
        area = " ".join(el.text or "" for el in cap.iter() if el.tag.endswith("areaDesc"))
        if not any(d.lower() in area.lower() for d in districts) and state.lower() not in area.lower():
            continue
        text = f"{info.get('event', '')} {info.get('headline', '')} {info.get('description', '')} {item.findtext('title')}"
        mult = {}
        for pattern, m in ALERT_RULES:
            if re.search(pattern, text, re.I):
                for k, v in m.items():
                    mult[k] = max(mult.get(k, 1), v)
        found.append({"title": (info.get("headline") or info.get("event") or item.findtext("title") or "").strip()[:300],
                      "body": f"{info.get('severity', '')} | {area[:300]} | {info.get('description', '')[:300]}",
                      "hazards": mult, "link": item.findtext("link"), "published": item.findtext("pubDate")})
    return found


def merge_multipliers(alerts: list[dict]) -> dict:
    out = {}
    for a in alerts:
        for k, v in a["hazards"].items():
            out[k] = max(out.get(k, 1), v)
    return out

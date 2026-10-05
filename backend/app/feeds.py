"""Live feeds: Open-Meteo rainfall forecast (no key) and NDMA SACHET CAP alerts (RSS)."""
import re
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import requests

SACHET_RSS = "https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml"
# Alert keyword -> hazard multipliers.
ALERT_RULES = [
    (r"cyclone|storm surge|depression", {"surge": 1.4, "coastal": 1.25, "flood": 1.2}),
    (r"cloud ?burst", {"cloudburst": 1.4, "landslide": 1.25, "flood": 1.2}),
    (r"landslide", {"landslide": 1.35}),
    (r"flood|inundation", {"flood": 1.3}),
    (r"heavy rain|very heavy|extremely heavy|thunderstorm", {"landslide": 1.2, "flood": 1.15, "cloudburst": 1.2}),
]


METNO_UA = {"User-Agent": "SURAKSHA-SIH/1.0 github.com/Amoghmore1409/Hazard-Based-Red-Zone-Intelligent-Classifier"}


def _openmeteo(grid: np.ndarray) -> list[pd.Series]:
    """Hourly precipitation (mm) for the next 72 h per grid point; raises with Open-Meteo's own reason."""
    out = []
    for chunk in np.array_split(grid, max(1, len(grid) // 100)):
        res = requests.get("https://api.open-meteo.com/v1/forecast", timeout=30, params={
            "latitude": ",".join(map(str, chunk[:, 0])), "longitude": ",".join(map(str, chunk[:, 1])),
            "hourly": "precipitation", "forecast_days": 3, "timezone": "Asia/Kolkata"}).json()
        if isinstance(res, dict) and "hourly" not in res:  # e.g. {"error": true, "reason": "...limit exceeded"}
            raise RuntimeError(f"Open-Meteo: {res.get('reason', res)}")
        out += [pd.Series(loc["hourly"]["precipitation"]).fillna(0) for loc in (res if isinstance(res, list) else [res])]
    return out


def _metno(grid: np.ndarray) -> list[pd.Series]:
    """Same, from MET Norway Locationforecast (global, free, needs an identifying User-Agent, one point per call).
    1-hour amounts where given, 6-hour amounts spread evenly after that."""
    def one(pt):
        la, lo = pt
        ts = requests.get("https://api.met.no/weatherapi/locationforecast/2.0/compact", headers=METNO_UA, timeout=30,
                          params={"lat": round(la, 4), "lon": round(lo, 4)}).json()["properties"]["timeseries"]
        hourly = []
        for t in ts:
            d = t["data"]
            if "next_1_hours" in d:
                hourly.append(d["next_1_hours"]["details"].get("precipitation_amount", 0))
            elif "next_6_hours" in d:
                hourly += [d["next_6_hours"]["details"].get("precipitation_amount", 0) / 6] * 6
            if len(hourly) >= 72:
                break
        return pd.Series(hourly[:72], dtype=float)

    with ThreadPoolExecutor(8) as ex:  # met.no allows ~20 requests/s
        return list(ex.map(one, grid))


def rainfall(cells: pd.DataFrame, step=0.2) -> tuple[pd.DataFrame, dict]:
    """Per-cell max 24 h accumulation (r24) and max hourly rain (r1) over the next 3 days.
    0.2° grid ≈ the resolution of the global forecast models; Open-Meteo first, MET Norway if it refuses
    (Open-Meteo's free quota is per IP, and cloud hosts share IPs)."""
    lat = np.arange(cells.lat.min(), cells.lat.max() + step, step)
    lon = np.arange(cells.lon.min(), cells.lon.max() + step, step)
    grid = np.array([(a, o) for a in lat for o in lon]).round(3)
    try:
        series, source = _openmeteo(grid), "Open-Meteo"
    except Exception as e:
        try:
            series, source = _metno(grid), "MET Norway"
        except Exception as e2:
            raise RuntimeError(f"{e}; MET Norway fallback: {e2}") from e2
    r24 = [s.rolling(24, min_periods=1).sum().max() for s in series]
    r1 = [s.max() for s in series]
    # Regular grid: the nearest point is found by rounding, row-major (lat outer, lon inner) like `grid`.
    ia = np.clip(np.rint((cells.lat.to_numpy() - lat[0]) / step).astype(int), 0, len(lat) - 1)
    io = np.clip(np.rint((cells.lon.to_numpy() - lon[0]) / step).astype(int), 0, len(lon) - 1)
    idx = ia * len(lon) + io
    out = pd.DataFrame({"r24": np.array(r24)[idx], "r1": np.array(r1)[idx]}, index=cells.index)
    return out, {"max_r24": float(max(r24)), "max_r1": float(max(r1)), "points": len(grid), "forecast_source": source}


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

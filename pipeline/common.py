"""Shared AOI config and paths for the offline pipeline."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
PROC.mkdir(parents=True, exist_ok=True)

GEE_PROJECT = os.getenv("GEE_PROJECT")
H3_RES = 9  # ~0.1 km² per cell

AOIS = {
    "uk": {
        "name": "Uttarakhand – Chamoli & Rudraprayag",
        "state": "Uttarakhand",
        "districts": ["Chamoli", "Rudraprayag"],
        "kind": "himalayan",
        "crs": "EPSG:32644",
        "scale": 60,
        "hazards": ["landslide", "flood", "cloudburst"],
    },
    "od": {
        "name": "Odisha – Kendrapara",
        "state": "Odisha",
        "districts": ["Kendrapara"],
        "kind": "coastal",
        "crs": "EPSG:32645",
        "scale": 60,
        "hazards": ["flood", "coastal", "surge"],
    },
}


def ee_init():
    import ee
    ee.Initialize(project=GEE_PROJECT)
    return ee

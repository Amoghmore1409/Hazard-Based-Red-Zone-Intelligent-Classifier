"""Copy the local SURAKSHA database (Docker PostGIS) to the cloud database (Supabase).

Usage:  .venv\\Scripts\\python deploy\\push_db.py            (reads DATABASE_URL from deploy/space.env)
        .venv\\Scripts\\python deploy\\push_db.py --users    (also overwrite the cloud's user accounts)

Pipeline tables are replaced. User accounts are copied only when the cloud has none yet (or with --users),
so viewer accounts registered on the live site are not wiped by a re-run. Alerts, notifications and field
reports start empty in the cloud (field photos live on local disk only).
"""
import os
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from dotenv import dotenv_values
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.db import DB_URL as LOCAL_URL, SCHEMA, normalize_url  # noqa: E402

GEO_TABLES = ["h3_cells", "habitations", "candidate_sites", "hazard_events", "districts"]
PLAIN_TABLES = ["road_distances", "relocation_plans"]
USER_COLS = ["username", "name", "role", "aoi", "pw_hash", "active", "status", "email", "organisation", "reason",
             "created_at", "decided_by", "decided_at"]


def main():
    remote_url = os.getenv("TARGET_DATABASE_URL") or dotenv_values(ROOT / "deploy" / "space.env").get("DATABASE_URL", "")
    if not remote_url or "YOUR_" in remote_url:
        sys.exit("Fill DATABASE_URL in deploy/space.env first (see deploy/space.env.example).")
    local, remote = create_engine(LOCAL_URL), create_engine(normalize_url(remote_url), pool_pre_ping=True)
    host = remote.url.host
    print(f"Copying local database -> {host}")

    with remote.begin() as con:
        con.execute(text(SCHEMA))  # PostGIS extension + users/alerts/... tables
        con.execute(text("SELECT postgis_version()"))
    for t in GEO_TABLES:
        g = gpd.read_postgis(f"SELECT * FROM {t}", local, geom_col="geometry")
        g.to_postgis(t, remote, if_exists="replace", index=False, chunksize=5000)
        print(f"  {t}: {len(g):,} rows")
    for t in PLAIN_TABLES:
        d = pd.read_sql(f"SELECT * FROM {t}", local)
        d.to_sql(t, remote, if_exists="replace", index=False, chunksize=5000, method="multi")
        print(f"  {t}: {len(d):,} rows")
    with remote.begin() as con:
        con.execute(text("CREATE INDEX IF NOT EXISTS h3_cells_aoi ON h3_cells(aoi); "
                         "CREATE INDEX IF NOT EXISTS hab_aoi ON habitations(aoi); "
                         "CREATE INDEX IF NOT EXISTS road_aoi ON road_distances(aoi);"))
        has_users = con.execute(text("SELECT count(*) FROM users")).scalar()
        if has_users and "--users" not in sys.argv:
            print(f"  users: kept the cloud's {has_users} accounts (pass --users to overwrite)")
        else:
            u = pd.read_sql(f"SELECT {', '.join(USER_COLS)} FROM users", local)
            con.execute(text("DELETE FROM users"))
            u.to_sql("users", con, if_exists="append", index=False)
            print(f"  users: {len(u)} accounts copied")
    print("Done.")


if __name__ == "__main__":
    main()

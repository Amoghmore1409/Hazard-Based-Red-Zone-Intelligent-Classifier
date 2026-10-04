import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
DB_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://suraksha:suraksha@localhost:5433/suraksha")

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE TABLE IF NOT EXISTS alerts (
  id serial PRIMARY KEY, aoi text, source text, severity text, title text, body text,
  hazards jsonb, issued_at timestamptz DEFAULT now(), expires_at timestamptz);
CREATE TABLE IF NOT EXISTS field_reports (
  id serial PRIMARY KEY, aoi text, kind text, note text, photo text, reporter text,
  geom geometry(Point, 4326), created_at timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS notifications (
  id serial PRIMARY KEY, aoi text, channel text, recipient text, message text, status text,
  created_at timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS users (
  id serial PRIMARY KEY, username text UNIQUE NOT NULL, name text, role text NOT NULL, aoi text,
  pw_hash text NOT NULL, active boolean DEFAULT true, created_at timestamptz DEFAULT now());
ALTER TABLE users ADD COLUMN IF NOT EXISTS status text DEFAULT 'active';   -- active | pending | rejected
ALTER TABLE users ADD COLUMN IF NOT EXISTS organisation text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS reason text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS email text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS decided_by text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS decided_at timestamptz;
CREATE TABLE IF NOT EXISTS zone_snapshots (
  id serial PRIMARY KEY, aoi text, source text, run_at timestamptz DEFAULT now(),
  red int, orange int, yellow int, green int, active_red int, max_r24 real, max_r1 real);
"""

engine = create_engine(DB_URL, pool_pre_ping=True)

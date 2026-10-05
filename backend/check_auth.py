"""Role/permission matrix check against a running API (python backend/check_auth.py).
Needs demo users from `python -m backend.app.auth seed`; reads their passwords from .env and never prints them."""
import os
import time
from pathlib import Path

import requests
from dotenv import dotenv_values

env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
B = os.getenv("API_BASE", "http://localhost:8000") + "/api"  # e.g. API_BASE=https://<user>-suraksha.hf.space
for _ in range(60):
    try:
        requests.get(B + "/aois", timeout=3)
        break
    except requests.RequestException:
        time.sleep(2)


def tok(u):
    r = requests.post(B + "/auth/login", json={"username": u, "password": env[f"DEMO_PASSWORD_{u.upper().replace('.', '_')}"]})
    assert r.ok, (u, r.status_code, r.text)
    return {"Authorization": "Bearer " + r.json()["token"]}


fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {label}: {got} (want {want})")
    if not ok:
        fails.append(label)


check("no token -> aois", requests.get(B + "/aois").status_code, 401)
check("bad password", requests.post(B + "/auth/login", json={"username": "viewer", "password": "wrong"}).status_code, 401)
check("unknown user", requests.post(B + "/auth/login", json={"username": "nobody", "password": "x"}).status_code, 401)
check("forged token", requests.get(B + "/aois", headers={"Authorization": "Bearer abc.def.ghi"}).status_code, 401)

H = {u: tok(u) for u in ("ndrf.admin", "sdma.uk", "sdma.od", "field.uk", "viewer")}
check("admin sees both regions", [a["id"] for a in requests.get(B + "/aois", headers=H["ndrf.admin"]).json()], ["uk", "od"])
check("sdma.uk sees only uk", [a["id"] for a in requests.get(B + "/aois", headers=H["sdma.uk"]).json()], ["uk"])
check("sdma.uk reads uk summary", requests.get(B + "/summary/uk", headers=H["sdma.uk"]).status_code, 200)
check("sdma.uk blocked from od summary", requests.get(B + "/summary/od", headers=H["sdma.uk"]).status_code, 403)
check("sdma.od blocked from uk cells", requests.get(B + "/cells/uk", headers=H["sdma.od"]).status_code, 403)
check("viewer reads od", requests.get(B + "/summary/od", headers=H["viewer"]).status_code, 200)
check("viewer can simulate", requests.post(B + "/simulate/od", json={"rain24": 100}, headers=H["viewer"]).status_code, 200)
check("viewer cannot optimize", requests.post(B + "/plans/uk/optimize", json={}, headers=H["viewer"]).status_code, 403)
check("viewer cannot refresh", requests.post(B + "/refresh/uk", headers=H["viewer"]).status_code, 403)
check("viewer cannot field-report", requests.post(B + "/field-reports", data={"aoi": "uk", "kind": "x", "lat": 30.5, "lon": 79.5},
                                                  headers=H["viewer"]).status_code, 403)
check("field cannot read dashboard data", requests.get(B + "/summary/uk", headers=H["field.uk"]).status_code, 403)
check("field cannot report other region", requests.post(B + "/field-reports", data={"aoi": "od", "kind": "x", "lat": 20.5, "lon": 86.5},
                                                       headers=H["field.uk"]).status_code, 403)
check("field bad coords rejected", requests.post(B + "/field-reports", data={"aoi": "uk", "kind": "x", "lat": 20.5, "lon": 86.5},
                                                headers=H["field.uk"]).status_code, 400)
check("sdma.uk cannot optimize od", requests.post(B + "/plans/od/optimize", json={}, headers=H["sdma.uk"]).status_code, 403)
check("admin pdf", requests.get(B + "/report/od.pdf", headers=H["ndrf.admin"]).status_code, 200)
me = requests.get(B + "/auth/me", headers=H["sdma.od"]).json()
check("me role/aoi", (me["role"], me["aoi"]), ("sdma", "od"))
print("\nALL PASS" if not fails else f"\nFAILED: {fails}")

"""Viewer self-registration + admin approval flow check against a running API (python backend/check_register.py).
Creates throw-away test accounts and deletes them at the end. Reads the admin demo password from .env."""
import os
import secrets
import time
from pathlib import Path

import requests
from dotenv import dotenv_values
from sqlalchemy import text

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.db import engine  # noqa: E402

env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
B = os.getenv("API_BASE", "http://localhost:8000") + "/api"  # set DATABASE_URL too, so clean-up hits that database
for _ in range(60):
    try:
        requests.get(B + "/aois", timeout=3)
        break
    except requests.RequestException:
        time.sleep(2)

fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {label}: {got} (want {want})")
    if not ok:
        fails.append(label)


u1, u2 = f"test.viewer.{secrets.token_hex(3)}", f"test.reject.{secrets.token_hex(3)}"
pw = "Viewer" + secrets.token_hex(4) + "9"
body = {"username": u1, "name": "Test Viewer", "password": pw, "email": "t@example.org", "organisation": "Test Org", "reason": "check"}
try:
    check("weak password rejected", requests.post(B + "/auth/register", json={**body, "password": "short"}).status_code, 400)
    check("bad username rejected", requests.post(B + "/auth/register", json={**body, "username": "Bad Name!"}).status_code, 400)
    check("bad email rejected", requests.post(B + "/auth/register", json={**body, "email": "nope"}).status_code, 400)
    check("register ok", requests.post(B + "/auth/register", json=body).status_code, 200)
    check("duplicate username", requests.post(B + "/auth/register", json=body).status_code, 409)
    check("cannot claim a demo name", requests.post(B + "/auth/register", json={**body, "username": "ndrf.admin"}).status_code, 409)
    r = requests.post(B + "/auth/login", json={"username": u1, "password": pw})
    check("pending cannot log in", (r.status_code, "awaiting approval" in r.text), (403, True))
    check("pending + wrong password looks generic", requests.post(B + "/auth/login", json={"username": u1, "password": "x"}).status_code, 401)

    admin = {"Authorization": "Bearer " + requests.post(B + "/auth/login", json={
        "username": "ndrf.admin", "password": env["DEMO_PASSWORD_NDRF_ADMIN"]}).json()["token"]}
    check("anonymous cannot list users", requests.get(B + "/auth/users").status_code, 401)
    lst = requests.get(B + "/auth/users", headers=admin).json()
    check("admin sees pending request", any(x["username"] == u1 and x["status"] == "pending" for x in lst), True)
    check("no password hashes exposed", any("pw_hash" in x for x in lst), False)
    check("approve", requests.post(B + f"/auth/users/{u1}/approve", headers=admin).status_code, 200)
    check("approve twice -> 404", requests.post(B + f"/auth/users/{u1}/approve", headers=admin).status_code, 404)
    r = requests.post(B + "/auth/login", json={"username": u1, "password": pw})
    check("approved viewer logs in as viewer", (r.status_code, r.json().get("user", {}).get("role")), (200, "viewer"))
    viewer = {"Authorization": "Bearer " + r.json()["token"]}
    check("viewer reads summary", requests.get(B + "/summary/uk", headers=viewer).status_code, 200)
    check("viewer cannot optimize", requests.post(B + "/plans/uk/optimize", json={}, headers=viewer).status_code, 403)
    check("viewer cannot list users", requests.get(B + "/auth/users", headers=viewer).status_code, 403)
    check("viewer cannot approve", requests.post(B + f"/auth/users/{u1}/approve", headers=viewer).status_code, 403)

    check("register 2", requests.post(B + "/auth/register", json={**body, "username": u2}).status_code, 200)
    check("reject", requests.post(B + f"/auth/users/{u2}/reject", headers=admin).status_code, 200)
    r = requests.post(B + "/auth/login", json={"username": u2, "password": pw})
    check("rejected cannot log in", (r.status_code, "not approved" in r.text), (403, True))
    check("cannot approve non-viewer/admin account", requests.post(B + "/auth/users/sdma.uk/approve", headers=admin).status_code, 404)
finally:
    with engine.begin() as con:
        con.execute(text("DELETE FROM users WHERE username IN (:a, :b)"), {"a": u1, "b": u2})
print("\nALL PASS" if not fails else f"\nFAILED: {fails}")

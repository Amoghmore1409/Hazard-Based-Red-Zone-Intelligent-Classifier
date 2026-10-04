"""Login + role-based access. JWT (HS256) bearer tokens; passwords hashed with PBKDF2-SHA256 (stdlib).

Roles
  admin   NDRF / MHA: every region, every action
  sdma    State/District DMA officer: own region; view, simulate, optimise plans, refresh live data, field reports
  viewer  Read-only: every region; view + what-if simulation
  field   Field reporter: own region; submit field reports only
Anyone may *request* a viewer account (POST /api/auth/register); it stays pending until an admin approves it.

CLI:  python -m backend.app.auth add <username> <role> [aoi] "<Full name>"   (prompts for password)
      python -m backend.app.auth seed      (creates demo users with random passwords, saved to .env)
"""
import hashlib
import hmac
import os
import re
import secrets
import sys
import time
from pathlib import Path

import jwt
from fastapi import HTTPException, Request
from sqlalchemy import text

from .db import engine

ENV = Path(__file__).resolve().parents[2] / ".env"
SECRET = os.getenv("JWT_SECRET") or ""
TOKEN_HOURS = 12
ROLES = {"admin", "sdma", "viewer", "field"}
READ = {"admin", "sdma", "viewer"}
# (method, route template) -> allowed roles. Anything under /api not listed here needs READ.
RULES = {
    ("POST", "/api/auth/login"): None,  # public
    ("POST", "/api/auth/register"): None,  # public: creates a *pending* viewer request
    ("GET", "/api/auth/users"): {"admin"},
    ("POST", "/api/auth/users/{username}/approve"): {"admin"},
    ("POST", "/api/auth/users/{username}/reject"): {"admin"},
    ("GET", "/api/auth/me"): ROLES,
    ("GET", "/api/aois"): ROLES,
    ("POST", "/api/plans/{aoi}/optimize"): {"admin", "sdma"},
    ("POST", "/api/refresh/{aoi}"): {"admin", "sdma"},
    ("POST", "/api/field-reports"): {"admin", "sdma", "field"},
}


def hash_password(pw: str, salt: bytes | None = None, it=200_000) -> str:
    salt = salt or secrets.token_bytes(16)
    return f"pbkdf2${it}${salt.hex()}${hashlib.pbkdf2_hmac('sha256', pw.encode(), salt, it).hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it)).hex()
        return hmac.compare_digest(calc, h)
    except ValueError:
        return False


def make_token(u: dict) -> str:
    claims = {"sub": u["username"], "name": u["name"], "role": u["role"], "aoi": u["aoi"],
              "exp": int(time.time()) + TOKEN_HOURS * 3600}
    return jwt.encode(claims, _secret(), algorithm="HS256")


def _secret() -> str:
    if not SECRET:
        raise HTTPException(500, "JWT_SECRET not configured: run `python -m backend.app.auth seed`")
    return SECRET


def login(username: str, password: str) -> dict:
    with engine.begin() as con:
        row = con.execute(text("SELECT username, name, role, aoi, pw_hash, status FROM users "
                               "WHERE username=:u AND active"), {"u": username.strip().lower()}).mappings().first()
    # Same work whether or not the user exists, so timing does not reveal valid usernames.
    ok = verify_password(password, row["pw_hash"] if row else hash_password("x"))
    if not row or not ok:
        raise HTTPException(401, "Invalid username or password")
    # Status is only revealed after a correct password, so it cannot be used to probe accounts.
    if row["status"] == "pending":
        raise HTTPException(403, "Your access request is awaiting approval by an NDRF administrator")
    if row["status"] == "rejected":
        raise HTTPException(403, "Your access request was not approved. Contact the NDRF control room")
    u = {k: row[k] for k in ("username", "name", "role", "aoi")}
    return {"token": make_token(u), "user": u}


USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def register(username: str, name: str, password: str, email: str, organisation: str, reason: str) -> dict:
    """Self-service request for a read-only viewer account; inactive until an admin approves it."""
    username = username.strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "Username: 3–32 characters, lowercase letters, digits, '.', '_' or '-'")
    if len(password) < 10 or password.lower() == password or not any(c.isdigit() for c in password):
        raise HTTPException(400, "Password: at least 10 characters, with an uppercase letter and a digit")
    if not EMAIL_RE.match(email.strip()):
        raise HTTPException(400, "Enter a valid official email address")
    if not name.strip() or not organisation.strip():
        raise HTTPException(400, "Name and organisation are required")
    with engine.begin() as con:
        if con.execute(text("SELECT 1 FROM users WHERE username=:u"), {"u": username}).first():
            raise HTTPException(409, "That username is taken")
        pending = con.execute(text("SELECT count(*) FROM users WHERE status='pending'")).scalar()
        if pending >= 200:  # ponytail: crude flood guard; add per-IP rate limiting before public deployment
            raise HTTPException(429, "Too many pending requests; try again later")
        con.execute(text("INSERT INTO users(username, name, role, aoi, pw_hash, status, email, organisation, reason) "
                         "VALUES (:u, :n, 'viewer', NULL, :p, 'pending', :e, :o, :r)"),
                    dict(u=username, n=name.strip()[:100], p=hash_password(password), e=email.strip()[:200],
                         o=organisation.strip()[:200], r=reason.strip()[:500]))
    return {"ok": True, "status": "pending"}


def list_users() -> list[dict]:
    with engine.begin() as con:
        rows = con.execute(text("SELECT username, name, role, aoi, status, email, organisation, reason, created_at, "
                                "decided_by, decided_at FROM users ORDER BY (status='pending') DESC, created_at DESC"))
        return [dict(r) for r in rows.mappings()]


def decide(username: str, approve: bool, admin: str) -> dict:
    with engine.begin() as con:
        n = con.execute(text("UPDATE users SET status=:s, decided_by=:a, decided_at=now() "
                             "WHERE username=:u AND role='viewer' AND status='pending'"),
                        dict(s="active" if approve else "rejected", a=admin, u=username)).rowcount
    if not n:
        raise HTTPException(404, "No pending request for that user")
    return {"ok": True, "username": username, "status": "active" if approve else "rejected"}


def authorize(request: Request):
    """App-wide dependency: authenticates /api requests and enforces role + region rules."""
    path = request.url.path
    if not path.startswith("/api"):
        return
    route = request.scope.get("route")
    key = (request.method, getattr(route, "path", path))
    allowed = RULES.get(key, READ)
    if allowed is None:
        return
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "Login required")
    try:
        user = jwt.decode(header[7:], _secret(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired, please log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid session, please log in again")
    if user.get("role") not in allowed:
        raise HTTPException(403, f"Role '{user.get('role')}' cannot perform this action")
    aoi = request.path_params.get("aoi")
    if aoi and user.get("aoi") and aoi != user["aoi"]:
        raise HTTPException(403, "You can only access your own region")
    request.state.user = user


def add_user(username, role, aoi, name, password):
    assert role in ROLES, f"role must be one of {ROLES}"
    with engine.begin() as con:
        con.execute(text("INSERT INTO users(username, name, role, aoi, pw_hash) VALUES (:u,:n,:r,:a,:p) "
                         "ON CONFLICT (username) DO UPDATE SET name=:n, role=:r, aoi=:a, pw_hash=:p, active=true"),
                    dict(u=username.lower(), n=name, r=role, a=aoi or None, p=hash_password(password)))


def _set_env(key, value):
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    lines = [ln for ln in lines if not ln.startswith(f"{key}=")] + [f"{key}={value}"]
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


DEMO_USERS = [  # username, role, aoi, name
    ("ndrf.admin", "admin", None, "NDRF Control Room"),
    ("sdma.uk", "sdma", "uk", "Uttarakhand SDMA Officer"),
    ("sdma.od", "sdma", "od", "Odisha SDMA Officer"),
    ("field.uk", "field", "uk", "Field Reporter – Chamoli"),
    ("viewer", "viewer", None, "Observer (read-only)"),
]

if __name__ == "__main__":
    from .db import SCHEMA
    with engine.begin() as con:
        con.execute(text(SCHEMA))
    if sys.argv[1:2] == ["seed"]:
        if not os.getenv("JWT_SECRET"):
            _set_env("JWT_SECRET", secrets.token_urlsafe(48))
        for u, role, aoi, name in DEMO_USERS:
            pw = secrets.token_urlsafe(9)
            add_user(u, role, aoi, name, pw)
            _set_env(f"DEMO_PASSWORD_{u.upper().replace('.', '_')}", pw)
        print(f"Seeded {len(DEMO_USERS)} demo users; usernames and passwords are in .env (DEMO_PASSWORD_*).")
    elif sys.argv[1:2] == ["add"] and len(sys.argv) >= 4:
        import getpass
        _, _, username, role, *rest = sys.argv
        aoi = rest[0] if rest and rest[0] in ("uk", "od") else None
        name = rest[-1] if rest and rest[-1] not in ("uk", "od") else username
        add_user(username, role, aoi, name, getpass.getpass("Password: "))
        print(f"user {username} ({role}) saved")
    else:
        assert verify_password("s3cret", hash_password("s3cret")) and not verify_password("nope", hash_password("s3cret"))
        print(__doc__)

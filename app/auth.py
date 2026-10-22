"""Accounts, wachtwoorden, sessies, csrf en een eenvoudige limiet per adres. Geen externe pakketten: scrypt en hmac uit de standaardbibliotheek."""
import base64
import hashlib
import hmac
import os
import secrets
import time
import datetime

from fastapi import Request, HTTPException

from . import config, db

COOKIE = "wf_sessie"
SESSIE_DAGEN = 30
_secret = None


def secret() -> bytes:
    global _secret
    if _secret is None: _secret = config.session_secret()
    return _secret


# ---------------------------------------------------------------- wachtwoorden
def hash_pw(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


# ---------------------------------------------------------------- sessies (ondertekende cookie)
def _sign(msg: bytes) -> str:
    return hmac.new(secret(), msg, hashlib.sha256).hexdigest()[:40]


def make_session(user_id: int) -> str:
    payload = f"{user_id}:{int(time.time())}".encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + _sign(payload)


def read_session(value: str):
    """Geeft user_id of None."""
    try:
        b64, sig = value.split(".")
        payload = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        if not hmac.compare_digest(_sign(payload), sig): return None
        uid, issued = payload.decode().split(":")
        if time.time() - int(issued) > SESSIE_DAGEN * 86400: return None
        return int(uid)
    except Exception:
        return None


def set_cookie(response, user_id: int):
    response.set_cookie(COOKIE, make_session(user_id), max_age=SESSIE_DAGEN * 86400, httponly=True,
                        secure=config.is_https(), samesite="lax", path="/")


def clear_cookie(response):
    response.delete_cookie(COOKIE, path="/")


def current_user(request: Request):
    """De ingelogde gebruiker (sqlite3.Row) of None. Geblokkeerde accounts blijven ingelogd maar mogen niets (zie require_*)."""
    if "user" in request.scope: return request.scope["user"]
    uid = read_session(request.cookies.get(COOKIE, ""))
    u = db.one("SELECT * FROM users WHERE id=?", uid) if uid else None
    request.scope["user"] = u
    return u


def csrf_token(request: Request) -> str:
    """Dubbel-ingediend token: afgeleid van de sessiecookie, dus alleen geldig voor deze gebruiker in deze sessie."""
    return _sign(("csrf:" + request.cookies.get(COOKIE, "geen")).encode())


def check_csrf(request: Request, token: str):
    if not token or not hmac.compare_digest(token, csrf_token(request)):
        raise HTTPException(400, "Formulier verlopen of ongeldig (csrf). Laad de pagina opnieuw en probeer het nog eens.")


def require_user(request: Request):
    u = current_user(request)
    if not u: raise HTTPException(401, "Log eerst in.")
    if u["blocked"]: raise HTTPException(403, "Dit account is geblokkeerd" + (f": {u['blocked_reason']}" if u["blocked_reason"] else "."))
    return u


def require_verified(request: Request):
    u = require_user(request)
    if not u["email_verified"]: raise HTTPException(403, "Bevestig eerst je e-mailadres (zie de mail die je kreeg, of vraag een nieuwe aan op je accountpagina).")
    return u


def require_admin(request: Request):
    u = require_user(request)
    if u["role"] != "admin": raise HTTPException(403, "Alleen voor beheer.")
    return u


def is_admin(u) -> bool:
    return bool(u and u["role"] == "admin")


def mag_direct(u) -> bool:
    """Mag deze gebruiker direct publiceren (zonder wachtrij)?"""
    return bool(u and (u["role"] == "admin" or u["trusted"]))


# ---------------------------------------------------------------- tokens (bevestiging, wachtwoord vergeten)
def new_token(user_id: int, kind: str, uren: int = 24) -> str:
    t = secrets.token_urlsafe(32)
    exp = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=uren)).isoformat(timespec="seconds")
    db.db().execute("INSERT INTO tokens(token,user_id,kind,expires_at) VALUES(?,?,?,?)", (t, user_id, kind, exp))
    return t


def use_token(token: str, kind: str):
    """Geeft user_id als het token geldig is en markeert het als gebruikt; anders None."""
    r = db.one("SELECT * FROM tokens WHERE token=? AND kind=? AND used=0", token, kind)
    if not r or r["expires_at"] < db.now(): return None
    db.db().execute("UPDATE tokens SET used=1 WHERE token=?", (token,))
    return r["user_id"]


# ---------------------------------------------------------------- limiet per adres (inloggen, registreren)
_hits: dict = {}


def client_ip(request: Request) -> str:
    xf = request.headers.get("x-forwarded-for")
    return (xf.split(",")[0].strip() if xf else (request.client.host if request.client else "?"))


def rate_limit(request: Request, key: str, limiet: int, venster_s: int = 900):
    k = f"{key}:{client_ip(request)}"
    nu = time.time()
    lst = [t for t in _hits.get(k, []) if nu - t < venster_s]
    if len(lst) >= limiet:
        raise HTTPException(429, "Te veel pogingen. Probeer het over een kwartier nog eens.")
    lst.append(nu)
    _hits[k] = lst

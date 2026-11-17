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


def _pw_merk(pw_hash: str) -> str:
    """Kort merk van het wachtwoord in de sessie: verandert het wachtwoord, dan klopt het merk niet meer en zijn alle andere sessies
    uitgelogd (ook een gestolen cookie). Uit de hash afgeleid en ondertekend, dus er lekt niets van het wachtwoord."""
    return _sign(("pw:" + (pw_hash or "")).encode())[:12]


def make_session(user_id: int, pw_hash: str) -> str:
    payload = f"{user_id}:{int(time.time())}:{_pw_merk(pw_hash)}".encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + _sign(payload)


def read_session(value: str):
    """Geeft (user_id, wachtwoordmerk) of None. Sessies van voor 0.6.0 (zonder merk) gelden niet meer: één keer opnieuw inloggen."""
    try:
        b64, sig = value.split(".")
        payload = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        if not hmac.compare_digest(_sign(payload), sig): return None
        uid, issued, merk = payload.decode().split(":")
        if time.time() - int(issued) > SESSIE_DAGEN * 86400: return None
        return int(uid), merk
    except Exception:
        return None


def set_cookie(response, user_id: int):
    u = db.one("SELECT pw_hash FROM users WHERE id=?", user_id)
    response.set_cookie(COOKIE, make_session(user_id, u["pw_hash"] if u else ""), max_age=SESSIE_DAGEN * 86400, httponly=True,
                        secure=config.is_https(), samesite="lax", path="/")


def clear_cookie(response):
    response.delete_cookie(COOKIE, path="/")


def current_user(request: Request):
    """De ingelogde gebruiker (sqlite3.Row) of None. Geblokkeerde accounts blijven ingelogd maar mogen niets (zie require_*)."""
    if "user" in request.scope: return request.scope["user"]
    s = read_session(request.cookies.get(COOKIE, ""))
    u = db.one("SELECT * FROM users WHERE id=?", s[0]) if s else None
    if u and not hmac.compare_digest(s[1], _pw_merk(u["pw_hash"])): u = None
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
_MAX_SLEUTELS = 20000   # daarboven worden oude sleutels opgeruimd, zodat veel verschillende adressen het geheugen niet vullen


def client_ip(request: Request) -> str:
    """Het adres van de bezoeker. De proxy (Tailscale Funnel) zet het echte adres achteraan in X-Forwarded-For; wat een bezoeker
    zelf in die kop meestuurt staat ervoor. Daarom de laatste: met de eerste kon iemand met een verzonnen kop elke limiet omzeilen."""
    xf = request.headers.get("x-forwarded-for")
    if xf:
        laatste = xf.split(",")[-1].strip()
        if laatste: return laatste
    return request.client.host if request.client else "?"


def binnen_limiet(sleutel: str, limiet: int, venster_s: int = 900) -> bool:
    """Telt één poging voor deze sleutel; False als er in het venster al `limiet` pogingen waren (die poging telt dan niet mee)."""
    nu = time.time()
    if len(_hits) > _MAX_SLEUTELS:
        for k in [k for k, v in _hits.items() if not v or nu - v[-1] > 3600]: del _hits[k]
    lst = [t for t in _hits.get(sleutel, []) if nu - t < venster_s]
    if len(lst) >= limiet:
        _hits[sleutel] = lst
        return False
    lst.append(nu)
    _hits[sleutel] = lst
    return True


def aantal(sleutel: str, venster_s: int) -> int:
    nu = time.time()
    return sum(1 for t in _hits.get(sleutel, []) if nu - t < venster_s)


def tel(sleutel: str):
    _hits.setdefault(sleutel, []).append(time.time())


def rate_limit(request: Request, key: str, limiet: int, venster_s: int = 900):
    if not binnen_limiet(f"{key}:{client_ip(request)}", limiet, venster_s):
        raise HTTPException(429, "Te veel pogingen. Probeer het over een kwartier nog eens.")

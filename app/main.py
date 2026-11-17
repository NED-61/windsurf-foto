"""windsurf-foto: de fotodienst van het Windsurf Wedstrijdarchief.

Draait als één proces (uvicorn) in één container; de archiefsite op Hostinger haalt hier de foto's op (zie README.md).
"""
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from . import config, db, auth, images, archive, web, naakt, gelijkenis
from .routes_public import router as public_router
from .routes_account import router as account_router
from .routes_admin import router as admin_router
from .routes_tags import router as tags_router

def opstart():
    for d in (config.DATA_DIR, config.ORIG_DIR, config.DERIVED_DIR, config.ARCHIEF_DIR, config.DATA_DIR / "tmp"):
        d.mkdir(parents=True, exist_ok=True)
    db.db()
    _zorg_admin()
    if os.environ.get("TESTSET", "1") == "1":
        threading.Thread(target=images.maak_testset, daemon=True).start()
    threading.Thread(target=_archief_en_migratie, daemon=True).start()


def _archief_en_migratie():
    """Archieflijst laden, originelen uit de oude indeling (op hash) verplaatsen naar <jaar>/<wedstrijd>/, en foto's van voor de
    naaktcheck (0.4.0) alsnog beoordelen, en vingerafdrukken voor de lijkt-op-controle. Na elkaar in één taak: de database-verbinding is gedeeld."""
    archive.events()
    try:
        images.migreer_originelen(lambda slug: (archive.event(slug) or {}).get("year"))
    except Exception as e:  # noqa: BLE001
        db.log("originelen.migratie_fout", detail=str(e)[:300])
    try:
        naakt.controleer_bestaande()
    except Exception as e:  # noqa: BLE001
        db.log("naakt.bestaande_fout", detail=str(e)[:300])
    try:   # lijkt-op-controle (0.6.1): vingerafdruk voor de foto's die er al stonden, en vergeten vragen opruimen
        gelijkenis.opruimen()
        gelijkenis.vul_bestaande()
    except Exception as e:  # noqa: BLE001
        db.log("lijkt_op.bestaande_fout", detail=str(e)[:300])


@asynccontextmanager
async def levensloop(app):
    opstart()
    yield


app = FastAPI(title="windsurf-foto", version=config.VERSIE, docs_url=None, redoc_url=None, openapi_url=None, lifespan=levensloop)
app.add_middleware(CORSMiddleware, allow_origins=config.SITE_ORIGINS + ["http://localhost:8000", "http://127.0.0.1:8000"],
                   allow_methods=["GET"], allow_headers=["*"], max_age=3600)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(public_router)
app.include_router(account_router)
app.include_router(admin_router)
app.include_router(tags_router)


def _zorg_admin():
    """Maakt het beheeraccount uit ADMIN_EMAIL/ADMIN_PASSWORD als het nog niet bestaat; bestaat het wel, dan wordt het admin gemaakt."""
    if not config.ADMIN_EMAIL or not config.ADMIN_PASSWORD: return
    u = db.one("SELECT * FROM users WHERE email=?", config.ADMIN_EMAIL)
    if u is None:
        db.db().execute("INSERT INTO users(email,email_verified,pw_hash,name,role,trusted,created_at) VALUES(?,1,?,?,'admin',1,?)",
                        (config.ADMIN_EMAIL, auth.hash_pw(config.ADMIN_PASSWORD), config.ADMIN_NAME, db.now()))
        db.log("account.admin_aangemaakt", detail=config.ADMIN_EMAIL)
    elif u["role"] != "admin":
        db.db().execute("UPDATE users SET role='admin', trusted=1, email_verified=1, blocked=0 WHERE id=?", (u["id"],))


def csp(nonce: str | None) -> str:
    """Wat een pagina van deze dienst mag laden: alleen van zichzelf, scripts alleen uit /static of met de nonce van deze pagina
    (de paar scripts in de templates). Een stukje html dat er toch tussen glipt, kan dan niets uitvoeren."""
    return "; ".join([
        "default-src 'self'",
        "script-src 'self'" + (f" 'nonce-{nonce}'" if nonce else ""),
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "connect-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "object-src 'none'",
    ])


@app.middleware("http")
async def koppen(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if config.is_https():
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if not request.url.path.startswith(("/i/", "/api/", "/static/", "/o/")):
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Content-Security-Policy", csp(request.scope.get("csp_nonce")))
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    return resp


class Groottegrens:
    """Weigert te grote verzoeken voordat iets ze leest. Dat moet vóór de routes: FastAPI leest een formulier (en zet een upload in een
    tijdelijk bestand) al voordat de route kijkt of je bent ingelogd. Een upload mag MAX_UPLOAD_MB plus wat ruimte voor de andere velden,
    al het andere 1 MB."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        grens = (config.MAX_UPLOAD_MB + 1) * 1024 * 1024 if scope["path"] == "/api/upload" else 1024 * 1024
        fout = JSONResponse({"fout": f"Te groot (hooguit {grens // (1024 * 1024)} MB)."}, status_code=413)
        lengte = dict(scope["headers"]).get(b"content-length")
        if lengte is not None:
            try: n = int(lengte)
            except ValueError: n = grens + 1
            if n > grens:
                return await fout(scope, receive, send)
        # Zonder lengte (in stukken verstuurd): tellen tijdens het lezen. Over de grens krijgt de app 'verbinding verbroken' (dan stopt
        # hij met lezen) en wordt zijn antwoord vervangen door 413. Een fout gooien kan hier niet: de andere middleware pakt die in.
        st = {"gelezen": 0, "te_groot": False, "gestart": False, "vervangen": False}

        async def ontvang():
            if st["te_groot"]: return {"type": "http.disconnect"}
            msg = await receive()
            if msg["type"] == "http.request":
                st["gelezen"] += len(msg.get("body", b""))
                if st["gelezen"] > grens:
                    st["te_groot"] = True
                    return {"type": "http.disconnect"}
            return msg

        async def stuur(msg):
            if msg["type"] == "http.response.start":
                if st["te_groot"] and not st["gestart"]:
                    st["gestart"] = st["vervangen"] = True
                    await send({"type": "http.response.start", "status": 413, "headers": fout.raw_headers})
                    await send({"type": "http.response.body", "body": fout.body})
                    return
                st["gestart"] = True
            if st["vervangen"]: return
            await send(msg)
        await self.app(scope, ontvang, stuur)


app.add_middleware(Groottegrens)


@app.exception_handler(HTTPException)
async def http_fout(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/") or request.headers.get("accept", "").startswith("application/json"):
        return JSONResponse({"fout": exc.detail}, status_code=exc.status_code)
    if exc.status_code == 401:
        return web.redirect("/inloggen?terug=" + request.url.path, "Log eerst in.")
    return web.render(request, "bericht.html", status_code=exc.status_code, titel="Dat ging niet", tekst=str(exc.detail))


@app.exception_handler(RequestValidationError)
async def val_fout(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"fout": "Ongeldige invoer: " + "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors())}, status_code=422)
    return web.render(request, "bericht.html", status_code=422, titel="Ongeldige invoer", tekst="Het formulier was niet compleet. Ga terug en probeer het opnieuw.")

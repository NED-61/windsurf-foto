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

from . import config, db, auth, images, archive, web
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
    """Archieflijst laden en daarna originelen uit de oude indeling (op hash) verplaatsen naar <jaar>/<wedstrijd>/."""
    archive.events()
    try:
        images.migreer_originelen(lambda slug: (archive.event(slug) or {}).get("year"))
    except Exception as e:  # noqa: BLE001
        db.log("originelen.migratie_fout", detail=str(e)[:300])


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


@app.middleware("http")
async def koppen(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if not request.url.path.startswith(("/i/", "/api/", "/static/", "/o/")):
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault("X-Frame-Options", "DENY")
    return resp


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

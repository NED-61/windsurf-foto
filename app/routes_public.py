"""Openbaar: gezondheid, testset (F0), manifesten per wedstrijd, de afbeeldingen zelf, en de pagina's die iedereen mag zien."""
import re

from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import FileResponse, JSONResponse

from . import config, db, auth, images, archive, web, mail

router = APIRouter()
IMMUTABLE = {"Cache-Control": "public, max-age=2592000, immutable", "Timing-Allow-Origin": "*"}
PRIVAAT = {"Cache-Control": "private, no-store"}
KEY_RE = re.compile(r"^[0-9a-f]{16}$")


# ---------------------------------------------------------------- api
@router.get("/api/health")
def health():
    return {"ok": True, "dienst": "windsurf-foto", "versie": config.VERSIE, "tijd": db.now(),
            "fotos": db.one("SELECT COUNT(*) n FROM photos WHERE status='zichtbaar'")["n"]}


@router.get("/api/test")
def api_test():
    """Testset voor de meting in F0: 60 thumbnails en kijkversies die zo groot zijn als echte foto's."""
    images.maak_testset()
    base = config.APP_URL
    return {"n": images.TEST_N, "fotos": [{"id": f"test-{n:02d}", "t": f"{base}/i/test-{n:02d}/t.jpg", "m": f"{base}/i/test-{n:02d}/m.jpg", "w": 1600, "h": 1067, "tw": 400, "th": 267}
                                           for n in range(1, images.TEST_N + 1)]}


@router.get("/api/wedstrijd/{slug}")
def api_wedstrijd(slug: str):
    fotos = [web.foto_json(r) for r in web.zichtbare_fotos("event_slug=?", slug)]
    return JSONResponse({"wedstrijd": slug, "naam": archive.event_naam(slug), "n": len(fotos), "fotos": fotos,
                         "upload_url": f"{config.APP_URL}/upload?wedstrijd={slug}", "pagina": f"{config.APP_URL}/wedstrijd/{slug}"},
                        headers={"Cache-Control": "public, max-age=60"})


@router.get("/api/rider/{pid}")
def api_rider(pid: str):
    rows = db.all_("SELECT p.* FROM photos p JOIN tags t ON t.photo_id=p.id WHERE t.person_id=? AND t.status='zichtbaar' AND p.status='zichtbaar' "
                   "GROUP BY p.id ORDER BY COALESCE(p.taken_at, p.uploaded_at) DESC", pid)
    return JSONResponse({"rider": pid, "n": len(rows), "fotos": [web.foto_json(r) for r in rows]}, headers={"Cache-Control": "public, max-age=60"})


@router.get("/api/nieuw")
def api_nieuw(n: int = 24):
    rows = db.all_("SELECT * FROM photos WHERE status='zichtbaar' ORDER BY uploaded_at DESC, id DESC LIMIT ?", max(1, min(n, 100)))
    return JSONResponse({"n": len(rows), "fotos": [web.foto_json(r) for r in rows]}, headers={"Cache-Control": "public, max-age=60"})


@router.get("/api/telling")
def api_telling():
    """Aantal zichtbare foto's per wedstrijd, voor een camerateken in de wedstrijdenlijst."""
    return JSONResponse({r["event_slug"]: r["n"] for r in db.all_("SELECT event_slug, COUNT(*) n FROM photos WHERE status='zichtbaar' GROUP BY event_slug")},
                        headers={"Cache-Control": "public, max-age=300"})


@router.get("/api/wedstrijden")
def api_wedstrijden(q: str = ""):
    return [{"slug": e["slug"], "name": e["name"], "year": e["year"], "date": e.get("date"), "date_end": e.get("date_end"), "location": e.get("location")}
            for e in archive.zoek_events(q, limiet=30 if q else 400)]


@router.get("/api/riders")
def api_riders(q: str = ""):
    return [{"id": r["id"], "name": r["name"], "sails": r.get("sails", []), "years": r.get("years", ""), "n": r.get("n", 0)} for r in archive.zoek_riders(q)]


# ---------------------------------------------------------------- afbeeldingen
@router.get("/i/{key}/{size}.jpg")
def afbeelding(request: Request, key: str, size: str):
    if size not in ("t", "m"): raise HTTPException(404, "Onbekende maat")
    m = re.fullmatch(r"test-(\d\d)", key)
    if m:
        p = images.testpad(int(m.group(1)), size)
        if not p: raise HTTPException(404, "Geen testfoto")
        return FileResponse(p, media_type="image/jpeg", headers=IMMUTABLE)
    if not KEY_RE.match(key): raise HTTPException(404, "Onbekende foto")
    r = db.one("SELECT id,key,status,uploader_id FROM photos WHERE key=?", key)
    if not r: raise HTTPException(404, "Onbekende foto")
    p = images.derived_dir(key) / f"{size}.jpg"
    if r["status"] == "zichtbaar":
        if not p.exists(): raise HTTPException(404, "Bestand ontbreekt")
        return FileResponse(p, media_type="image/jpeg", headers=IMMUTABLE)
    u = auth.current_user(request)
    if r["status"] != "verwijderd" and u and (auth.is_admin(u) or u["id"] == r["uploader_id"]) and p.exists():
        return FileResponse(p, media_type="image/jpeg", headers=PRIVAAT)
    raise HTTPException(404, "Onbekende foto")


@router.get("/o/{foto_id}")
def origineel(request: Request, foto_id: int):
    r = db.one("SELECT * FROM photos WHERE id=?", foto_id)
    if not r or r["status"] == "verwijderd": raise HTTPException(404, "Onbekende foto")
    u = auth.current_user(request)
    mag = (r["status"] == "zichtbaar" and r["orig_public"]) or (u and (auth.is_admin(u) or u["id"] == r["uploader_id"]))
    if not mag: raise HTTPException(403, "Het origineel van deze foto is niet vrij te downloaden.")
    p = images.orig_path(r["sha256"], r["ext"])
    if not p.exists(): raise HTTPException(404, "Origineel ontbreekt")
    naam = f"{r['event_slug']}-{r['id']}.{r['ext']}"
    return FileResponse(p, filename=naam, headers=PRIVAAT)


# ---------------------------------------------------------------- pagina's
@router.get("/")
def home(request: Request):
    nieuw = [web.foto_json(r) for r in db.all_("SELECT * FROM photos WHERE status='zichtbaar' ORDER BY uploaded_at DESC, id DESC LIMIT 24")]
    per_event = db.all_("SELECT event_slug, COUNT(*) n, MAX(uploaded_at) laatst FROM photos WHERE status='zichtbaar' GROUP BY event_slug ORDER BY laatst DESC")
    evs = archive.events()
    lijst = [{"slug": r["event_slug"], "naam": archive.event_naam(r["event_slug"]), "n": r["n"], "jaar": (evs.get(r["event_slug"]) or {}).get("year")} for r in per_event]
    totaal = db.one("SELECT COUNT(*) n FROM photos WHERE status='zichtbaar'")["n"]
    return web.render(request, "index.html", nieuw=nieuw, wedstrijden=lijst, totaal=totaal)


@router.get("/wedstrijd/{slug}")
def wedstrijd(request: Request, slug: str):
    ev = archive.event(slug)
    fotos = [web.foto_json(r) for r in web.zichtbare_fotos("event_slug=?", slug)]
    u = auth.current_user(request)
    eigen = []
    if u:
        eigen = [web.foto_json(r, met_status=True) for r in db.all_("SELECT * FROM photos WHERE event_slug=? AND uploader_id=? AND status IN ('wacht','verborgen') ORDER BY id", slug, u["id"])]
    if not ev and not fotos: raise HTTPException(404, "Deze wedstrijd staat niet in het archief.")
    return web.render(request, "wedstrijd.html", slug=slug, ev=ev, fotos=fotos, eigen=eigen, site_url=f"{config.SITE_URL}/wedstrijd/{slug}.html")


@router.get("/foto/{foto_id}")
def foto(request: Request, foto_id: int):
    r = db.one("SELECT * FROM photos WHERE id=?", foto_id)
    u = auth.current_user(request)
    if not r or r["status"] == "verwijderd": raise HTTPException(404, "Deze foto bestaat niet (meer).")
    eigenaar = bool(u and u["id"] == r["uploader_id"])
    if r["status"] != "zichtbaar" and not (eigenaar or auth.is_admin(u)): raise HTTPException(404, "Deze foto is niet zichtbaar.")
    up = db.one("SELECT id,name FROM users WHERE id=?", r["uploader_id"])
    buren = db.all_("SELECT id FROM photos WHERE event_slug=? AND status='zichtbaar' ORDER BY COALESCE(taken_at, uploaded_at), id", r["event_slug"])
    ids = [b["id"] for b in buren]
    i = ids.index(foto_id) if foto_id in ids else -1
    vorige = ids[i - 1] if i > 0 else None
    volgende = ids[i + 1] if 0 <= i < len(ids) - 1 else None
    tags = db.all_("SELECT * FROM tags WHERE photo_id=? AND status!='verwijderd' ORDER BY id", foto_id)
    meldingen = db.all_("SELECT r.*, u.name uname FROM reports r LEFT JOIN users u ON u.id=r.user_id WHERE photo_id=? ORDER BY id DESC", foto_id) if auth.is_admin(u) else []
    return web.render(request, "foto.html", f=r, fj=web.foto_json(r, met_status=True), uploader=up, eigenaar=eigenaar, vorige=vorige, volgende=volgende,
                      tags=tags, meldingen=meldingen, ev=archive.event(r["event_slug"]), orig=images.orig_path(r["sha256"], r["ext"]).exists())


@router.post("/foto/{foto_id}/melden")
def melden(request: Request, foto_id: int, reden: str = Form(...), csrf: str = Form("")):
    auth.check_csrf(request, csrf)
    auth.rate_limit(request, "melden", 10)
    r = db.one("SELECT id, event_slug FROM photos WHERE id=? AND status='zichtbaar'", foto_id)
    if not r: raise HTTPException(404, "Deze foto bestaat niet.")
    reden = reden.strip()[:500]
    if len(reden) < 3: raise HTTPException(400, "Geef een korte reden op.")
    u = auth.current_user(request)
    db.db().execute("INSERT INTO reports(photo_id,user_id,reason,created_at) VALUES(?,?,?,?)", (foto_id, u["id"] if u else None, reden, db.now()))
    db.log("melding", u["id"] if u else None, photo_id=foto_id, event_slug=r["event_slug"], detail=reden)
    mail.mail_admins("Foto gemeld", f"Foto {config.APP_URL}/foto/{foto_id} is gemeld" + (f" door {u['name']}" if u else " (anoniem)") + f":\n\n{reden}\n\n{config.APP_URL}/beheer?tab=meldingen\n")
    return web.redirect(f"/foto/{foto_id}", "Bedankt, de melding is doorgegeven aan de beheerder.")

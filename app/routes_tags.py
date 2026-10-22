"""Fase F3: wie staat erop. Een tag is een naam bij de hele foto (rider uit het archief, of een vrije naam).

Regels:
- Taggen mag elk bevestigd account. Nieuw account: de tag wacht op beoordeling. Rider of vertrouwd: direct zichtbaar.
- Jeugd: is de getagde rider in het archief alleen in jeugdklassen te vinden, of zijn alle klassen van de wedstrijd jeugdklassen,
  dan wacht de tag tot de rider zelf (geclaimd account) of de beheerder hem goedkeurt. Tagt een rider zichzelf, dan is dat toestemming.
- Weghalen mag: de beheerder, de rider zelf, wie de tag zette, en de uploader van de foto.
- Goedkeuren mag: de beheerder en de rider zelf.
"""
from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import JSONResponse

from . import db, auth, archive, web

router = APIRouter()


def _antwoord(request, data, terug):
    """Formulieren op /beheer en /account sturen terug naar een pagina; de api geeft json."""
    if terug and terug.startswith("/") and not terug.startswith("//"):
        return web.redirect(terug, data.get("bericht"))
    return JSONResponse(data)


def tag_status(u, person_id, event_slug) -> str:
    if person_id and u["person_id"] == person_id: return "zichtbaar"   # jezelf taggen is toestemming
    if not auth.mag_direct(u): return "wacht"
    if person_id and archive.rider_youth(person_id): return "wacht"
    if archive.event_youth_only(event_slug): return "wacht"
    return "zichtbaar"


@router.post("/api/foto/{foto_id}/tag")
def tag_toevoegen(request: Request, foto_id: int, person_id: str = Form(""), naam: str = Form(""), csrf: str = Form(""), terug: str = Form("")):
    u = auth.require_verified(request); auth.check_csrf(request, csrf)
    auth.rate_limit(request, "tag", 200, 3600)
    r = db.one("SELECT * FROM photos WHERE id=?", foto_id)
    if not r or r["status"] == "verwijderd": raise HTTPException(404, "Onbekende foto.")
    if r["status"] != "zichtbaar" and not (auth.is_admin(u) or r["uploader_id"] == u["id"]): raise HTTPException(404, "Onbekende foto.")
    person_id = person_id.strip()
    naam = " ".join(naam.split())[:60]
    if person_id:
        if not archive.rider(person_id): raise HTTPException(400, "Kies een rider uit de lijst, of typ een naam.")
        bestaat = db.one("SELECT id FROM tags WHERE photo_id=? AND person_id=? AND status!='verwijderd'", foto_id, person_id)
        naam = None
    elif len(naam) >= 2:
        bestaat = db.one("SELECT id FROM tags WHERE photo_id=? AND person_id IS NULL AND status!='verwijderd' AND lower(name)=lower(?)", foto_id, naam)
        person_id = None
    else:
        raise HTTPException(400, "Kies een rider of typ een naam.")
    if bestaat: raise HTTPException(409, "Die naam staat al bij deze foto.")
    status = tag_status(u, person_id, r["event_slug"])
    cur = db.db().execute("INSERT INTO tags(photo_id,person_id,name,tagged_by,created_at,status) VALUES(?,?,?,?,?,?)", (foto_id, person_id, naam, u["id"], db.now(), status))
    db.log("tag", u["id"], photo_id=foto_id, event_slug=r["event_slug"], detail=f"{person_id or naam}: {status}")
    t = db.one("SELECT * FROM tags WHERE id=?", cur.lastrowid)
    tj = web.tag_json(t, u, r)
    tj["bericht"] = "Tag geplaatst." if status == "zichtbaar" else "Tag geplaatst; hij wordt zichtbaar na goedkeuring door de rider of de beheerder."
    return _antwoord(request, {"ok": True, "tag": tj, "bericht": tj["bericht"]}, terug)


def _tag(tid):
    t = db.one("SELECT * FROM tags WHERE id=? AND status!='verwijderd'", tid)
    if not t: raise HTTPException(404, "Onbekende tag.")
    return t, db.one("SELECT * FROM photos WHERE id=?", t["photo_id"])


@router.post("/api/tag/{tid}/verwijder")
def tag_verwijderen(request: Request, tid: int, csrf: str = Form(""), terug: str = Form("")):
    u = auth.require_user(request); auth.check_csrf(request, csrf)
    t, r = _tag(tid)
    if not web.tag_json(t, u, r)["kan_weg"]: raise HTTPException(403, "Deze tag mag je niet weghalen.")
    db.db().execute("UPDATE tags SET status='verwijderd' WHERE id=?", (tid,))
    db.log("tag.verwijderd", u["id"], photo_id=t["photo_id"], event_slug=r["event_slug"] if r else None, detail=t["person_id"] or t["name"])
    return _antwoord(request, {"ok": True, "bericht": "Tag weggehaald."}, terug)


@router.post("/api/tag/{tid}/goedkeuren")
def tag_goedkeuren(request: Request, tid: int, csrf: str = Form(""), terug: str = Form("")):
    u = auth.require_user(request); auth.check_csrf(request, csrf)
    t, r = _tag(tid)
    if not web.tag_json(t, u, r)["kan_ok"]: raise HTTPException(403, "Deze tag mag je niet goedkeuren.")
    db.db().execute("UPDATE tags SET status='zichtbaar' WHERE id=?", (tid,))
    db.log("tag.goedgekeurd", u["id"], photo_id=t["photo_id"], event_slug=r["event_slug"] if r else None, detail=t["person_id"] or t["name"])
    return _antwoord(request, {"ok": True, "bericht": "Tag goedgekeurd."}, terug)


@router.post("/account/profiel")
def profiel(request: Request, verberg: str = Form(""), csrf: str = Form("")):
    """'Toon geen foto's op mijn profiel': geen foto's op de riderpagina en geen tags met deze naam, tot de rider het weer aanzet."""
    u = auth.require_user(request); auth.check_csrf(request, csrf)
    if not u["person_id"]: raise HTTPException(400, "Je account is niet aan een rider gekoppeld.")
    aan = 1 if verberg in ("1", "on", "ja") else 0
    db.db().execute("UPDATE users SET hide_profile=? WHERE id=?", (aan, u["id"]))
    db.log("profiel.verbergen" if aan else "profiel.tonen", u["id"], detail=u["person_id"])
    return web.redirect("/account", "Foto's op je profiel staan nu " + ("uit." if aan else "aan."))

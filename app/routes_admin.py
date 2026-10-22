"""Beheer: wachtrij, meldingen, claims en claimcodes, accounts, log, mail-outbox en de archiefkoppeling. Alles onder /beheer, alleen voor admin."""
import secrets

from fastapi import APIRouter, Request, HTTPException, Form

from . import config, db, auth, archive, web, mail
from .routes_account import verwijder_foto

router = APIRouter()
TABS = ("wachtrij", "meldingen", "claims", "accounts", "log", "mail", "archief")


@router.get("/beheer")
def beheer(request: Request, tab: str = "wachtrij", account: int | None = None, wedstrijd: str = ""):
    auth.require_admin(request)
    if tab not in TABS: tab = "wachtrij"
    ctx = {"tab": tab}
    if tab == "wachtrij":
        rows = db.all_("SELECT p.*, u.name uname, u.email uemail, u.trusted utrusted FROM photos p JOIN users u ON u.id=p.uploader_id WHERE p.status='wacht' ORDER BY p.uploader_id, p.event_slug, p.id")
        groepen = {}
        for r in rows: groepen.setdefault((r["uploader_id"], r["uname"], r["uemail"], r["event_slug"]), []).append(web.foto_json(r, met_status=True))
        ctx["groepen"] = groepen
        ctx["verborgen"] = db.all_("SELECT p.*, u.name uname FROM photos p JOIN users u ON u.id=p.uploader_id WHERE p.status='verborgen' ORDER BY p.status_at DESC LIMIT 100")
        ctx["tags_wacht"] = [dict(t, naam=t["name"] or archive.rider_naam(t["person_id"]), jeugd=bool(t["person_id"] and archive.rider_youth(t["person_id"])))
                             for t in db.all_("SELECT t.*, p.key, p.event_slug, u.name tagger FROM tags t JOIN photos p ON p.id=t.photo_id JOIN users u ON u.id=t.tagged_by WHERE t.status='wacht' ORDER BY t.id")]
    elif tab == "meldingen":
        ctx["open"] = db.all_("SELECT r.*, u.name uname, p.event_slug, p.status pstatus, p.key FROM reports r LEFT JOIN users u ON u.id=r.user_id JOIN photos p ON p.id=r.photo_id WHERE r.handled_at IS NULL ORDER BY r.id DESC")
        ctx["afgehandeld"] = db.all_("SELECT r.*, u.name uname, p.event_slug FROM reports r LEFT JOIN users u ON u.id=r.user_id JOIN photos p ON p.id=r.photo_id WHERE r.handled_at IS NOT NULL ORDER BY r.id DESC LIMIT 50")
    elif tab == "claims":
        claims = db.all_("SELECT c.*, u.name uname, u.email uemail, u.created_at ucreated FROM claims c JOIN users u ON u.id=c.user_id WHERE c.status='wacht' ORDER BY c.id")
        ctx["claims"] = [dict(c, rider=archive.rider(c["person_id"]), al_gekoppeld=db.one("SELECT name FROM users WHERE person_id=? AND id!=?", c["person_id"], c["user_id"]),
                              n_fotos=db.one("SELECT COUNT(*) n FROM photos WHERE uploader_id=? AND status!='verwijderd'", c["user_id"])["n"]) for c in claims]
        ctx["codes"] = db.all_("SELECT * FROM claim_codes WHERE used_by IS NULL ORDER BY created_at DESC")
        ctx["beslist"] = db.all_("SELECT c.*, u.name uname FROM claims c JOIN users u ON u.id=c.user_id WHERE c.status!='wacht' ORDER BY c.decided_at DESC LIMIT 30")
    elif tab == "accounts":
        ctx["accounts"] = db.all_("""SELECT u.*, (SELECT COUNT(*) FROM photos p WHERE p.uploader_id=u.id AND p.status='zichtbaar') n_zichtbaar,
            (SELECT COUNT(*) FROM photos p WHERE p.uploader_id=u.id AND p.status='wacht') n_wacht,
            (SELECT COUNT(*) FROM photos p WHERE p.uploader_id=u.id AND p.status IN ('verborgen','verwijderd')) n_weg,
            (SELECT COUNT(*) FROM reports r JOIN photos p ON p.id=r.photo_id WHERE p.uploader_id=u.id) n_meldingen
            FROM users u ORDER BY u.created_at DESC""")
    elif tab == "log":
        sql, args = "SELECT l.*, u.name uname FROM log l LEFT JOIN users u ON u.id=l.user_id", []
        where = []
        if account: where.append("(l.user_id=? OR l.target_user=?)"); args += [account, account]
        if wedstrijd: where.append("l.event_slug=?"); args.append(wedstrijd)
        if where: sql += " WHERE " + " AND ".join(where)
        ctx["regels"] = db.all_(sql + " ORDER BY l.id DESC LIMIT 300", *args)
        ctx["account_f"], ctx["wedstrijd_f"] = account, wedstrijd
    elif tab == "mail":
        ctx["mails"] = db.all_("SELECT * FROM outbox ORDER BY id DESC LIMIT 50")
        ctx["smtp"] = bool(config.SMTP_HOST)
    elif tab == "archief":
        ctx["status"] = archive.status()
    return web.render(request, "beheer.html", **ctx)


def _foto(foto_id):
    r = db.one("SELECT * FROM photos WHERE id=?", foto_id)
    if not r: raise HTTPException(404, "Onbekende foto.")
    return r


def _mail_uploader(uid, onderwerp, tekst):
    u = db.one("SELECT email FROM users WHERE id=?", uid)
    if u: mail.send(u["email"], onderwerp, tekst)


@router.post("/beheer/foto/{foto_id}/{actie}")
def beheer_foto(request: Request, foto_id: int, actie: str, reden: str = Form(""), csrf: str = Form(""), terug: str = Form("/beheer")):
    a = auth.require_admin(request); auth.check_csrf(request, csrf)
    r = _foto(foto_id)
    reden = " ".join(reden.split())[:300]
    if actie == "goedkeuren":
        db.db().execute("UPDATE photos SET status='zichtbaar', status_reason=NULL, status_by=?, status_at=? WHERE id=?", (a["id"], db.now(), foto_id))
        db.log("foto.goedgekeurd", a["id"], photo_id=foto_id, event_slug=r["event_slug"], target_user=r["uploader_id"])
    elif actie == "verbergen":
        db.db().execute("UPDATE photos SET status='verborgen', status_reason=?, status_by=?, status_at=? WHERE id=?", (reden or None, a["id"], db.now(), foto_id))
        db.log("foto.verborgen", a["id"], photo_id=foto_id, event_slug=r["event_slug"], target_user=r["uploader_id"], detail=reden)
        if r["status"] != "verborgen":
            naam = db.one("SELECT name FROM users WHERE id=?", r["uploader_id"])
            onderwerp, tekst = mail.foto_beslist(naam["name"] if naam else "", 1, False, reden)
            _mail_uploader(r["uploader_id"], onderwerp, tekst)
    elif actie == "verwijderen":
        verwijder_foto(r, a["id"], reden or "door beheer verwijderd")
    else:
        raise HTTPException(400, "Onbekende actie.")
    return web.redirect(terug if terug.startswith("/") else "/beheer", f"Foto {foto_id}: {actie}.")


@router.post("/beheer/account/{uid}/goedkeuren-alles")
def goedkeuren_alles(request: Request, uid: int, wedstrijd: str = Form(""), csrf: str = Form("")):
    a = auth.require_admin(request); auth.check_csrf(request, csrf)
    sql, args = "SELECT id, event_slug FROM photos WHERE uploader_id=? AND status='wacht'", [uid]
    if wedstrijd: sql += " AND event_slug=?"; args.append(wedstrijd)
    rows = db.all_(sql, *args)
    for r in rows:
        db.db().execute("UPDATE photos SET status='zichtbaar', status_by=?, status_at=? WHERE id=?", (a["id"], db.now(), r["id"]))
        db.log("foto.goedgekeurd", a["id"], photo_id=r["id"], event_slug=r["event_slug"], target_user=uid)
    if rows:
        naam = db.one("SELECT name FROM users WHERE id=?", uid)
        onderwerp, tekst = mail.foto_beslist(naam["name"] if naam else "", len(rows), True, "")
        _mail_uploader(uid, onderwerp, tekst)
    return web.redirect("/beheer?tab=wachtrij", f"{len(rows)} foto's goedgekeurd.")


@router.post("/beheer/claim/{claim_id}/{actie}")
def beheer_claim(request: Request, claim_id: int, actie: str, toelichting: str = Form(""), csrf: str = Form("")):
    a = auth.require_admin(request); auth.check_csrf(request, csrf)
    c = db.one("SELECT c.*, u.name uname, u.email uemail FROM claims c JOIN users u ON u.id=c.user_id WHERE c.id=? AND c.status='wacht'", claim_id)
    if not c: raise HTTPException(404, "Deze claim is al beoordeeld.")
    toelichting = " ".join(toelichting.split())[:300]
    if actie == "goedkeuren":
        with db.tx() as con:
            con.execute("UPDATE claims SET status='goedgekeurd', decided_by=?, decided_at=?, decision_note=? WHERE id=?", (a["id"], db.now(), toelichting, claim_id))
            con.execute("UPDATE users SET person_id=?, trusted=1 WHERE id=?", (c["person_id"], c["user_id"]))
            con.execute("UPDATE claims SET status='afgewezen', decided_by=?, decided_at=?, decision_note='andere claim goedgekeurd' WHERE user_id=? AND status='wacht'", (a["id"], db.now(), c["user_id"]))
        db.log("claim.goedgekeurd", a["id"], target_user=c["user_id"], detail=c["person_id"])
    elif actie == "afwijzen":
        db.db().execute("UPDATE claims SET status='afgewezen', decided_by=?, decided_at=?, decision_note=? WHERE id=?", (a["id"], db.now(), toelichting, claim_id))
        db.log("claim.afgewezen", a["id"], target_user=c["user_id"], detail=f"{c['person_id']}: {toelichting}")
    else:
        raise HTTPException(400, "Onbekende actie.")
    onderwerp, tekst = mail.claim_beslist(c["uname"], archive.rider_naam(c["person_id"]), actie == "goedkeuren", toelichting)
    mail.send(c["uemail"], onderwerp, tekst)
    return web.redirect("/beheer?tab=claims", f"Claim van {c['uname']}: {actie}.")


@router.post("/beheer/code")
def beheer_code(request: Request, person_id: str = Form(""), csrf: str = Form("")):
    a = auth.require_admin(request); auth.check_csrf(request, csrf)
    r = archive.rider(person_id)
    if not r: raise HTTPException(400, "Kies een rider uit de lijst.")
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    db.db().execute("INSERT INTO claim_codes(code,person_id,created_by,created_at) VALUES(?,?,?,?)", (code, person_id, a["id"], db.now()))
    db.log("code.aangemaakt", a["id"], detail=f"{code} -> {person_id}")
    return web.redirect("/beheer?tab=claims", f"Claimcode voor {r['name']}: {code} (geef die door; invullen op {config.APP_URL}/claim).")


@router.post("/beheer/account/{uid}/{actie}")
def beheer_account(request: Request, uid: int, actie: str, reden: str = Form(""), csrf: str = Form("")):
    a = auth.require_admin(request); auth.check_csrf(request, csrf)
    u = db.one("SELECT * FROM users WHERE id=?", uid)
    if not u: raise HTTPException(404, "Onbekend account.")
    if uid == a["id"] and actie in ("blokkeren", "geen-admin"): raise HTTPException(400, "Niet op je eigen account.")
    reden = " ".join(reden.split())[:300]
    sql = {"vertrouwd": "UPDATE users SET trusted=1 WHERE id=?", "onvertrouwd": "UPDATE users SET trusted=0 WHERE id=?",
           "deblokkeren": "UPDATE users SET blocked=0, blocked_reason=NULL WHERE id=?",
           "admin": "UPDATE users SET role='admin', trusted=1, email_verified=1 WHERE id=?", "geen-admin": "UPDATE users SET role='lid' WHERE id=?",
           "ontkoppelen": "UPDATE users SET person_id=NULL WHERE id=?"}.get(actie)
    if actie == "blokkeren":
        with db.tx() as con:
            con.execute("UPDATE users SET blocked=1, blocked_reason=? WHERE id=?", (reden or None, uid))
            # alles van dit account uit het zicht, niet weg: foto's die zichtbaar of in de wachtrij stonden worden verborgen
            con.execute("UPDATE photos SET status='verborgen', status_reason=?, status_by=?, status_at=? WHERE uploader_id=? AND status IN ('zichtbaar','wacht')",
                        (f"account geblokkeerd: {reden}" if reden else "account geblokkeerd", a["id"], db.now(), uid))
    elif sql:
        db.db().execute(sql, (uid,))
    else:
        raise HTTPException(400, "Onbekende actie.")
    db.log(f"account.{actie}", a["id"], target_user=uid, detail=reden)
    return web.redirect("/beheer?tab=accounts", f"{u['name']}: {actie}.")


@router.post("/beheer/melding/{mid}/afgehandeld")
def melding_afgehandeld(request: Request, mid: int, notitie: str = Form(""), csrf: str = Form("")):
    a = auth.require_admin(request); auth.check_csrf(request, csrf)
    db.db().execute("UPDATE reports SET handled_by=?, handled_at=?, handled_note=? WHERE id=?", (a["id"], db.now(), " ".join(notitie.split())[:300] or None, mid))
    db.log("melding.afgehandeld", a["id"], detail=f"melding {mid}: {notitie}")
    return web.redirect("/beheer?tab=meldingen", "Melding afgehandeld.")


@router.post("/beheer/archief/verversen")
def archief_verversen(request: Request, csrf: str = Form("")):
    a = auth.require_admin(request); auth.check_csrf(request, csrf)
    uitkomst = archive.ververs(force=True)
    db.log("archief.ververst", a["id"], detail=uitkomst)
    return web.redirect("/beheer?tab=archief", uitkomst)


@router.post("/beheer/mail/{mid}/opnieuw")
def mail_opnieuw(request: Request, mid: int, csrf: str = Form("")):
    auth.require_admin(request); auth.check_csrf(request, csrf)
    m = db.one("SELECT * FROM outbox WHERE id=?", mid)
    if not m: raise HTTPException(404, "Onbekende mail.")
    mail.send(m["to_addr"], m["subject"], m["body"], achtergrond=False)
    return web.redirect("/beheer?tab=mail", "Opnieuw geprobeerd; zie de bovenste regel.")

"""Accounts (registreren, bevestigen, inloggen, wachtwoord), ridernaam claimen, en uploaden en bewerken van eigen foto's."""
import datetime
import json
import re
import secrets
import shutil

from fastapi import APIRouter, Request, HTTPException, Form, File, UploadFile
from fastapi.responses import JSONResponse, FileResponse
from starlette.concurrency import run_in_threadpool

from . import config, db, auth, images, archive, web, mail, naakt, gelijkenis

router = APIRouter()
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


MISLUKT_PER_ACCOUNT = 30   # mislukte inlogpogingen per account per uur; daarna een uur niet (tegen raden vanaf veel adressen tegelijk)
REGISTRATIES_PER_UUR = 30  # nieuwe accounts per uur voor de hele dienst: genoeg voor een drukke dag, te weinig om de mailbox te misbruiken


def veilig_pad(pad: str) -> bool:
    """Alleen een pad op deze dienst. '//host' en '/\\host' zijn voor een browser een ander domein, dus die niet."""
    return bool(pad) and pad.startswith("/") and not pad.startswith("//") and "\\" not in pad and not any(ord(c) < 32 for c in pad)


def _terug(pad: str) -> str:
    return pad if veilig_pad(pad) else "/account"


# ---------------------------------------------------------------- registreren en bevestigen
@router.get("/registreren")
def registreren(request: Request):
    if auth.current_user(request): return web.redirect("/account")
    return web.render(request, "registreren.html")


@router.post("/registreren")
def registreren_post(request: Request, email: str = Form(""), naam: str = Form(""), wachtwoord: str = Form(""), wachtwoord2: str = Form(""), csrf: str = Form("")):
    auth.check_csrf(request, csrf)
    auth.rate_limit(request, "registreren", 5)
    email, naam = email.strip().lower(), " ".join(naam.split())[:60]
    fout = None
    if not EMAIL_RE.match(email): fout = "Dat ziet er niet uit als een e-mailadres."
    elif len(naam) < 2: fout = "Vul je naam in (zo verschijnt hij bij je foto's)."
    elif len(wachtwoord) < 10: fout = "Kies een wachtwoord van minstens 10 tekens."
    elif wachtwoord != wachtwoord2: fout = "De twee wachtwoorden zijn niet gelijk."
    elif db.one("SELECT id FROM users WHERE email=?", email): fout = "Er is al een account met dit adres. Log in, of vraag een nieuw wachtwoord aan."
    if fout: return web.render(request, "registreren.html", status_code=400, fout=fout, email=email, naam=naam)
    if not auth.binnen_limiet("registreren:alle", REGISTRATIES_PER_UUR, 3600):
        db.log("registratie.limiet", detail=email)
        raise HTTPException(429, "Er zijn het afgelopen uur erg veel accounts aangemaakt. Probeer het over een uur nog eens.")
    cur = db.db().execute("INSERT INTO users(email,pw_hash,name,created_at) VALUES(?,?,?,?)", (email, auth.hash_pw(wachtwoord), naam, db.now()))
    uid = cur.lastrowid
    db.log("registratie", uid, detail=email)
    _stuur_bevestiging(uid, email)
    return web.render(request, "bericht.html", titel="Bijna klaar", tekst=f"We hebben een mail gestuurd naar {email}. Klik op de link in die mail om je adres te bevestigen; daarna kun je uploaden. Geen mail? Kijk in de spam, of vraag op je accountpagina een nieuwe aan.",
                      link="/inloggen", linktekst="Naar inloggen")


def _stuur_bevestiging(uid, email):
    """Zonder de ingevulde naam: het adres is nog niet bevestigd, dus anders kon iemand een eigen tekst naar een vreemd adres laten mailen."""
    t = auth.new_token(uid, "bevestig", 24)
    onderwerp, tekst = mail.bevestiging(f"{config.APP_URL}/bevestig/{t}")
    mail.send(email, onderwerp, tekst)


@router.get("/bevestig/{token}")
def bevestig(request: Request, token: str):
    uid = auth.use_token(token, "bevestig")
    if not uid: return web.render(request, "bericht.html", status_code=400, titel="Link verlopen", tekst="Deze bevestigingslink is verlopen of al gebruikt. Log in en vraag op je accountpagina een nieuwe aan.", link="/inloggen", linktekst="Naar inloggen")
    db.db().execute("UPDATE users SET email_verified=1 WHERE id=?", (uid,))
    db.log("bevestigd", uid)
    resp = web.redirect("/account", "Je e-mailadres is bevestigd. Je kunt nu foto's uploaden.")
    auth.set_cookie(resp, uid)
    return resp


# ---------------------------------------------------------------- inloggen
@router.get("/inloggen")
def inloggen(request: Request, terug: str = ""):
    if auth.current_user(request): return web.redirect(_terug(terug))
    return web.render(request, "inloggen.html", terug=terug)


@router.post("/inloggen")
def inloggen_post(request: Request, email: str = Form(""), wachtwoord: str = Form(""), terug: str = Form(""), csrf: str = Form("")):
    auth.check_csrf(request, csrf)
    auth.rate_limit(request, "inloggen", 10)
    adres = email.strip().lower()
    if auth.aantal("inloggen-mislukt:" + adres, 3600) >= MISLUKT_PER_ACCOUNT:
        raise HTTPException(429, "Te veel mislukte pogingen voor dit account. Probeer het over een uur nog eens, of vraag een nieuw wachtwoord aan.")
    u = db.one("SELECT * FROM users WHERE email=?", adres)
    if not u or not auth.check_pw(wachtwoord, u["pw_hash"]):
        auth.tel("inloggen-mislukt:" + adres)
        return web.render(request, "inloggen.html", status_code=400, fout="Onbekend adres of verkeerd wachtwoord.", email=email, terug=terug)
    db.db().execute("UPDATE users SET last_login=? WHERE id=?", (db.now(), u["id"]))
    db.log("login", u["id"])
    resp = web.redirect(_terug(terug))
    auth.set_cookie(resp, u["id"])
    return resp


@router.post("/uitloggen")
def uitloggen(request: Request, csrf: str = Form("")):
    auth.check_csrf(request, csrf)
    resp = web.redirect("/", "Je bent uitgelogd.")
    auth.clear_cookie(resp)
    return resp


# ---------------------------------------------------------------- wachtwoord vergeten
@router.get("/wachtwoord-vergeten")
def vergeten(request: Request):
    return web.render(request, "wachtwoord.html", stap="vragen")


@router.post("/wachtwoord-vergeten")
def vergeten_post(request: Request, email: str = Form(""), csrf: str = Form("")):
    auth.check_csrf(request, csrf)
    auth.rate_limit(request, "vergeten", 5)
    u = db.one("SELECT * FROM users WHERE email=?", email.strip().lower())
    # hooguit 3 per adres per uur, stil (dezelfde melding): zo kan niemand via deze dienst iemands postvak volmailen
    if u and auth.binnen_limiet("vergeten-adres:" + u["email"], 3, 3600):
        t = auth.new_token(u["id"], "reset", 2)
        onderwerp, tekst = mail.wachtwoord_reset(u["name"] if u["email_verified"] else "", f"{config.APP_URL}/wachtwoord/{t}")
        mail.send(u["email"], onderwerp, tekst)
    return web.render(request, "bericht.html", titel="Mail onderweg", tekst="Als dit adres bij ons bekend is, staat er binnen een paar minuten een mail in je postvak met een link om een nieuw wachtwoord in te stellen (2 uur geldig).")


@router.get("/wachtwoord/{token}")
def nieuw_wachtwoord(request: Request, token: str):
    r = db.one("SELECT * FROM tokens WHERE token=? AND kind='reset' AND used=0", token)
    if not r or r["expires_at"] < db.now(): raise HTTPException(400, "Deze link is verlopen of al gebruikt. Vraag een nieuwe aan.")
    return web.render(request, "wachtwoord.html", stap="instellen", token=token)


@router.post("/wachtwoord/{token}")
def nieuw_wachtwoord_post(request: Request, token: str, wachtwoord: str = Form(""), wachtwoord2: str = Form(""), csrf: str = Form("")):
    auth.check_csrf(request, csrf)
    if len(wachtwoord) < 10 or wachtwoord != wachtwoord2:
        return web.render(request, "wachtwoord.html", status_code=400, stap="instellen", token=token, fout="Minstens 10 tekens, en twee keer hetzelfde.")
    uid = auth.use_token(token, "reset")
    if not uid: raise HTTPException(400, "Deze link is verlopen of al gebruikt. Vraag een nieuwe aan.")
    db.db().execute("UPDATE users SET pw_hash=?, email_verified=1 WHERE id=?", (auth.hash_pw(wachtwoord), uid))
    db.log("wachtwoord.gewijzigd", uid)
    resp = web.redirect("/account", "Je wachtwoord is gewijzigd.")
    auth.set_cookie(resp, uid)
    return resp


# ---------------------------------------------------------------- account
@router.get("/account")
def account(request: Request):
    u = auth.require_user(request)
    fotos = db.all_("SELECT * FROM photos WHERE uploader_id=? AND status!='verwijderd' ORDER BY event_slug, id", u["id"])
    per_event = {}
    for r in fotos: per_event.setdefault(r["event_slug"], []).append(web.foto_json(r, met_status=True))
    claim = db.one("SELECT * FROM claims WHERE user_id=? ORDER BY id DESC LIMIT 1", u["id"])
    mijn_tags, n_op_profiel = [], 0
    if u["person_id"]:
        n_op_profiel = db.one("SELECT COUNT(DISTINCT t.photo_id) n FROM tags t JOIN photos p ON p.id=t.photo_id WHERE t.person_id=? AND t.status='zichtbaar' AND p.status='zichtbaar'", u["person_id"])["n"]
        mijn_tags = [dict(web.tag_json(t, u), key=t["key"], event_slug=t["event_slug"], tagger=t["tagger"], photo_id=t["photo_id"])
                     for t in db.all_("SELECT t.*, p.key, p.event_slug, uu.name tagger FROM tags t JOIN photos p ON p.id=t.photo_id JOIN users uu ON uu.id=t.tagged_by "
                                      "WHERE t.person_id=? AND t.status='wacht' AND p.status='zichtbaar' ORDER BY t.id", u["person_id"])]
    return web.render(request, "account.html", per_event=per_event, claim=claim, n_fotos=len(fotos), mijn_tags=mijn_tags, n_op_profiel=n_op_profiel)


@router.post("/account/bevestiging-opnieuw")
def bevestiging_opnieuw(request: Request, csrf: str = Form("")):
    u = auth.require_user(request); auth.check_csrf(request, csrf)
    auth.rate_limit(request, "bevestiging", 3, 3600)
    if u["email_verified"]: return web.redirect("/account", "Je adres is al bevestigd.")
    _stuur_bevestiging(u["id"], u["email"])
    return web.redirect("/account", f"Er is een nieuwe bevestigingsmail gestuurd naar {u['email']}.")


@router.post("/account/naam")
def naam_wijzigen(request: Request, naam: str = Form(""), csrf: str = Form("")):
    u = auth.require_user(request); auth.check_csrf(request, csrf)
    naam = " ".join(naam.split())[:60]
    if len(naam) < 2: raise HTTPException(400, "Vul een naam in.")
    db.db().execute("UPDATE users SET name=? WHERE id=?", (naam, u["id"]))
    return web.redirect("/account", "Je naam is gewijzigd.")


@router.post("/account/wachtwoord")
def wachtwoord_wijzigen(request: Request, huidig: str = Form(""), wachtwoord: str = Form(""), wachtwoord2: str = Form(""), csrf: str = Form("")):
    u = auth.require_user(request); auth.check_csrf(request, csrf)
    if not auth.check_pw(huidig, u["pw_hash"]): raise HTTPException(400, "Het huidige wachtwoord klopt niet.")
    if len(wachtwoord) < 10 or wachtwoord != wachtwoord2: raise HTTPException(400, "Minstens 10 tekens, en twee keer hetzelfde.")
    db.db().execute("UPDATE users SET pw_hash=? WHERE id=?", (auth.hash_pw(wachtwoord), u["id"]))
    db.log("wachtwoord.gewijzigd", u["id"])
    resp = web.redirect("/account", "Je wachtwoord is gewijzigd. Op andere apparaten ben je nu uitgelogd.")
    auth.set_cookie(resp, u["id"])   # deze sessie blijft ingelogd; alle andere vervallen (nieuw wachtwoordmerk)
    return resp


# ---------------------------------------------------------------- ridernaam claimen
@router.get("/claim")
def claim(request: Request):
    u = auth.require_user(request)
    open_claim = db.one("SELECT * FROM claims WHERE user_id=? AND status='wacht'", u["id"])
    return web.render(request, "claim.html", open_claim=open_claim)


@router.post("/claim")
def claim_post(request: Request, person_id: str = Form(""), toelichting: str = Form(""), csrf: str = Form("")):
    u = auth.require_verified(request); auth.check_csrf(request, csrf)
    if u["person_id"]: return web.redirect("/account", "Je account is al aan een rider gekoppeld.")
    if db.one("SELECT id FROM claims WHERE user_id=? AND status='wacht'", u["id"]): return web.redirect("/claim", "Je hebt al een claim openstaan.")
    r = archive.rider(person_id)
    if not r: raise HTTPException(400, "Kies een rider uit de lijst.")
    toelichting = " ".join(toelichting.split())[:300]
    db.db().execute("INSERT INTO claims(user_id,person_id,note,created_at) VALUES(?,?,?,?)", (u["id"], person_id, toelichting, db.now()))
    db.log("claim.aangevraagd", u["id"], detail=f"{person_id}: {toelichting}")
    ander = db.one("SELECT name FROM users WHERE person_id=?", person_id)
    mail.mail_admins("Nieuwe claim op een ridernaam", f"{u['name']} ({u['email']}) claimt {r['name']} ({person_id}).\n\nToelichting: {toelichting or '-'}\n"
                     + (f"\nLET OP: {ander['name']} is al aan deze rider gekoppeld.\n" if ander else "") + f"\n{config.APP_URL}/beheer?tab=claims\n")
    return web.redirect("/account", f"Je claim op {r['name']} is ingediend. Je krijgt een mail zodra hij beoordeeld is.")


@router.post("/claim/code")
def claim_code(request: Request, code: str = Form(""), csrf: str = Form("")):
    u = auth.require_verified(request); auth.check_csrf(request, csrf)
    auth.rate_limit(request, "claimcode", 10)
    if u["person_id"]: return web.redirect("/account", "Je account is al aan een rider gekoppeld.")
    code = code.strip().upper().replace(" ", "")
    c = db.one("SELECT * FROM claim_codes WHERE code=? AND used_by IS NULL", code)
    if not c: return web.render(request, "claim.html", status_code=400, fout="Deze code is onbekend of al gebruikt.", open_claim=None)
    with db.tx() as con:
        con.execute("UPDATE claim_codes SET used_by=?, used_at=? WHERE code=?", (u["id"], db.now(), code))
        con.execute("UPDATE users SET person_id=?, trusted=1 WHERE id=?", (c["person_id"], u["id"]))
        con.execute("INSERT INTO claims(user_id,person_id,note,status,decided_at,decision_note,created_at) VALUES(?,?,?,?,?,?,?)",
                    (u["id"], c["person_id"], "claimcode", "goedgekeurd", db.now(), f"code {code}", db.now()))
    db.log("code.gebruikt", u["id"], detail=f"{code} -> {c['person_id']}")
    return web.redirect("/account", f"Je account is gekoppeld aan {archive.rider_naam(c['person_id'])}. Je foto's zijn vanaf nu direct zichtbaar.")


# ---------------------------------------------------------------- uploaden
@router.get("/upload")
def upload(request: Request, wedstrijd: str = ""):
    u = auth.require_user(request)
    ev = archive.event(wedstrijd) if wedstrijd else None
    return web.render(request, "upload.html", ev=ev, slug=wedstrijd, archief_leeg=not archive.events(), mag_direct=auth.mag_direct(u))


@router.get("/api/mijn")
def api_mijn(request: Request, wedstrijd: str):
    u = auth.require_user(request)
    rows = db.all_("SELECT * FROM photos WHERE uploader_id=? AND event_slug=? AND status!='verwijderd' ORDER BY id", u["id"], wedstrijd)
    return {"fotos": [web.foto_json(r, met_status=True) for r in rows]}


def _dagtelling(uid) -> int:
    grens = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)).isoformat(timespec="seconds")
    return db.one("SELECT COUNT(*) n FROM photos WHERE uploader_id=? AND uploaded_at>? AND status!='verwijderd'", uid, grens)["n"]


def _datum_waarschuwing(taken_at, ev):
    if not taken_at or not ev or not ev.get("date"): return None
    try:
        d = datetime.date.fromisoformat(taken_at[:10])
        start = datetime.date.fromisoformat(ev["date"])
        eind = datetime.date.fromisoformat(ev.get("date_end") or ev["date"])
    except ValueError:
        return None
    if d < start - datetime.timedelta(days=1) or d > eind + datetime.timedelta(days=1):
        return f"Deze foto is gemaakt op {web.fmt_dt(taken_at, False)}, de wedstrijd was {web.fmt_dt(ev['date'], False)}" + (f" tot {web.fmt_dt(ev['date_end'], False)}" if ev.get("date_end") else "") + ". Klopt de wedstrijd?"
    return None


def herkomst_check(herkomst, fotograaf, licentie, naam_uploader):
    """Geeft (herkomst, credit, licentie) of een foutmelding. Bij een onbekende fotograaf kan alleen 'alleen op dit archief':
    je kunt geen Creative Commons weggeven op andermans foto."""
    if herkomst not in config.HERKOMST: raise HTTPException(400, "Geef aan wie de foto's heeft gemaakt.")
    if licentie not in config.LICENTIES: raise HTTPException(400, "Onbekende licentie.")
    fotograaf = " ".join(fotograaf.split())[:100]
    if herkomst == "eigen": return herkomst, fotograaf or naam_uploader, licentie
    if herkomst == "toestemming":
        if len(fotograaf) < 2: raise HTTPException(400, "Vul de naam van de fotograaf in.")
        return herkomst, fotograaf, licentie
    return herkomst, None, "archief"


def _upload_grenzen(u):
    """Mag dit account nu uploaden? Ook nagelopen bij het antwoord op de lijkt-op-vraag: dat kan later komen."""
    if u["upload_pauze"]:
        raise HTTPException(403, "Je uploads staan even stil tot de beheerder je vorige foto heeft bekeken. Je hoort het per mail.")
    if not auth.mag_direct(u) and _dagtelling(u["id"]) >= config.DAGLIMIET_NIEUW:
        raise HTTPException(429, f"Je kunt als nieuw account {config.DAGLIMIET_NIEUW} foto's per dag uploaden. Morgen kan het weer; zodra je ridernaam is bevestigd vervalt die grens.")


def _exact_dubbel(sha, wedstrijd):
    """Staat precies dit bestand er al? Dan een 409 met die foto. Een eerder verwijderde regel met dezelfde sha gaat weg (de log houdt de geschiedenis)."""
    dup = db.one("SELECT * FROM photos WHERE sha256=?", sha)
    if dup and dup["status"] != "verwijderd":
        return JSONResponse({"fout": "Deze foto staat er al" + (" (bij een andere wedstrijd)" if dup["event_slug"] != wedstrijd else "") + ".", "dubbel": web.foto_json(dup, met_status=True)}, status_code=409)
    if dup:
        db.db().execute("DELETE FROM photos WHERE id=?", (dup["id"],))
        oud = config.ORIG_DIR / "verwijderd" / images.orig_path(dup).name
        if oud.exists(): oud.unlink()
    return None


def _event_of_fout(wedstrijd):
    ev = archive.event(wedstrijd)
    if not ev:
        raise HTTPException(503 if not archive.events() else 400, "De wedstrijdenlijst is nu niet beschikbaar, probeer het straks nog eens." if not archive.events() else "Onbekende wedstrijd.")
    return ev


@router.post("/api/upload")
async def api_upload(request: Request, wedstrijd: str = Form(...), licentie: str = Form("archief"), fotograaf: str = Form(""), toestemming: str = Form(""),
                     herkomst: str = Form("eigen"), csrf: str = Form(""), bestand: UploadFile = File(...)):
    u = auth.require_verified(request); auth.check_csrf(request, csrf)
    _upload_grenzen(u)
    if toestemming != "ja": raise HTTPException(400, "Vink de verklaring over de foto's aan.")
    herkomst, credit, licentie = herkomst_check(herkomst, fotograaf, licentie, u["name"])
    ev = _event_of_fout(wedstrijd)
    tmp = config.DATA_DIR / "tmp" / secrets.token_hex(8)
    grootte, limiet = 0, config.MAX_UPLOAD_MB * 1024 * 1024
    with open(tmp, "wb") as f:
        while True:
            blok = await bestand.read(1 << 20)
            if not blok: break
            grootte += len(blok)
            if grootte > limiet:
                f.close(); tmp.unlink(missing_ok=True)
                raise HTTPException(413, f"Dit bestand is groter dan {config.MAX_UPLOAD_MB} MB.")
            f.write(blok)
    orig_name = (bestand.filename or "")[-200:]
    try:
        soort = images.herken(tmp)
        if not soort:
            raise HTTPException(415, "Dit is geen foto die we kunnen lezen (JPEG, PNG, WebP of HEIC).")
        fmt, ext = soort
        sha = images.sha256_file(tmp)
        dub = _exact_dubbel(sha, wedstrijd)
        if dub: return dub
        if gelijkenis.wacht_al(sha):
            return JSONResponse({"fout": "Deze foto wacht hierboven al op je antwoord."}, status_code=409)
        # lijkt hij op een foto die er al staat (opnieuw opgeslagen, verkleind, ...)? Dan eerst de uploader vragen (zie app/gelijkenis.py)
        ph = await run_in_threadpool(gelijkenis.vingerafdruk, tmp)
        taken = images.opnamedatum_van(tmp)
        treffers = gelijkenis.zoek(ph, taken)
        te_zien = [(d, r) for d, r in treffers if gelijkenis.kan_zien(u, r)]
        meta = {"user_id": u["id"], "wedstrijd": wedstrijd, "licentie": licentie, "credit": credit, "herkomst": herkomst, "ext": ext, "sha": sha,
                "grootte": grootte, "orig_name": orig_name, "phash": ph, "lijkt_op": gelijkenis.lijkt_op_json(treffers, gevraagd=bool(te_zien)) if treffers else None}
        if te_zien:
            token = await run_in_threadpool(gelijkenis.bewaar, tmp, meta)
            return JSONResponse({"vraag": True, "token": token, "voorbeeld": f"/api/upload/lijkt/{token}.jpg",
                                 "fout": "Deze foto lijkt op een foto die er al staat. Kies of het dezelfde foto is.",
                                 "lijkt_op": [dict(web.foto_json(r, met_status=True), afstand=d, eigen=r["uploader_id"] == u["id"],
                                                   wedstrijd_naam=(archive.event(r["event_slug"]) or {}).get("name") or r["event_slug"]) for d, r in te_zien[:3]]},
                                status_code=409)
        return await _sla_op(u, tmp, ev, meta)
    finally:
        tmp.unlink(missing_ok=True)


@router.get("/api/upload/lijkt/{token}.jpg")
def api_upload_voorbeeld(request: Request, token: str):
    """De kleine versie van een upload die op een antwoord wacht; alleen voor de uploader zelf."""
    u = auth.require_verified(request)
    p = gelijkenis.voorbeeld_pad(token, u["id"])
    if not p: raise HTTPException(404, "Geen voorbeeld")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.post("/api/upload/antwoord")
async def api_upload_antwoord(request: Request, token: str = Form(""), antwoord: str = Form(""), csrf: str = Form("")):
    """Het antwoord op 'staat deze foto er al?': zelfde = niet uploaden; anders = toch uploaden, en de beheerder kijkt even mee."""
    u = auth.require_verified(request); auth.check_csrf(request, csrf)
    if antwoord not in ("zelfde", "anders"): raise HTTPException(400, "Kies of het dezelfde foto is of een andere.")
    w = gelijkenis.haal(token, u["id"])
    if not w: raise HTTPException(410, "Deze vraag is verlopen of al beantwoord. Upload de foto opnieuw als je hem toch wilt plaatsen.")
    if antwoord == "zelfde":
        gelijkenis.weg(token)
        db.log("upload.lijkt_op_overgeslagen", u["id"], event_slug=w["wedstrijd"], detail=f"{w['orig_name'][-120:]}; {w['lijkt_op']}")
        return {"ok": True, "overgeslagen": True}
    _upload_grenzen(u)
    ev = _event_of_fout(w["wedstrijd"])
    dub = _exact_dubbel(w["sha"], w["wedstrijd"])
    if dub:
        gelijkenis.weg(token); return dub
    tmp = gelijkenis.neem(token)
    if not tmp: raise HTTPException(410, "Deze vraag is al beantwoord.")
    try:
        return await _sla_op(u, tmp, ev, w)
    finally:
        tmp.unlink(missing_ok=True)


async def _sla_op(u, tmp, ev, w):
    """Een gekeurde upload opslaan: origineel op de NAS, verkleinde versies, naaktcheck, en de regel in de database."""
    wedstrijd, sha, ext, herkomst = w["wedstrijd"], w["sha"], w["ext"], w["herkomst"]
    key = sha[:16]
    rel = images.orig_rel(wedstrijd, ev.get("year"), images.opnamedatum_van(tmp), sha, ext)
    orig = config.ORIG_DIR / rel
    orig.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(tmp), str(orig))
    try:
        info = images.maak_verkleind(orig, key)
    except Exception as e:  # noqa: BLE001
        orig.unlink(missing_ok=True); images.verwijder_verkleind(key)
        raise HTTPException(415, f"De foto kon niet verwerkt worden ({type(e).__name__}).")
    direct = auth.mag_direct(u)
    niveau, detail = await run_in_threadpool(naakt.check, images.derived_dir(key) / "m.jpg")
    if niveau in naakt.WEIGEREN:
        # lijkt bloot: geweigerd en niet bewaard, ook niet in de wachtrij (keuze van Max, 3 november 2026); alleen een logregel zonder beeld
        orig.unlink(missing_ok=True); images.verwijder_verkleind(key)
        db.log("naakt.geweigerd", u["id"], event_slug=wedstrijd, detail=f"{niveau}; {detail}; {w['orig_name'][-120:]}; sha256 {sha[:16]}")
        if niveau == "rood":
            if not direct: db.db().execute("UPDATE users SET upload_pauze=1 WHERE id=?", (u["id"],))
            mail.mail_admins("Naaktcheck: upload geweigerd", f"De naaktcheck heeft een upload van {u['name']} ({u['email']}) geweigerd ({detail}). De foto is niet bewaard. "
                             + ("Het account kan niet verder uploaden tot je op het tabblad Accounts 'Uploaden weer toestaan' kiest of het blokkeert."
                                if not direct else "") + f"\n\n{config.APP_URL}/beheer?tab=accounts\n")
        raise HTTPException(422, naakt.WEIGER_TEKST)
    status = "zichtbaar" if direct and naakt.zichtbaar_mag(niveau) else "wacht"
    if w.get("lijkt_op"): status = "wacht"     # lijkt op een foto die er al staat: de beheerder kijkt even of het echt een andere is
    cur = db.db().execute(
        "INSERT INTO photos(key,event_slug,uploader_id,sha256,ext,orig_rel,orig_name,size,width,height,taken_at,uploaded_at,credit,licence,rights_basis,status,status_at,naakt,naakt_detail,phash,lijkt_op) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (key, wedstrijd, u["id"], sha, ext, rel, w["orig_name"], w["grootte"], info["width"], info["height"], info["taken_at"], db.now(), w["credit"], w["licentie"], herkomst, status, db.now(),
         niveau, detail or None, w.get("phash"), w.get("lijkt_op")))
    fid = cur.lastrowid
    db.log("upload", u["id"], photo_id=fid, event_slug=wedstrijd, detail=f"{status}; {herkomst}; {w['orig_name']}; {w['grootte']} bytes")
    if w.get("lijkt_op"):
        lo = json.loads(w["lijkt_op"])
        db.log("upload.lijkt_op", u["id"], photo_id=fid, event_slug=wedstrijd,
               detail=("uploader: een andere foto" if lo["gevraagd"] else "niet gevraagd (de gelijkende foto is niet zichtbaar voor de uploader)") + "; lijkt op "
               + ", ".join(f"{t['id']} ({t['afstand']})" for t in lo["treffers"]))
    if not naakt.zichtbaar_mag(niveau):        # de check kon niet draaien: voor de zekerheid naar de wachtrij
        db.log(f"naakt.{niveau}", u["id"], photo_id=fid, event_slug=wedstrijd, detail=detail)
    if status == "wacht": _wachtrij_melding()
    row = db.one("SELECT * FROM photos WHERE id=?", fid)
    waarschuwing = _datum_waarschuwing(info["taken_at"], ev)
    if status == "wacht" and (direct or w.get("lijkt_op")):      # vertrouwd (of lijkt op een bestaande foto), maar toch eerst de beheerder
        waarschuwing = "; ".join(x for x in ("deze foto wordt eerst door de beheerder bekeken", waarschuwing) if x)
    return {"ok": True, "foto": web.foto_json(row, met_status=True), "waarschuwing": waarschuwing}


def _wachtrij_melding():
    n = db.one("SELECT COUNT(*) n FROM photos WHERE status='wacht'")["n"]
    laatst = db.kv_get("wachtrij.gemeld", "")
    grens = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)).isoformat(timespec="seconds")
    if n >= 10 and laatst < grens:
        db.kv_set("wachtrij.gemeld", db.now())
        mail.mail_admins("Wachtrij met foto's", f"Er staan {n} foto's te wachten op beoordeling.\n\n{config.APP_URL}/beheer?tab=wachtrij\n")


def _eigen_of_admin(request, foto_id):
    u = auth.require_user(request)
    r = db.one("SELECT * FROM photos WHERE id=?", foto_id)
    if not r or r["status"] == "verwijderd": raise HTTPException(404, "Onbekende foto.")
    if not (auth.is_admin(u) or r["uploader_id"] == u["id"]): raise HTTPException(403, "Dit is niet jouw foto.")
    return u, r


@router.post("/api/foto/{foto_id}")
def foto_bewerken(request: Request, foto_id: int, bijschrift: str = Form(""), fotograaf: str = Form(""), licentie: str = Form("archief"), herkomst: str = Form(""),
                  origineel_vrij: str = Form(""), csrf: str = Form("")):
    u, r = _eigen_of_admin(request, foto_id); auth.check_csrf(request, csrf)
    up = db.one("SELECT name FROM users WHERE id=?", r["uploader_id"])
    herkomst, credit, licentie = herkomst_check(herkomst or r["rights_basis"], fotograaf, licentie, up["name"] if up else u["name"])
    db.db().execute("UPDATE photos SET caption=?, credit=?, licence=?, rights_basis=?, orig_public=? WHERE id=?",
                    (" ".join(bijschrift.split())[:300] or None, credit, licentie, herkomst, 1 if origineel_vrij in ("1", "ja", "on") else 0, foto_id))
    db.log("foto.bewerkt", u["id"], photo_id=foto_id, event_slug=r["event_slug"])
    return {"ok": True, "foto": web.foto_json(db.one("SELECT * FROM photos WHERE id=?", foto_id), met_status=True)}


@router.post("/api/foto/{foto_id}/verwijder")
def foto_verwijderen(request: Request, foto_id: int, csrf: str = Form("")):
    u, r = _eigen_of_admin(request, foto_id); auth.check_csrf(request, csrf)
    verwijder_foto(r, u["id"], "door uploader verwijderd" if r["uploader_id"] == u["id"] else "door beheer verwijderd")
    return {"ok": True}


def verwijder_foto(r, door, reden):
    """Zet de status op verwijderd, haalt de verkleinde versies weg en zet het origineel in ORIG_DIR/verwijderd/ (niets gaat echt weg)."""
    images.verwijder_verkleind(r["key"])
    p = images.orig_path(r)
    if p.exists():
        doel = config.ORIG_DIR / "verwijderd"; doel.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(doel / p.name))
    db.db().execute("UPDATE photos SET status='verwijderd', status_reason=?, status_by=?, status_at=? WHERE id=?", (reden, door, db.now(), r["id"]))
    db.log("foto.verwijderd", door, photo_id=r["id"], event_slug=r["event_slug"], detail=reden)

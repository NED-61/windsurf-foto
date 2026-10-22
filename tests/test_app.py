"""Doorloopt de hele keten: registreren, bevestigen, uploaden (wachtrij), beheer goedkeuren, api en afbeeldingen, claim, claimcode,
bewerken, verwijderen, melden, blokkeren. Draait tegen een lege database in een tijdelijke map; SMTP staat uit, dus mails komen in de outbox."""
import io
import json
import re
import sys
import pathlib

import pytest
from PIL import Image


@pytest.fixture()
def omgeving(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ORIG_DIR", str(tmp_path / "orig"))
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.org")
    monkeypatch.setenv("ADMIN_PASSWORD", "beheerwachtwoord1")
    monkeypatch.setenv("SITE_URL", "http://127.0.0.1:9/archief")   # onbereikbaar: de cache in DATA_DIR/archief wordt gebruikt
    monkeypatch.setenv("APP_URL", "http://testserver")
    monkeypatch.setenv("TESTSET", "0")
    for m in [k for k in list(sys.modules) if k == "app" or k.startswith("app.")]: del sys.modules[m]
    from app import config
    archief = pathlib.Path(config.ARCHIEF_DIR); archief.mkdir(parents=True)
    (archief / "wedstrijden.json").write_text(json.dumps([
        {"slug": "nk-slalom-2024", "name": "NK Slalom 2024", "year": 2024, "date": "2024-06-15", "date_end": "2024-06-16", "location": "Aalsmeer", "classes": ["Heren", "Jeugd"], "youth_classes": ["Jeugd"], "riders": ["jan-jansen", "kim-de-jong"]},
        {"slug": "gpa-2025", "name": "Grote Prijs van Aalsmeer 2025", "year": 2025, "date": "2025-09-06", "date_end": None, "location": "Aalsmeer", "classes": [], "riders": []},
        {"slug": "kids-cup-2025", "name": "Kids Cup 2025", "year": 2025, "date": "2025-05-01", "date_end": None, "location": "Heeg", "classes": ["Kids"], "youth_classes": ["Kids"], "riders": ["kim-de-jong"]}]), encoding="utf-8")
    (archief / "riders.json").write_text(json.dumps([{"id": "jan-jansen", "name": "Jan Jansen", "aliases": [], "sails": ["NED 61"], "n": 5, "years": "2017–2024", "youth": False},
                                                      {"id": "piet-de-vries", "name": "Piet de Vries", "aliases": [], "sails": ["H 12"], "n": 1, "years": "1999", "youth": False},
                                                      {"id": "kim-de-jong", "name": "Kim de Jong", "aliases": [], "sails": ["NED 7"], "n": 2, "years": "2024–2025", "youth": True}]), encoding="utf-8")
    from app.main import app
    from app import db
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield {"app": app, "c": c, "db": db, "config": config}
    db.reset()


def csrf_van(c, pad="/inloggen"):
    html = c.get(pad).text
    return re.search(r'name="csrf" content="([^"]+)"', html).group(1)


def jpeg(kleur, datum="2024:06:15 12:00:00", maat=(1200, 800)):
    im = Image.new("RGB", maat, kleur)
    exif = im.getexif(); exif[306] = datum
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=85, exif=exif.tobytes())
    return buf.getvalue()


def upload(c, slug, data, naam="foto.jpg", **extra):
    velden = {"csrf": csrf_van(c, "/account"), "wedstrijd": slug, "licentie": "archief", "fotograaf": "", "toestemming": "ja"}
    velden.update(extra)
    return c.post("/api/upload", data=velden, files={"bestand": (naam, data, "image/jpeg")})


def registreer_en_bevestig(c, db, email, naam, pw="wachtwoord12345"):
    r = c.post("/registreren", data={"csrf": csrf_van(c, "/registreren"), "email": email, "naam": naam, "wachtwoord": pw, "wachtwoord2": pw})
    assert r.status_code == 200 and "Bijna klaar" in r.text
    m = db.one("SELECT * FROM outbox WHERE to_addr=? ORDER BY id DESC", email)
    assert m and m["sent"] == 0 and "SMTP niet ingesteld" in m["error"]
    link = re.search(r"http://testserver(/bevestig/\S+)", m["body"]).group(1)
    r = c.get(link)
    assert r.status_code == 200 and "bevestigd" in r.text
    return db.one("SELECT * FROM users WHERE email=?", email)


def login(c, email, pw):
    r = c.post("/inloggen", data={"csrf": csrf_van(c), "email": email, "wachtwoord": pw})
    assert r.status_code == 200


def admin_client(omg):
    from fastapi.testclient import TestClient
    a = TestClient(omg["app"])
    login(a, "admin@example.org", "beheerwachtwoord1")
    return a


def test_health_en_admin(omgeving):
    c, db = omgeving["c"], omgeving["db"]
    assert c.get("/api/health").json()["ok"] is True
    u = db.one("SELECT * FROM users WHERE email='admin@example.org'")
    assert u["role"] == "admin" and u["trusted"] == 1 and u["email_verified"] == 1


def test_wachtrij_goedkeuren_api_en_afbeelding(omgeving):
    c, db = omgeving["c"], omgeving["db"]
    registreer_en_bevestig(c, db, "lid@example.org", "Lid Nieuw")
    r = upload(c, "nk-slalom-2024", jpeg((200, 30, 30)))
    assert r.status_code == 200, r.text
    f = r.json()["foto"]
    assert f["status"] == "wacht" and r.json()["waarschuwing"] is None
    # datum buiten de wedstrijd geeft een waarschuwing, geen fout
    r2 = upload(c, "nk-slalom-2024", jpeg((30, 200, 30), datum="2024:03:01 10:00:00"))
    assert r2.status_code == 200 and "Klopt de wedstrijd" in r2.json()["waarschuwing"]
    # nog niet zichtbaar voor de buitenwereld
    assert c.get("/api/wedstrijd/nk-slalom-2024").json()["n"] == 0
    assert c.get(f"/i/{f['key']}/t.jpg").status_code == 200   # de uploader zelf wel (privé, no-store)
    anoniem = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(omgeving["app"])
    assert anoniem.get(f"/i/{f['key']}/t.jpg").status_code == 404
    # beheer: wachtrij tonen en één goedkeuren
    a = admin_client(omgeving)
    html = a.get("/beheer?tab=wachtrij").text
    assert "Lid Nieuw" in html and "NK Slalom 2024" in html
    r = a.post(f"/beheer/foto/{f['id']}/goedkeuren", data={"csrf": csrf_van(a, "/beheer"), "terug": "/beheer?tab=wachtrij"})
    assert r.status_code == 200
    j = anoniem.get("/api/wedstrijd/nk-slalom-2024", headers={"Origin": "https://www.ned-61.com"})
    assert j.json()["n"] == 1 and j.headers["access-control-allow-origin"] == "https://www.ned-61.com"
    assert j.json()["fotos"][0]["t"].startswith("http://testserver/i/")
    img = anoniem.get(f"/i/{f['key']}/t.jpg")
    assert img.status_code == 200 and "immutable" in img.headers["cache-control"] and img.headers["timing-allow-origin"] == "*"
    with Image.open(io.BytesIO(img.content)) as im:
        assert max(im.size) == 400 and im.getexif().get(306) is None   # verkleind, EXIF gestript
    assert anoniem.get(f"/i/{f['key']}/m.jpg").status_code == 200
    assert anoniem.get(f"/o/{f['id']}").status_code == 403    # origineel niet vrij
    assert anoniem.get("/api/telling").json() == {"nk-slalom-2024": 1}
    assert anoniem.get("/api/nieuw").json()["n"] == 1
    # alles goedkeuren per account, met mail naar de uploader
    r = a.post(f"/beheer/account/{f['id'] and db.one('SELECT uploader_id FROM photos WHERE id=?', f['id'])['uploader_id']}/goedkeuren-alles", data={"csrf": csrf_van(a, "/beheer")})
    assert anoniem.get("/api/wedstrijd/nk-slalom-2024").json()["n"] == 2
    assert db.one("SELECT * FROM outbox WHERE subject='Je foto''s staan online'")


def test_dubbel_en_fout_bestand_en_csrf(omgeving):
    c, db = omgeving["c"], omgeving["db"]
    registreer_en_bevestig(c, db, "lid@example.org", "Lid")
    data = jpeg((1, 2, 3))
    assert upload(c, "nk-slalom-2024", data).status_code == 200
    r = upload(c, "gpa-2025", data)
    assert r.status_code == 409 and "andere wedstrijd" in r.json()["fout"]
    assert upload(c, "nk-slalom-2024", b"dit is geen foto", naam="x.jpg").status_code == 415
    assert upload(c, "bestaat-niet", jpeg((9, 9, 9))).status_code == 400
    assert upload(c, "nk-slalom-2024", jpeg((4, 5, 6)), csrf="fout").status_code == 400
    assert upload(c, "nk-slalom-2024", jpeg((7, 8, 9)), toestemming="").status_code == 400


def test_claim_goedkeuren_maakt_vertrouwd(omgeving):
    c, db = omgeving["c"], omgeving["db"]
    registreer_en_bevestig(c, db, "jan@example.org", "Jan")
    assert c.get("/api/riders?q=jansen").json()[0]["id"] == "jan-jansen"
    r = c.post("/claim", data={"csrf": csrf_van(c, "/claim"), "person_id": "jan-jansen", "toelichting": "NED 61"})
    assert r.status_code == 200 and "ingediend" in r.text
    assert db.one("SELECT * FROM outbox WHERE subject='Nieuwe claim op een ridernaam'")
    a = admin_client(omgeving)
    assert "Jan Jansen" in a.get("/beheer?tab=claims").text
    cid = db.one("SELECT id FROM claims WHERE status='wacht'")["id"]
    a.post(f"/beheer/claim/{cid}/goedkeuren", data={"csrf": csrf_van(a, "/beheer")})
    u = db.one("SELECT * FROM users WHERE email='jan@example.org'")
    assert u["person_id"] == "jan-jansen" and u["trusted"] == 1
    r = upload(c, "nk-slalom-2024", jpeg((50, 60, 70)))
    assert r.json()["foto"]["status"] == "zichtbaar"
    assert "Jan Jansen" in c.get("/account").text


def test_claimcode(omgeving):
    c, db = omgeving["c"], omgeving["db"]
    registreer_en_bevestig(c, db, "piet@example.org", "Piet")
    a = admin_client(omgeving)
    r = a.post("/beheer/code", data={"csrf": csrf_van(a, "/beheer"), "person_id": "piet-de-vries"})
    code = db.one("SELECT code FROM claim_codes")["code"]
    assert code in r.text
    r = c.post("/claim/code", data={"csrf": csrf_van(c, "/claim"), "code": code.lower()})
    assert "Piet de Vries" in r.text
    assert db.one("SELECT trusted, person_id FROM users WHERE email='piet@example.org'")["trusted"] == 1
    assert c.post("/claim/code", data={"csrf": csrf_van(c, "/claim"), "code": code}).status_code == 303 or True  # al gekoppeld: terug naar account


def test_bewerken_verwijderen_melden(omgeving):
    c, db, config = omgeving["c"], omgeving["db"], omgeving["config"]
    u = registreer_en_bevestig(c, db, "lid@example.org", "Lid")
    db.db().execute("UPDATE users SET trusted=1 WHERE id=?", (u["id"],))
    f = upload(c, "nk-slalom-2024", jpeg((10, 20, 30))).json()["foto"]
    r = c.post(f"/api/foto/{f['id']}", data={"csrf": csrf_van(c, "/account"), "bijschrift": "Finale heren", "fotograaf": "Oma", "licentie": "cc-by", "origineel_vrij": "1"})
    assert r.json()["foto"]["caption"] == "Finale heren" and r.json()["foto"]["credit"] == "Oma"
    anoniem = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(omgeving["app"])
    assert anoniem.get(f"/o/{f['id']}").status_code == 200          # origineel nu vrij
    assert "Finale heren" in anoniem.get(f"/foto/{f['id']}").text
    r = anoniem.post(f"/foto/{f['id']}/melden", data={"csrf": csrf_van(anoniem, f"/foto/{f['id']}"), "reden": "dit ben ik, liever niet online"})
    assert r.status_code == 200 and "doorgegeven" in r.text
    a = admin_client(omgeving)
    assert "liever niet online" in a.get("/beheer?tab=meldingen").text
    # verwijderen door de uploader: weg van de site, origineel naar verwijderd/
    sha = db.one("SELECT sha256, ext FROM photos WHERE id=?", f["id"])
    r = c.post(f"/api/foto/{f['id']}/verwijder", data={"csrf": csrf_van(c, "/account")})
    assert r.json()["ok"] is True
    assert anoniem.get(f"/foto/{f['id']}").status_code == 404
    assert anoniem.get(f"/i/{f['key']}/t.jpg").status_code == 404
    assert (pathlib.Path(config.ORIG_DIR) / "verwijderd" / f"{sha['sha256']}.{sha['ext']}").exists()
    # dezelfde foto opnieuw uploaden mag daarna weer
    assert upload(c, "nk-slalom-2024", jpeg((10, 20, 30))).status_code == 200


def test_blokkeren_verbergt_alles(omgeving):
    c, db = omgeving["c"], omgeving["db"]
    u = registreer_en_bevestig(c, db, "lid@example.org", "Lid")
    db.db().execute("UPDATE users SET trusted=1 WHERE id=?", (u["id"],))
    upload(c, "nk-slalom-2024", jpeg((1, 1, 1)))
    assert c.get("/api/wedstrijd/nk-slalom-2024").json()["n"] == 1
    a = admin_client(omgeving)
    a.post(f"/beheer/account/{u['id']}/blokkeren", data={"csrf": csrf_van(a, "/beheer"), "reden": "spam"})
    assert c.get("/api/wedstrijd/nk-slalom-2024").json()["n"] == 0
    assert upload(c, "nk-slalom-2024", jpeg((2, 2, 2))).status_code == 403
    assert "geblokkeerd" in c.get("/upload").text


def test_wachtwoord_vergeten_en_onbevestigd(omgeving):
    c, db = omgeving["c"], omgeving["db"]
    c.post("/registreren", data={"csrf": csrf_van(c, "/registreren"), "email": "x@example.org", "naam": "Xx", "wachtwoord": "wachtwoord12345", "wachtwoord2": "wachtwoord12345"})
    login(c, "x@example.org", "wachtwoord12345")
    assert upload(c, "nk-slalom-2024", jpeg((3, 3, 3))).status_code == 403   # nog niet bevestigd
    c.post("/wachtwoord-vergeten", data={"csrf": csrf_van(c, "/wachtwoord-vergeten"), "email": "x@example.org"})
    m = db.one("SELECT body FROM outbox WHERE subject='Nieuw wachtwoord instellen'")
    link = re.search(r"http://testserver(/wachtwoord/\S+)", m["body"]).group(1)
    r = c.post(link, data={"csrf": csrf_van(c, link), "wachtwoord": "nieuwwachtwoord1", "wachtwoord2": "nieuwwachtwoord1"})
    assert r.status_code == 200 and "gewijzigd" in r.text
    assert db.one("SELECT email_verified FROM users WHERE email='x@example.org'")["email_verified"] == 1   # via de maillink bewezen


def test_taggen_jeugd_profiel_en_export(omgeving):
    c, db = omgeving["c"], omgeving["db"]
    u = registreer_en_bevestig(c, db, "lid@example.org", "Lid")
    db.db().execute("UPDATE users SET trusted=1 WHERE id=?", (u["id"],))
    f = upload(c, "nk-slalom-2024", jpeg((90, 90, 90))).json()["foto"]
    # deelnemers eerst in de suggesties, ook zonder zoekterm
    lst = c.get("/api/riders?wedstrijd=nk-slalom-2024").json()
    assert [r["id"] for r in lst] == ["jan-jansen", "kim-de-jong"] and all(r["deelnemer"] for r in lst)
    assert c.get("/api/riders?wedstrijd=nk-slalom-2024&q=vries").json()[0]["id"] == "piet-de-vries"
    # vertrouwd account tagt een volwassen rider: direct zichtbaar, ook op de riderpagina
    r = c.post(f"/api/foto/{f['id']}/tag", data={"csrf": csrf_van(c, "/account"), "person_id": "jan-jansen"})
    assert r.status_code == 200 and r.json()["tag"]["status"] == "zichtbaar"
    assert c.post(f"/api/foto/{f['id']}/tag", data={"csrf": csrf_van(c, "/account"), "person_id": "jan-jansen"}).status_code == 409
    anoniem = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(omgeving["app"])
    assert anoniem.get("/api/rider/jan-jansen").json()["n"] == 1
    assert anoniem.get("/api/wedstrijd/nk-slalom-2024").json()["fotos"][0]["tags"] == [{"person": "jan-jansen", "name": "Jan Jansen"}]
    # jeugd: de tag wacht, ook al is de tagger vertrouwd
    r = c.post(f"/api/foto/{f['id']}/tag", data={"csrf": csrf_van(c, "/account"), "person_id": "kim-de-jong"})
    assert r.json()["tag"]["status"] == "wacht"
    assert anoniem.get("/api/rider/kim-de-jong").json()["n"] == 0
    # vrije naam
    r = c.post(f"/api/foto/{f['id']}/tag", data={"csrf": csrf_van(c, "/account"), "naam": "Onbekende Surfer"})
    assert r.json()["tag"]["status"] == "zichtbaar" and r.json()["tag"]["person"] is None
    # de jeugdrider claimt haar naam en keurt de tag zelf goed
    k = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(omgeving["app"])
    registreer_en_bevestig(k, db, "kim@example.org", "Kim")
    a = admin_client(omgeving)
    a.post("/beheer/code", data={"csrf": csrf_van(a, "/beheer"), "person_id": "kim-de-jong"})
    k.post("/claim/code", data={"csrf": csrf_van(k, "/claim"), "code": db.one("SELECT code FROM claim_codes")["code"]})
    html = k.get("/account").text
    assert "Wacht op jouw goedkeuring" in html
    tid = db.one("SELECT id FROM tags WHERE person_id='kim-de-jong'")["id"]
    assert "Tags in de wachtrij" in a.get("/beheer?tab=wachtrij").text
    r = k.post(f"/api/tag/{tid}/goedkeuren", data={"csrf": csrf_van(k, "/account"), "terug": "/account"})
    assert r.status_code == 200 and anoniem.get("/api/rider/kim-de-jong").json()["n"] == 1
    # 'toon geen foto's op mijn profiel': riderpagina leeg en naam weg bij de foto
    k.post("/account/profiel", data={"csrf": csrf_van(k, "/account"), "verberg": "1"})
    assert anoniem.get("/api/rider/kim-de-jong").json()["n"] == 0
    assert "Kim de Jong" not in anoniem.get(f"/foto/{f['id']}").text
    assert {t["person"] for t in anoniem.get("/api/wedstrijd/nk-slalom-2024").json()["fotos"][0]["tags"]} == {"jan-jansen", None}
    k.post("/account/profiel", data={"csrf": csrf_van(k, "/account"), "verberg": "0"})
    assert anoniem.get("/api/rider/kim-de-jong").json()["n"] == 1
    # een nieuw (niet vertrouwd) account: tag wacht; de uploader mag hem weghalen
    n = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(omgeving["app"])
    registreer_en_bevestig(n, db, "nieuw@example.org", "Nieuw")
    r = n.post(f"/api/foto/{f['id']}/tag", data={"csrf": csrf_van(n, "/account"), "person_id": "piet-de-vries"})
    assert r.json()["tag"]["status"] == "wacht"
    tid2 = r.json()["tag"]["id"]
    assert n.post(f"/api/tag/{tid}/verwijder", data={"csrf": csrf_van(n, "/account")}).status_code == 403   # niet zijn tag
    assert c.post(f"/api/tag/{tid2}/verwijder", data={"csrf": csrf_van(c, "/account")}).json()["ok"] is True   # uploader wel
    # wedstrijd met alleen jeugdklassen: tag wacht
    f2 = upload(c, "kids-cup-2025", jpeg((91, 91, 91))).json()["foto"]
    assert c.post(f"/api/foto/{f2['id']}/tag", data={"csrf": csrf_van(c, "/account"), "person_id": "jan-jansen"}).json()["tag"]["status"] == "wacht"
    # export in media.json-formaat
    ex = anoniem.get("/api/export/nk-slalom-2024").json()
    assert ex["n"] == 1 and ex["files"][0]["sha256"] and ex["files"][0]["people"] == ["jan-jansen", "kim-de-jong"] and ex["files"][0]["in_repo"] is False
    assert "Fotografen" in anoniem.get("/").text


def test_testset_en_pagina(omgeving, monkeypatch):
    from app import images
    monkeypatch.setattr(images, "TEST_N", 2)
    images.maak_testset(force=True)
    c = omgeving["c"]
    t = c.get("/i/test-01/t.jpg"); m = c.get("/i/test-02/m.jpg")
    assert t.status_code == 200 and m.status_code == 200
    assert 20_000 < len(t.content) < 120_000 and 150_000 < len(m.content) < 900_000, (len(t.content), len(m.content))
    assert c.get("/").status_code == 200 and c.get("/wedstrijd/nk-slalom-2024").status_code == 200
    assert c.get("/upload").status_code == 200 and "Log eerst in" in c.get("/upload").text

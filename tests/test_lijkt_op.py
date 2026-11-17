"""Lijkt-op-controle (0.6.1): een opnieuw opgeslagen of verkleinde kopie van een foto die er al staat wordt niet zomaar opgeslagen;
de uploader moet eerst zeggen of het dezelfde foto is. Zie app/gelijkenis.py."""
import io
import json
import random

import pytest
from PIL import Image, ImageDraw, ImageEnhance

from test_app import omgeving, csrf_van, upload, registreer_en_bevestig, admin_client, jpeg  # noqa: F401  (omgeving is een fixture)


@pytest.fixture()
def omg(omgeving, monkeypatch):
    from app import naakt
    monkeypatch.setattr(naakt, "check", lambda p: ("groen", ""))   # snel en voorspelbaar; de naaktcheck heeft zijn eigen tests
    return omgeving


def beeld(seed, maat=(1200, 800)):
    """Een 'foto' met structuur (verloop en vormen); effen vlakken hebben geen bruikbare vingerafdruk."""
    rnd = random.Random(seed)
    im = Image.new("RGB", maat)
    d = ImageDraw.Draw(im)
    for y in range(maat[1]):
        d.line([(0, y), (maat[0], y)], fill=(40 + y * 120 // maat[1], 90, 200 - y * 120 // maat[1]))
    for _ in range(30):
        x, y = rnd.randrange(maat[0]), rnd.randrange(maat[1])
        w, h = rnd.randrange(40, 400), rnd.randrange(40, 300)
        kleur = tuple(rnd.randrange(256) for _ in range(3))
        (d.ellipse if rnd.random() < .5 else d.rectangle)([x, y, x + w, y + h], fill=kleur)
    return im


def als_jpeg(im, datum=None, kwaliteit=90, lange_zijde=None):
    if lange_zijde:
        im = im.copy(); im.thumbnail((lange_zijde, lange_zijde), Image.LANCZOS)
    exif = Image.Exif()
    if datum: exif[306] = datum
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=kwaliteit, exif=exif.tobytes())
    return buf.getvalue()


def lid(omg, email="lid@example.org", naam="Lid Een"):
    from fastapi.testclient import TestClient
    c = TestClient(omg["app"])
    registreer_en_bevestig(c, omg["db"], email, naam)
    return c


def zichtbare_foto(omg, a, seed=1, datum="2024:06:15 12:00:00"):
    r = upload(a, "nk-slalom-2024", als_jpeg(beeld(seed), datum=datum))
    assert r.status_code == 200, r.text
    f = r.json()["foto"]
    assert f["status"] == "zichtbaar"
    return f


def antwoord(c, token, a):
    return c.post("/api/upload/antwoord", data={"csrf": csrf_van(c, "/account"), "token": token, "antwoord": a})


def wachtende(config):
    return sorted(p.name for p in (config.DATA_DIR / "tmp").glob("lijkt-*"))


def test_vingerafdruk_kopie_dichtbij_andere_ver(omg):
    from app import gelijkenis
    a = beeld(1)
    pa = gelijkenis.vingerafdruk_beeld(a)
    kopie = Image.open(io.BytesIO(als_jpeg(a, kwaliteit=55, lange_zijde=700)))
    lichter = ImageEnhance.Brightness(a).enhance(1.15)
    assert gelijkenis.afstand(pa, gelijkenis.vingerafdruk_beeld(kopie)) <= 4
    assert gelijkenis.afstand(pa, gelijkenis.vingerafdruk_beeld(a.convert("L"))) <= 4
    assert gelijkenis.afstand(pa, gelijkenis.vingerafdruk_beeld(lichter)) <= 10
    assert gelijkenis.afstand(pa, gelijkenis.vingerafdruk_beeld(beeld(2))) > 16
    # effen vlakken: geen vingerafdruk, anders zou elke effen kleur op elkaar lijken
    assert gelijkenis.vingerafdruk_beeld(Image.new("RGB", (800, 600), (200, 30, 30))) == gelijkenis.GEEN


def test_kopie_vraagt_eerst_en_dezelfde_slaat_over(omg):
    c0, db, config = omg["c"], omg["db"], omg["config"]
    a = admin_client(omg)
    f = zichtbare_foto(omg, a)
    c = lid(omg)
    n = db.one("SELECT COUNT(*) n FROM photos")["n"]
    r = upload(c, "gpa-2025", als_jpeg(beeld(1), kwaliteit=60, lange_zijde=800), naam="van-facebook.jpg")   # zonder EXIF, zoals Facebook
    assert r.status_code == 409, r.text
    j = r.json()
    assert j["vraag"] is True and j["token"] and j["lijkt_op"][0]["id"] == f["id"] and j["lijkt_op"][0]["afstand"] <= 4
    assert j["lijkt_op"][0]["wedstrijd_naam"] == "NK Slalom 2024" and j["lijkt_op"][0]["eigen"] is False
    # nog niets opgeslagen: geen regel, alleen de wachtende upload
    assert db.one("SELECT COUNT(*) n FROM photos")["n"] == n
    assert len(wachtende(config)) == 3
    # het voorbeeld van de eigen foto: alleen voor de uploader
    assert c.get(j["voorbeeld"]).status_code == 200 and c.get(j["voorbeeld"]).headers["content-type"] == "image/jpeg"
    assert a.get(j["voorbeeld"]).status_code == 404
    # hetzelfde bestand nog eens erin slepen: wacht al
    r2 = upload(c, "gpa-2025", als_jpeg(beeld(1), kwaliteit=60, lange_zijde=800), naam="van-facebook.jpg")
    assert r2.status_code == 409 and "wacht" in r2.json()["fout"] and "vraag" not in r2.json()
    # een ander kan niet antwoorden; onzin-antwoord geeft 400
    assert antwoord(a, j["token"], "anders").status_code == 410
    assert antwoord(c, j["token"], "misschien").status_code == 400
    # dezelfde foto: weg, niets opgeslagen, wel een logregel
    r = antwoord(c, j["token"], "zelfde")
    assert r.status_code == 200 and r.json()["overgeslagen"] is True
    assert db.one("SELECT COUNT(*) n FROM photos")["n"] == n and wachtende(config) == []
    assert db.one("SELECT * FROM log WHERE action='upload.lijkt_op_overgeslagen'")
    assert antwoord(c, j["token"], "zelfde").status_code == 410


def test_andere_foto_gaat_naar_wachtrij_met_vlag(omg):
    db = omg["db"]
    a = admin_client(omg)
    f = zichtbare_foto(omg, a)
    # ook een vertrouwd account (de beheerder zelf) krijgt de vraag, en "een andere foto" gaat dan langs de wachtrij
    r = upload(a, "nk-slalom-2024", als_jpeg(ImageEnhance.Brightness(beeld(1)).enhance(1.1), kwaliteit=70))
    assert r.status_code == 409 and r.json()["lijkt_op"][0]["eigen"] is True
    tok = r.json()["token"]
    r = antwoord(a, tok, "anders")
    assert r.status_code == 200, r.text
    g = r.json()["foto"]
    assert g["status"] == "wacht" and "beheerder" in r.json()["waarschuwing"]
    lo = json.loads(db.one("SELECT lijkt_op FROM photos WHERE id=?", g["id"])["lijkt_op"])
    assert lo["gevraagd"] is True and lo["treffers"][0]["id"] == f["id"]
    assert db.one("SELECT phash FROM photos WHERE id=?", g["id"])["phash"]
    assert antwoord(a, tok, "anders").status_code == 410            # twee keer klikken: maar één keer opgeslagen
    assert db.one("SELECT COUNT(*) n FROM photos WHERE sha256=(SELECT sha256 FROM photos WHERE id=?)", g["id"])["n"] == 1
    # in het beheer: een eigen blok naast de gelijkende foto, en niet mee met "alles goedkeuren"
    html = a.get("/beheer?tab=wachtrij").text
    assert "Lijkt op een foto die er al staat" in html and f"/foto/{f['id']}" in html and "uploader zegt: een andere foto" in html
    uid = db.one("SELECT id FROM users WHERE email='admin@example.org'")["id"]
    r = a.post(f"/beheer/account/{uid}/goedkeuren-alles", data={"csrf": csrf_van(a, "/beheer")}, follow_redirects=False)
    assert db.one("SELECT status FROM photos WHERE id=?", g["id"])["status"] == "wacht"
    a.post(f"/beheer/foto/{g['id']}/goedkeuren", data={"csrf": csrf_van(a, "/beheer"), "terug": "/beheer?tab=wachtrij"})
    assert db.one("SELECT status FROM photos WHERE id=?", g["id"])["status"] == "zichtbaar"
    assert "Lijkt op een foto die er al staat" not in a.get("/beheer?tab=wachtrij").text


def test_niet_zichtbare_treffer_geen_vraag_wel_vlag(omg):
    db = omg["db"]
    c1 = lid(omg, "een@example.org", "Lid Een")
    r = upload(c1, "nk-slalom-2024", als_jpeg(beeld(3), datum="2024:06:15 12:00:00"))
    assert r.status_code == 200 and r.json()["foto"]["status"] == "wacht"     # nog in de wachtrij: anderen kunnen hem niet zien
    eerste = r.json()["foto"]["id"]
    c2 = lid(omg, "twee@example.org", "Lid Twee")
    r = upload(c2, "nk-slalom-2024", als_jpeg(beeld(3), kwaliteit=60, lange_zijde=900))
    assert r.status_code == 200, r.text                                         # geen vraag over een foto die hij niet kan zien
    lo = json.loads(db.one("SELECT lijkt_op FROM photos WHERE id=?", r.json()["foto"]["id"])["lijkt_op"])
    assert lo["gevraagd"] is False and lo["treffers"][0]["id"] == eerste
    # de eerste uploader zelf ziet zijn eigen wachtende foto wel, en krijgt dus de vraag
    r = upload(c1, "nk-slalom-2024", als_jpeg(beeld(3), kwaliteit=50, lange_zijde=600))
    assert r.status_code == 409 and r.json()["lijkt_op"][0]["eigen"] is True


def test_andere_foto_en_serie_geen_vraag(omg):
    a = admin_client(omg)
    zichtbare_foto(omg, a, seed=1, datum="2024:06:15 12:00:00")
    assert upload(a, "nk-slalom-2024", als_jpeg(beeld(2), datum="2024:06:15 12:00:05")).status_code == 200
    # lijkt sterk, maar allebei een opnametijd en die verschilt: een andere foto uit de serie, geen kopie
    r = upload(a, "nk-slalom-2024", als_jpeg(ImageEnhance.Brightness(beeld(1)).enhance(1.05), datum="2024:06:15 12:00:01"))
    assert r.status_code == 200, r.text
    # dezelfde opnametijd (kopie van het origineel met EXIF): wel de vraag
    r = upload(a, "nk-slalom-2024", als_jpeg(beeld(1), datum="2024:06:15 12:00:00", kwaliteit=60, lange_zijde=1000))
    assert r.status_code == 409 and r.json()["vraag"]


def test_effen_fotos_en_drempel_uit(omg, monkeypatch):
    a = admin_client(omg)
    assert upload(a, "nk-slalom-2024", jpeg((200, 30, 30))).status_code == 200
    assert upload(a, "nk-slalom-2024", jpeg((30, 200, 30))).status_code == 200     # twee effen kleuren lijken niet op elkaar
    zichtbare_foto(omg, a, seed=5)
    monkeypatch.setattr(omg["config"], "LIJKT_OP_DREMPEL", -1)
    assert upload(a, "nk-slalom-2024", als_jpeg(beeld(5), kwaliteit=60, lange_zijde=800)).status_code == 200


def test_bestaande_fotos_krijgen_vingerafdruk_en_opruimen(omg):
    import os
    import time
    from app import gelijkenis
    db, config = omg["db"], omg["config"]
    a = admin_client(omg)
    f = zichtbare_foto(omg, a, seed=7)
    db.db().execute("UPDATE photos SET phash=NULL WHERE id=?", (f["id"],))       # zoals een foto van voor 0.6.1
    assert gelijkenis.vul_bestaande() == 1
    assert db.one("SELECT phash FROM photos WHERE id=?", f["id"])["phash"]
    assert gelijkenis.vul_bestaande() == 0
    # een vergeten vraag ruimt zichzelf op na BEWAAR_UREN
    r = upload(lid(omg), "gpa-2025", als_jpeg(beeld(7), kwaliteit=60, lange_zijde=800))
    assert r.status_code == 409 and len(wachtende(config)) == 3
    oud = time.time() - (gelijkenis.BEWAAR_UREN + 1) * 3600
    for p in (config.DATA_DIR / "tmp").glob("lijkt-*"): os.utime(p, (oud, oud))
    assert gelijkenis.opruimen() == 1 and wachtende(config) == []

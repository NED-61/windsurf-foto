"""Beveiliging (0.6.0, 17 november 2026): koppen (CSP met nonce, HSTS), groottegrens vóór het inloggen, limieten die niet met een
verzonnen X-Forwarded-For te omzeilen zijn, limiet per account, geen doorsturen naar een ander domein, sessies die vervallen bij een
nieuw wachtwoord, mails die niet te misbruiken zijn, en alleen de lezers van de toegestane soorten foto's."""
import io
import re

from PIL import Image

from test_app import omgeving, csrf_van, jpeg, upload, registreer_en_bevestig, login, admin_client  # noqa: F401  (omgeving is een fixture)


def test_koppen_csp_met_nonce_en_hsts(omgeving):
    c, config = omgeving["c"], omgeving["config"]
    r = c.get("/")
    k = r.headers["content-security-policy"]
    assert "script-src 'self' 'nonce-" in k and "frame-ancestors 'none'" in k and "object-src 'none'" in k
    assert "strict-transport-security" not in r.headers   # APP_URL is http in de test
    # een pagina met een script in de template: de nonce in de pagina is die uit de kop
    a = admin_client(omgeving)
    f = upload(a, "nk-slalom-2024", jpeg((5, 50, 150))).json()["foto"]
    r = a.get(f"/foto/{f['id']}")
    nonce = re.search(r"'nonce-([^']+)'", r.headers["content-security-policy"]).group(1)
    assert f'<script nonce="{nonce}">' in r.text
    assert " onsubmit=" not in a.get("/beheer?tab=accounts").text and " onsubmit=" not in a.get("/upload").text
    assert "content-security-policy" not in c.get(f"/i/{f['key']}/t.jpg").headers   # afbeeldingen: geen paginakop nodig
    config.APP_URL = "https://foto.example"
    assert c.get("/").headers["strict-transport-security"].startswith("max-age=")


def test_groottegrens_voor_het_inloggen(omgeving, monkeypatch):
    c, config = omgeving["c"], omgeving["config"]
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 1)
    # zonder account: geweigerd op de lengte, voordat er iets gelezen of als bestand bewaard wordt
    r = c.post("/api/upload", content=b"x" * (3 * 1024 * 1024), headers={"content-type": "multipart/form-data; boundary=zz"})
    assert r.status_code == 413
    assert c.post("/inloggen", content=b"a=" + b"x" * (2 * 1024 * 1024), headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 413

    # zonder lengte (in stukken): ook gestopt
    def stukken():
        for _ in range(3): yield b"x" * (1024 * 1024)
    assert c.post("/inloggen", content=stukken(), headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 413
    assert c.post("/api/upload", content=stukken(), headers={"content-type": "multipart/form-data; boundary=zz"}).status_code == 413
    assert c.post("/api/tel", content=stukken()).status_code in (204, 413)   # de teller stopt zelf al na 2 kB; nooit een fout
    assert not list((config.DATA_DIR / "tmp").iterdir())
    # een gewone upload past nog
    registreer_en_bevestig(c, omgeving["db"], "lid@example.org", "Lid")
    assert upload(c, "nk-slalom-2024", jpeg((1, 1, 1))).status_code == 200


def test_limiet_niet_te_omzeilen_met_verzonnen_kop(omgeving):
    c = omgeving["c"]
    tok = csrf_van(c)
    codes = [c.post("/inloggen", data={"csrf": tok, "email": "niemand@example.org", "wachtwoord": "fout"},
                    headers={"x-forwarded-for": f"10.0.0.{i}, 203.0.113.9"}).status_code for i in range(12)]
    assert codes[:10] == [400] * 10 and codes[10] == 429   # de verzonnen eerste adressen tellen niet, het laatste (van de proxy) wel
    # een ander echt adres mag gewoon
    assert c.post("/inloggen", data={"csrf": tok, "email": "niemand@example.org", "wachtwoord": "fout"},
                  headers={"x-forwarded-for": "203.0.113.10"}).status_code == 400


def test_mislukt_inloggen_per_account(omgeving):
    c = omgeving["c"]
    from app import routes_account
    tok = csrf_van(c)
    for i in range(routes_account.MISLUKT_PER_ACCOUNT):
        assert c.post("/inloggen", data={"csrf": tok, "email": "admin@example.org", "wachtwoord": f"fout{i}"},
                      headers={"x-forwarded-for": f"198.51.100.{i}"}).status_code == 400
    r = c.post("/inloggen", data={"csrf": tok, "email": "admin@example.org", "wachtwoord": "beheerwachtwoord1"}, headers={"x-forwarded-for": "198.51.100.200"})
    assert r.status_code == 429   # ook met het goede wachtwoord, vanaf een nieuw adres: dit account is een uur dicht


def test_terug_naar_ander_domein_kan_niet(omgeving):
    c = omgeving["c"]
    for i, slecht in enumerate(("//evil.example/", "/\\evil.example/", "/\\/evil.example", "https://evil.example/", "/\tx")):
        r = c.post("/inloggen", data={"csrf": csrf_van(c), "email": "admin@example.org", "wachtwoord": "beheerwachtwoord1", "terug": slecht},
                   headers={"x-forwarded-for": f"203.0.113.{i}"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/account", slecht
        c.cookies.clear()
    r = c.post("/inloggen", data={"csrf": csrf_van(c), "email": "admin@example.org", "wachtwoord": "beheerwachtwoord1", "terug": "/upload"}, follow_redirects=False)
    assert r.headers["location"] == "/upload"


def test_nieuw_wachtwoord_logt_andere_sessies_uit(omgeving):
    c, db = omgeving["c"], omgeving["db"]
    from fastapi.testclient import TestClient
    registreer_en_bevestig(c, db, "lid@example.org", "Lid", pw="wachtwoord12345")
    ander = TestClient(omgeving["app"])
    login(ander, "lid@example.org", "wachtwoord12345")
    assert ander.get("/account", follow_redirects=False).status_code == 200
    r = c.post("/account/wachtwoord", data={"csrf": csrf_van(c, "/account"), "huidig": "wachtwoord12345", "wachtwoord": "nieuwwachtwoord9", "wachtwoord2": "nieuwwachtwoord9"})
    assert r.status_code == 200 and "uitgelogd" in r.text
    assert c.get("/account", follow_redirects=False).status_code == 200          # deze sessie blijft
    assert ander.get("/account", follow_redirects=False).status_code == 303      # de andere niet (naar inloggen)
    login(ander, "lid@example.org", "nieuwwachtwoord9")
    assert ander.get("/account", follow_redirects=False).status_code == 200


def test_mails_niet_te_misbruiken(omgeving):
    c, db = omgeving["c"], omgeving["db"]
    # de ingevulde naam komt niet in de mail naar een nog onbevestigd adres
    c.post("/registreren", data={"csrf": csrf_van(c, "/registreren"), "email": "slachtoffer@example.org", "naam": "Win een prijs op evil.example",
                                 "wachtwoord": "wachtwoord12345", "wachtwoord2": "wachtwoord12345"})
    m = db.one("SELECT body FROM outbox WHERE to_addr='slachtoffer@example.org'")
    assert m and "evil.example" not in m["body"]
    # wachtwoord vergeten: hooguit 3 mails per adres per uur, ook vanaf verschillende adressen, en steeds dezelfde melding
    for i in range(6):
        r = c.post("/wachtwoord-vergeten", data={"csrf": csrf_van(c, "/wachtwoord-vergeten"), "email": "slachtoffer@example.org"},
                   headers={"x-forwarded-for": f"192.0.2.{i}"})
        assert r.status_code == 200 and "Mail onderweg" in r.text
    resets = db.all_("SELECT body FROM outbox WHERE to_addr='slachtoffer@example.org' AND subject='Nieuw wachtwoord instellen'")
    assert len(resets) == 3 and not any("evil.example" in x["body"] for x in resets)


def test_registraties_per_uur(omgeving, monkeypatch):
    c, db = omgeving["c"], omgeving["db"]
    from app import routes_account
    monkeypatch.setattr(routes_account, "REGISTRATIES_PER_UUR", 2)

    def reg(i):
        return c.post("/registreren", data={"csrf": csrf_van(c, "/registreren"), "email": f"n{i}@example.org", "naam": f"Nieuw {i}",
                                            "wachtwoord": "wachtwoord12345", "wachtwoord2": "wachtwoord12345"}, headers={"x-forwarded-for": f"192.0.2.{i}"})
    assert reg(1).status_code == 200 and reg(2).status_code == 200 and reg(3).status_code == 429
    assert db.one("SELECT COUNT(*) n FROM users WHERE email LIKE 'n%@example.org'")["n"] == 2


def test_alleen_toegestane_soorten_en_niet_te_groot(omgeving, monkeypatch):
    c, db = omgeving["c"], omgeving["db"]
    registreer_en_bevestig(c, db, "lid@example.org", "Lid")
    for fmt in ("TIFF", "BMP", "GIF"):
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (1, 2, 3)).save(buf, fmt)
        assert upload(c, "nk-slalom-2024", buf.getvalue(), naam=f"x.{fmt.lower()}").status_code == 415, fmt
    from app import images
    assert images.FORMATEN == ["JPEG", "PNG", "WEBP", "HEIF"]
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100 * 100)
    assert upload(c, "nk-slalom-2024", jpeg((9, 8, 7), maat=(150, 100))).status_code == 415   # 1,5x de grens: al geweigerd (niet pas bij 2x)
    assert not list((omgeving["config"].DATA_DIR / "tmp").iterdir())


def test_heic_gaat_nog(omgeving):
    """HEIC (iPhone) moet blijven werken met de beperkte lijst van lezers."""
    import pillow_heif
    c, db = omgeving["c"], omgeving["db"]
    registreer_en_bevestig(c, db, "lid@example.org", "Lid")
    buf = io.BytesIO()
    pillow_heif.from_pillow(Image.new("RGB", (640, 480), (20, 90, 160))).save(buf, quality=80)
    r = upload(c, "nk-slalom-2024", buf.getvalue(), naam="IMG_0001.HEIC")
    assert r.status_code == 200, r.text

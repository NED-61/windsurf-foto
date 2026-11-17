"""Lijkt-op-controle bij het uploaden: een vingerafdruk van het beeld (pHash), zodat dezelfde foto ook herkend wordt als hij opnieuw is
opgeslagen, verkleind, in zwart-wit staat of iets lichter is gemaakt. De sha256 is dan anders, het beeld niet. Bij oude wedstrijden
komen de foto's bij iedereen uit dezelfde bronnen (Facebook, WhatsApp, clubsites), dus dat gebeurt vaak.

Gemeten op 111 echte wedstrijdfoto's (17 november 2026), verschil in bits van de 64:
- kopie via Facebook, WhatsApp, een kleine webversie, een thumbnail, png of zwart-wit: hooguit 4
- 15 % lichter en 10 % meer contrast: hooguit 10
- een randje eraf (3 % per kant): meestal onder de 12; een flinke uitsnede vindt deze methode niet
- verschillende foto's: minstens 12 (twee uitslagenlijsten), seriefoto's van dezelfde rider 16 tot 18, meestal rond de 32
Vandaar de grens van 12 (LIJKT_OP_DREMPEL).

Lijkt een upload op een foto die er al staat en die de uploader kan zien, dan wordt hij nog niet opgeslagen: de uploader moet eerst
zeggen of het dezelfde foto is (keuze van Max, 17 november 2026). Tot dat antwoord staat het bestand als 'wachtend' in
DATA_DIR/tmp/lijkt-<token>.*; na BEWAAR_UREN ruimt opruimen() het op.
"""
import json
import re
import secrets
import shutil
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from . import config, db, images, auth

TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,40}")
BEWAAR_UREN = 24
VLAK = 2.0          # een effen vlak (alle lage frequenties ~0) geeft geen bruikbare vingerafdruk: elke effen kleur zou op elkaar lijken
GEEN = ""           # in photos.phash: wel bekeken, maar geen bruikbare vingerafdruk (effen vlak); NULL = nog niet bekeken


def _dct_matrix(n):
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    m = np.cos(np.pi * (2 * i + 1) * k / (2 * n)) * np.sqrt(2 / n)
    m[0] /= np.sqrt(2)
    return m


_D32 = _dct_matrix(32)


def vingerafdruk_beeld(im) -> str:
    """64-bit pHash als 16 hextekens (hetzelfde recept als de imagehash-bibliotheek), of GEEN voor een effen vlak."""
    g = np.asarray(im.convert("L").resize((32, 32), Image.LANCZOS), dtype=np.float64)
    laag = (_D32 @ g @ _D32.T)[:8, :8]
    if np.abs(laag.flatten()[1:]).max() < VLAK: return GEEN
    bits = (laag > np.median(laag)).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def vingerafdruk(path: Path) -> str | None:
    """Vingerafdruk van een bestand (gedraaid volgens de EXIF), GEEN voor een effen vlak, None als het bestand niet te lezen is."""
    try:
        with images.open_upload(path) as im:   # alleen de lezers van de toegestane soorten (beveiliging 0.6.0)
            # bewust geen im.draft(): sneller, maar dan viel 1 van de 111 lichter gemaakte kopieën buiten de grens (14 in plaats van hooguit 10)
            return vingerafdruk_beeld(ImageOps.exif_transpose(im))
    except Exception:  # noqa: BLE001
        return None


def afstand(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def zoek(ph: str | None, taken_at: str | None, drempel: int | None = None) -> list:
    """Foto's (niet verwijderd) die op deze vingerafdruk lijken, de meest gelijkende eerst, als [(afstand, rij)].
    Hebben beide foto's een opnametijd en verschilt die, dan is het een andere foto (bijvoorbeeld uit dezelfde serie) en geen kopie:
    een kopie houdt de opnametijd van het origineel, of verliest hem (Facebook en WhatsApp halen de EXIF eraf)."""
    grens = config.LIJKT_OP_DREMPEL if drempel is None else drempel
    if not ph or grens < 0: return []
    x = int(ph, 16)
    kandidaten = []
    for r in db.all_("SELECT id, phash, taken_at FROM photos WHERE phash IS NOT NULL AND phash!='' AND status!='verwijderd'"):
        d = (x ^ int(r["phash"], 16)).bit_count()
        if d > grens: continue
        if taken_at and r["taken_at"] and taken_at != r["taken_at"]: continue
        kandidaten.append((d, r["id"]))
    kandidaten.sort()
    return [(d, db.one("SELECT * FROM photos WHERE id=?", fid)) for d, fid in kandidaten]


def kan_zien(u, r) -> bool:
    """Mag deze uploader de gelijkende foto zien (dezelfde regel als /i/<key>/t.jpg)? Anders vragen we hem niets en kijkt de beheerder."""
    return r["status"] == "zichtbaar" or r["uploader_id"] == u["id"] or auth.is_admin(u)


def lijkt_op_json(treffers, gevraagd: bool) -> str:
    """Wat er in photos.lijkt_op komt: op welke foto's hij leek, en of de uploader gezegd heeft dat het een andere foto is."""
    return json.dumps({"gevraagd": gevraagd, "treffers": [{"id": r["id"], "afstand": d} for d, r in treffers[:10]]})


# ---------------------------------------------------------------- uploads die op een antwoord wachten
def _map() -> Path:
    return config.DATA_DIR / "tmp"


def _paden(token: str):
    d = _map()
    return d / f"lijkt-{token}.bestand", d / f"lijkt-{token}.json", d / f"lijkt-{token}-t.jpg"


def maak_voorbeeld(src: Path, doel: Path):
    """Kleine versie van de wachtende upload (zonder EXIF), zodat de uploader zijn eigen foto naast de gelijkende ziet; ook voor HEIC,
    dat de meeste browsers zelf niet kunnen tonen."""
    with images.open_upload(src) as im:
        im.draft("RGB", (800, 800))
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"): im = im.convert("RGB")
        im.thumbnail((config.THUMB_PX, config.THUMB_PX), Image.LANCZOS)
        im.save(doel, "JPEG", quality=80)


def bewaar(tmp: Path, meta: dict) -> str:
    """Zet een upload apart tot de uploader antwoordt. Geeft het token."""
    token = secrets.token_urlsafe(18)
    bestand, js, voorbeeld = _paden(token)
    shutil.move(str(tmp), str(bestand))
    try:
        maak_voorbeeld(bestand, voorbeeld)
    except Exception:  # noqa: BLE001  (dan zonder eigen voorbeeld; de vraag blijft)
        pass
    js.write_text(json.dumps(dict(meta, aangemaakt=time.time())), encoding="utf-8")
    return token


def haal(token: str, user_id: int) -> dict | None:
    """De gegevens van een wachtende upload van deze gebruiker, of None (onbekend, van een ander, of verlopen)."""
    if not TOKEN_RE.fullmatch(token or ""): return None
    bestand, js, _ = _paden(token)
    try:
        meta = json.loads(js.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if meta.get("user_id") != user_id or not bestand.exists(): return None
    if time.time() - meta.get("aangemaakt", 0) > BEWAAR_UREN * 3600:
        weg(token); return None
    return meta


def voorbeeld_pad(token: str, user_id: int) -> Path | None:
    if not haal(token, user_id): return None
    p = _paden(token)[2]
    return p if p.exists() else None


def neem(token: str) -> Path | None:
    """Haalt het bestand van een wachtende upload weg uit de wachtstand, voor de verdere verwerking. Atomair: bij twee keer klikken
    krijgt alleen de eerste het bestand, de tweede None."""
    bestand, js, voorbeeld = _paden(token)
    doel = _map() / secrets.token_hex(8)
    try:
        bestand.rename(doel)
    except OSError:
        return None
    js.unlink(missing_ok=True); voorbeeld.unlink(missing_ok=True)
    return doel


def weg(token: str):
    for p in _paden(token): p.unlink(missing_ok=True)


def wacht_al(sha: str) -> bool:
    """Wacht precies dit bestand al op een antwoord (twee keer in dezelfde reeks gesleept)?"""
    for js in _map().glob("lijkt-*.json"):
        try:
            if json.loads(js.read_text(encoding="utf-8")).get("sha") == sha: return True
        except (OSError, ValueError):
            continue
    return False


def opruimen() -> int:
    """Wachtende uploads waar na BEWAAR_UREN nog geen antwoord op is: weg (ze stonden nog nergens). Geeft het aantal."""
    grens, n = time.time() - BEWAAR_UREN * 3600, 0
    for p in _map().glob("lijkt-*"):
        try:
            if p.stat().st_mtime < grens: p.unlink(); n += p.suffix == ".json"
        except OSError:
            continue
    return n


# ---------------------------------------------------------------- bestaande foto's
def vul_bestaande() -> int:
    """Eenmalig bij het opstarten: vingerafdruk voor foto's van voor deze controle, uit de kijkversie (m.jpg) of anders het origineel.
    Geeft het aantal."""
    n = 0
    for r in db.all_("SELECT * FROM photos WHERE phash IS NULL AND status!='verwijderd'"):
        ph = None
        m = images.derived_dir(r["key"]) / "m.jpg"
        if m.exists(): ph = vingerafdruk(m)
        if ph is None:
            o = images.orig_path(r)
            if o.exists(): ph = vingerafdruk(o)
        if ph is None: continue   # niet te lezen: bij de volgende start nog eens
        db.db().execute("UPDATE photos SET phash=? WHERE id=?", (ph, r["id"]))
        n += 1
    if n: db.log("lijkt_op.bestaande_gevuld", detail=f"{n} foto's")
    return n

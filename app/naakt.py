"""Naaktcheck bij het uploaden: NudeNet (MIT, model van 12 MB), draait in deze container op de NAS; er gaat geen foto naar buiten.

NudeNet zoekt per lichaamsdeel en zegt of het bedekt of bloot is. Uitkomst per foto:
- groen: niets bloots gevonden. De foto volgt de gewone weg (nieuw account: wachtrij; vertrouwd: direct zichtbaar).
- oranje: mogelijk bloot (blote borsten of billen, of een zwakke treffer op geslachtsdelen).
- rood: duidelijk bloot (geslachtsdelen).
  Oranje en rood worden bij het uploaden geweigerd, voor iedereen, en niet bewaard: geen origineel, geen kijkversie, alleen een logregel
  (keuze van Max, 3 november 2026: topless hoort niet op de site; "weiger naaktfotos in de upload volledig"). Bij rood krijgt de beheerder
  een mail, en een account dat niet vertrouwd is kan pas weer uploaden als de beheerder dat op het tabblad Accounts toestaat.
- fout: de check kon niet draaien. Dan gaat de foto voor de zekerheid naar de wachtrij (weigeren zou al het uploaden stilleggen).
- uit: NAAKTCHECK=0 (noodknop); dan telt de foto als groen.
Bedekt (bikini, zwembroek, wetsuit) en een blote mannenborst tellen niet. Het model kijkt op 320 pixels: een klein figuurtje ver weg op
het water ziet het slecht, een foto waarop iemand groot in beeld is goed. Er wordt nooit automatisch iets verwijderd.
"""
import threading
from pathlib import Path

from . import config

ROOD = {"FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED", "ANUS_EXPOSED"}
ORANJE = {"FEMALE_BREAST_EXPOSED", "BUTTOCKS_EXPOSED"}
ROOD_MIN = 0.5      # vanaf deze score is een treffer op ROOD duidelijk
ORANJE_MIN = 0.3    # vanaf deze score telt een treffer op ORANJE (en een zwakke op ROOD) als twijfel
NIVEAUS = ("groen", "oranje", "rood", "fout", "uit")
WEIGEREN = {"oranje", "rood"}   # bij het uploaden geweigerd en niet bewaard (keuze van Max, 3 november 2026: "weiger naaktfotos in de upload volledig")
WEIGER_TEKST = ("Deze foto is niet geplaatst: hij lijkt bloot te zijn, en dat hoort niet op deze site. De foto is niet bewaard. "
                "Is dit een vergissing (bijvoorbeeld een wetsuit of een bikini), laat het dan weten aan de beheerder van het archief.")
LABEL_NL = {"FEMALE_GENITALIA_EXPOSED": "geslachtsdeel (v)", "MALE_GENITALIA_EXPOSED": "geslachtsdeel (m)", "ANUS_EXPOSED": "anus",
            "FEMALE_BREAST_EXPOSED": "blote borst", "BUTTOCKS_EXPOSED": "blote billen"}

_detector = None
_fout = None
_lock = threading.Lock()


def _laad():
    """Het model één keer laden (0,1 s); bij een fout onthouden waarom, zodat /api/health het kan tonen."""
    global _detector, _fout
    with _lock:
        if _detector is None and _fout is None:
            try:
                from nudenet import NudeDetector
                _detector = NudeDetector()
            except Exception as e:  # noqa: BLE001
                _fout = f"{type(e).__name__}: {e}"[:200]
    return _detector


def status() -> str:
    """'aan', 'uit' of 'fout: ...' (voor /api/health en de beheerpagina)."""
    if not config.NAAKTCHECK: return "uit"
    return "aan" if _laad() is not None else f"fout: {_fout}"


def _detecteer(pad: Path) -> list:
    """Treffers van NudeNet: [{'class': ..., 'score': ...}, ...]. Via Pillow naar RGB, zodat ook zwart-wit en png werken."""
    import numpy as np
    from PIL import Image
    d = _laad()
    if d is None: raise RuntimeError(_fout or "NudeNet niet beschikbaar")
    with Image.open(pad) as im:
        bgr = np.asarray(im.convert("RGB"))[:, :, ::-1].copy()      # dezelfde vorm als cv2.imread geeft
    return d.detect(bgr)


def beoordeel(treffers: list) -> tuple[str, str]:
    """(niveau, detail) uit de treffers. Detail = de treffers die meetellen, met score, voor de beheerder."""
    tel = [(t["class"], float(t["score"])) for t in treffers
           if (t["class"] in ROOD and t["score"] >= ORANJE_MIN) or (t["class"] in ORANJE and t["score"] >= ORANJE_MIN)]
    if not tel: return "groen", ""
    niveau = "rood" if any(c in ROOD and s >= ROOD_MIN for c, s in tel) else "oranje"
    detail = "; ".join(f"{LABEL_NL.get(c, c)} {s:.2f}" for c, s in sorted(tel, key=lambda x: -x[1]))
    return niveau, detail


def check(pad: Path) -> tuple[str, str]:
    """Beoordeel één afbeelding (de kijkversie m.jpg). Geeft (niveau, detail); gooit nooit een fout."""
    if not config.NAAKTCHECK: return "uit", ""
    try:
        return beoordeel(_detecteer(pad))
    except Exception as e:  # noqa: BLE001
        return "fout", f"{type(e).__name__}: {e}"[:200]


def zichtbaar_mag(niveau) -> bool:
    """Mag een foto met deze uitkomst direct zichtbaar worden (als de uploader dat verder ook mag)?"""
    return niveau in ("groen", "uit")


def controleer_bestaande() -> int:
    """Eenmalig bij het opstarten: foto's van voor de naaktcheck alsnog beoordelen. Een zichtbare foto die niet groen is gaat terug
    naar de wachtrij (niet weg: hij stond er al, de beheerder beslist). Geeft het aantal beoordeelde foto's."""
    from . import db, images
    n = 0
    for r in db.all_("SELECT id, key, status, event_slug, uploader_id FROM photos WHERE naakt IS NULL AND status!='verwijderd'"):
        niveau, detail = check(images.derived_dir(r["key"]) / "m.jpg")
        if niveau == "uit": return n
        db.db().execute("UPDATE photos SET naakt=?, naakt_detail=? WHERE id=?", (niveau, detail or None, r["id"]))
        if not zichtbaar_mag(niveau):
            db.log(f"naakt.{niveau}", photo_id=r["id"], target_user=r["uploader_id"], event_slug=r["event_slug"], detail=f"bestaande foto; {detail}")
            if r["status"] == "zichtbaar":
                db.db().execute("UPDATE photos SET status='wacht', status_reason=?, status_at=? WHERE id=?",
                                (f"naaktcheck: {niveau}", db.now(), r["id"]))
        n += 1
    if n: db.log("naakt.bestaande_gecontroleerd", detail=f"{n} foto's")
    return n

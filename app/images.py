"""Beeldbewerking: bestandstype herkennen, hash, EXIF-opnamedatum, verkleinde versies (zonder EXIF, dus zonder GPS), en de testset voor F0.

Het origineel wordt nooit aangeraakt: byte-identiek bewaard onder ORIG_DIR/<sha[:2]>/<sha>.<ext>.
"""
import hashlib
import random
import threading
import datetime
from pathlib import Path

from PIL import Image, ImageOps, ImageDraw

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:  # noqa: BLE001
    pass

from . import config

Image.MAX_IMAGE_PIXELS = 200_000_000


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blok in iter(lambda: f.read(1 << 20), b""): h.update(blok)
    return h.hexdigest()


def herken(path: Path):
    """Geeft (formaat, extensie) of None als het geen toegestane afbeelding is."""
    try:
        with Image.open(path) as im:
            fmt = im.format
            im.verify()
    except Exception:  # noqa: BLE001
        return None
    ext = config.TOEGESTANE_TYPES.get(fmt or "")
    return (fmt, ext) if ext else None


def opnamedatum(im) -> str | None:
    """DateTimeOriginal uit de EXIF als ISO-8601 (lokale tijd van de camera, zonder zone), anders None."""
    try:
        exif = im.getexif()
        s = exif.get_ifd(0x8769).get(36867) or exif.get(306)   # DateTimeOriginal, anders DateTime
        if not s: return None
        d = datetime.datetime.strptime(str(s)[:19], "%Y:%m:%d %H:%M:%S")
        return d.isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        return None


def derived_dir(key: str) -> Path:
    return config.DERIVED_DIR / key[:2] / key


def orig_rel(slug: str, jaar, taken_at, sha: str, ext: str) -> str:
    """Pad van een origineel binnen ORIG_DIR, zodat de foto's ook op de NAS zelf terug te vinden zijn:
    <jaar>/<wedstrijd>/<wedstrijd>_<opnamedatum-tijd>_<sha8>.<ext>, bijvoorbeeld 2024/nk-slalom-2024/nk-slalom-2024_20241019-143512_a874401b.jpg."""
    veilig = "".join(c if c.isalnum() or c in "-_" else "-" for c in slug)[:80] or "onbekend"
    opname = (taken_at or "")[:19].replace("-", "").replace(":", "").replace("T", "-") or "geen-datum"
    return f"{jaar or 'onbekend'}/{veilig}/{veilig}_{opname}_{sha[:8]}.{ext}"


def orig_hash_path(sha: str, ext: str) -> Path:
    """De oude indeling (tot versie 0.3): op hash. Alleen nog voor de migratie."""
    return config.ORIG_DIR / sha[:2] / f"{sha}.{ext}"


def orig_path(r) -> Path:
    """Het origineel van een foto-regel uit de database (nieuwe indeling, anders de oude op hash)."""
    if r["orig_rel"]: return config.ORIG_DIR / r["orig_rel"]
    return orig_hash_path(r["sha256"], r["ext"])


def opnamedatum_van(path: Path):
    try:
        with Image.open(path) as im: return opnamedatum(im)
    except Exception:  # noqa: BLE001
        return None


def maak_verkleind(src: Path, key: str) -> dict:
    """Maakt t.jpg (400) en m.jpg (1600) in derived_dir(key). Geeft breedte, hoogte (na draaien volgens EXIF) en opnamedatum."""
    d = derived_dir(key)
    d.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        taken = opnamedatum(im)
        im = ImageOps.exif_transpose(im)
        w, h = im.size
        if im.mode not in ("RGB", "L"): im = im.convert("RGB")
        for naam, px in (("m", config.MEDIUM_PX), ("t", config.THUMB_PX)):
            k = im.copy()
            k.thumbnail((px, px), Image.LANCZOS)
            k.save(d / f"{naam}.jpg", "JPEG", quality=config.JPEG_KWALITEIT, optimize=True, progressive=True)
    return {"width": w, "height": h, "taken_at": taken}


def verwijder_verkleind(key: str):
    d = derived_dir(key)
    for f in ("t.jpg", "m.jpg"):
        p = d / f
        if p.exists(): p.unlink()


def migreer_originelen(jaar_van) -> int:
    """Zet originelen die nog in de oude indeling (op hash) staan om naar <jaar>/<wedstrijd>/... en vult orig_rel. Geeft het aantal verplaatste bestanden.
    jaar_van(slug) geeft het jaar van een wedstrijd (None als onbekend: dan wacht die foto tot de archieflijst er is)."""
    from . import db
    import shutil
    n = 0
    for r in db.all_("SELECT * FROM photos WHERE orig_rel IS NULL AND status!='verwijderd'"):
        jaar = jaar_van(r["event_slug"])
        if not jaar: continue
        oud = orig_hash_path(r["sha256"], r["ext"])
        if not oud.exists(): continue
        rel = orig_rel(r["event_slug"], jaar, r["taken_at"], r["sha256"], r["ext"])
        nieuw = config.ORIG_DIR / rel
        nieuw.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(oud), str(nieuw))
        db.db().execute("UPDATE photos SET orig_rel=? WHERE id=?", (rel, r["id"]))
        n += 1
    if n: db.log("originelen.verplaatst", detail=f"{n} bestanden naar <jaar>/<wedstrijd>/")
    return n


# ---------------------------------------------------------------- testset voor de meting in F0
TEST_N = 60
_test_lock = threading.Lock()


def maak_testset(force=False):
    """60 'foto's' (water, lucht, een paar zeilen, wat ruis zodat de jpeg zo groot wordt als een echte foto) in DATA_DIR/test/<nn>/{t,m}.jpg."""
    config.TEST_DIR.mkdir(parents=True, exist_ok=True)
    with _test_lock:
        if not force and (config.TEST_DIR / f"{TEST_N:02d}" / "t.jpg").exists(): return
        _maak_testset()


def _maak_testset():
    rnd = random.Random(61)
    for n in range(1, TEST_N + 1):
        d = config.TEST_DIR / f"{n:02d}"
        d.mkdir(exist_ok=True)
        basis = _testfoto(rnd, n, 1600, 1067)
        _ruis(basis, 0.28).save(d / "m.jpg", "JPEG", quality=config.JPEG_KWALITEIT, optimize=True, progressive=True)
        t = basis.copy(); t.thumbnail((config.THUMB_PX, config.THUMB_PX), Image.LANCZOS)
        # ruis pas na het verkleinen: anders middelt het verkleinen de ruis weg en wordt de thumbnail onrealistisch klein (8 kB in plaats van 40)
        _ruis(t, 0.35).save(d / "t.jpg", "JPEG", quality=config.JPEG_KWALITEIT, optimize=True, progressive=True)


def _ruis(im, sterkte):
    """Ruis zoals golven en sensorruis: zonder dit comprimeert een vlak beeld tot een paar kB en zegt de meting niets."""
    return Image.blend(im, Image.effect_noise(im.size, 40).convert("RGB"), sterkte)


def _testfoto(rnd, n, w, h):
    lucht = (rnd.randint(120, 170), rnd.randint(160, 200), rnd.randint(210, 240))
    water = (rnd.randint(20, 60), rnd.randint(70, 120), rnd.randint(110, 160))
    kolom = Image.new("RGB", (1, h))
    px = kolom.load()
    horizon = int(h * rnd.uniform(0.38, 0.5))
    for y in range(h):
        if y < horizon:
            f = y / horizon
            px[0, y] = tuple(int(lucht[i] * (1 - f * 0.25) + 255 * f * 0.1) for i in range(3))
        else:
            f = (y - horizon) / (h - horizon)
            px[0, y] = tuple(int(water[i] * (1 - f * 0.3)) for i in range(3))
    im = kolom.resize((w, h), Image.NEAREST)   # verloop van lucht naar water, snel
    dr = ImageDraw.Draw(im)
    for _ in range(rnd.randint(2, 5)):   # zeilen
        x = rnd.randint(int(w * 0.1), int(w * 0.9)); top = rnd.randint(int(h * 0.2), horizon); voet = horizon + rnd.randint(5, 40)
        kleur = (rnd.randint(180, 255), rnd.randint(60, 230), rnd.randint(40, 200))
        dr.polygon([(x, top), (x + rnd.randint(60, 140), voet), (x - rnd.randint(10, 40), voet)], fill=kleur)
    dr.text((20, h - 40), f"testfoto {n:02d}", fill=(255, 255, 255))
    return im


def testpad(n: int, size: str) -> Path | None:
    if not (1 <= n <= TEST_N) or size not in ("t", "m"): return None
    p = config.TEST_DIR / f"{n:02d}" / f"{size}.jpg"
    return p if p.exists() else None

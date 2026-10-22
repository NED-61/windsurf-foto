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


def orig_path(sha: str, ext: str) -> Path:
    return config.ORIG_DIR / sha[:2] / f"{sha}.{ext}"


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

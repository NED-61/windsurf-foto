"""Instellingen van de fotodienst, allemaal uit omgevingsvariabelen (zie UNRAID.md).

Alleen ADMIN_EMAIL en ADMIN_PASSWORD zijn bij de eerste start nodig; de rest heeft een werkende standaard.
"""
import os
import secrets
from pathlib import Path

VERSIE = "0.2.0"  # F0 t/m F2

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))                       # SSD: database, verkleinde versies, cache
ORIG_DIR = Path(os.environ.get("ORIG_DIR", str(DATA_DIR / "orig")))        # harde schijven: de originelen
APP_URL = os.environ.get("APP_URL", "http://localhost:8000").rstrip("/")    # het publieke adres van deze dienst (funnel)
SITE_URL = os.environ.get("SITE_URL", "https://www.ned-61.com/windsurf-archief").rstrip("/")   # de archiefsite
SITE_ORIGINS = [o.strip().rstrip("/") for o in os.environ.get("SITE_ORIGINS", "https://www.ned-61.com,https://ned-61.com").split(",") if o.strip()]
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_NAME = os.environ.get("ADMIN_NAME", "Beheer")

SMTP_HOST = os.environ.get("SMTP_HOST", "")           # bijv. smtp.hostinger.com
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))   # 465 = SSL (Hostinger), 587 = STARTTLS
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
MAIL_FROM = os.environ.get("MAIL_FROM", SMTP_USER)

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "40"))
DAGLIMIET_NIEUW = int(os.environ.get("DAGLIMIET_NIEUW", "50"))   # uploads per dag voor een account dat nog niet vertrouwd is
THUMB_PX = 400     # lange zijde thumbnail
MEDIUM_PX = 1600   # lange zijde kijkversie
JPEG_KWALITEIT = 82
ARCHIEF_VERVERS_UREN = 6
TOEGESTANE_TYPES = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "HEIF": "heic"}
LICENTIES = {"archief": "Alleen op dit archief", "cc-by": "CC BY 4.0 (naamsvermelding)", "cc-by-nc": "CC BY-NC 4.0 (naamsvermelding, niet commercieel)"}

DERIVED_DIR = DATA_DIR / "derived"
TEST_DIR = DATA_DIR / "test"
ARCHIEF_DIR = DATA_DIR / "archief"
DB_PATH = DATA_DIR / "fotos.sqlite3"


def session_secret() -> bytes:
    """Sleutel voor sessies en csrf. Uit SESSION_SECRET, of anders eenmalig aangemaakt en bewaard in DATA_DIR/secret.txt
    (verandert die, dan is iedereen uitgelogd; verder gebeurt er niets)."""
    s = os.environ.get("SESSION_SECRET", "").strip()
    if len(s) >= 32: return s.encode()
    f = DATA_DIR / "secret.txt"
    if f.exists() and len(f.read_text().strip()) >= 32: return f.read_text().strip().encode()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    s = secrets.token_hex(32)
    f.write_text(s)
    return s.encode()


def is_https() -> bool:
    return APP_URL.startswith("https://")

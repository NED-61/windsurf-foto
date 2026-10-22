"""SQLite-database van de fotodienst: schema, verbinding en kleine hulpfuncties.

Eén bestand (DATA_DIR/fotos.sqlite3) in WAL-modus; één proces, dus geen locking-gedoe. Alle tijden zijn ISO-8601 in UTC.
"""
import sqlite3
import datetime
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  email_verified INTEGER NOT NULL DEFAULT 0,
  pw_hash TEXT NOT NULL,
  name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'lid',          -- lid | admin
  trusted INTEGER NOT NULL DEFAULT 0,        -- vertrouwd: uploads direct zichtbaar
  blocked INTEGER NOT NULL DEFAULT 0,
  blocked_reason TEXT,
  person_id TEXT,                            -- goedgekeurde claim op een rider uit people.json
  hide_profile INTEGER NOT NULL DEFAULT 0,   -- rider wil geen foto's op zijn profiel en geen tags met zijn naam
  created_at TEXT NOT NULL,
  last_login TEXT
);
CREATE TABLE IF NOT EXISTS tokens (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  kind TEXT NOT NULL,                        -- bevestig | reset
  expires_at TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS claims (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  person_id TEXT NOT NULL,
  note TEXT,
  status TEXT NOT NULL DEFAULT 'wacht',      -- wacht | goedgekeurd | afgewezen
  decided_by INTEGER, decided_at TEXT, decision_note TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claim_codes (
  code TEXT PRIMARY KEY,
  person_id TEXT NOT NULL,
  created_by INTEGER, created_at TEXT NOT NULL,
  used_by INTEGER, used_at TEXT
);
CREATE TABLE IF NOT EXISTS photos (
  id INTEGER PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,                  -- eerste 16 tekens van de sha256: adres van de verkleinde versies
  event_slug TEXT NOT NULL,
  uploader_id INTEGER NOT NULL,
  sha256 TEXT NOT NULL UNIQUE,
  ext TEXT NOT NULL,
  orig_rel TEXT,                             -- pad van het origineel binnen ORIG_DIR: <jaar>/<wedstrijd>/<wedstrijd>_<opname>_<sha8>.<ext>
  orig_name TEXT,
  size INTEGER, width INTEGER, height INTEGER,
  taken_at TEXT,
  uploaded_at TEXT NOT NULL,
  caption TEXT, credit TEXT,
  licence TEXT NOT NULL DEFAULT 'archief',
  orig_public INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'wacht',      -- wacht | zichtbaar | verborgen | verwijderd
  status_reason TEXT, status_by INTEGER, status_at TEXT,
  result_id TEXT
);
CREATE INDEX IF NOT EXISTS photos_event ON photos(event_slug, status);
CREATE INDEX IF NOT EXISTS photos_uploader ON photos(uploader_id, uploaded_at);
CREATE TABLE IF NOT EXISTS tags (                 -- fase F3: wie staat erop
  id INTEGER PRIMARY KEY,
  photo_id INTEGER NOT NULL,
  person_id TEXT, name TEXT,
  tagged_by INTEGER NOT NULL, created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'zichtbaar'   -- zichtbaar | wacht | verwijderd
);
CREATE INDEX IF NOT EXISTS tags_photo ON tags(photo_id);
CREATE INDEX IF NOT EXISTS tags_person ON tags(person_id, status);
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY,
  photo_id INTEGER NOT NULL,
  user_id INTEGER, reason TEXT NOT NULL, created_at TEXT NOT NULL,
  handled_by INTEGER, handled_at TEXT, handled_note TEXT
);
CREATE TABLE IF NOT EXISTS log (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  user_id INTEGER, action TEXT NOT NULL,
  photo_id INTEGER, target_user INTEGER, event_slug TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS log_at ON log(at);
CREATE TABLE IF NOT EXISTS outbox (               -- mails die (nog) niet verstuurd konden worden: te lezen op /beheer
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL, to_addr TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
  sent INTEGER NOT NULL DEFAULT 0, error TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, isolation_level=None, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con


_con = None


def db() -> sqlite3.Connection:
    global _con
    if _con is None:
        _con = connect()
        _con.executescript(SCHEMA)
        _migreer(_con)
    return _con


def _migreer(con):
    """Kolommen die na versie 0.2 zijn toegevoegd, voor een database die al bestond."""
    kol = {r["name"] for r in con.execute("PRAGMA table_info(users)")}
    if "hide_profile" not in kol: con.execute("ALTER TABLE users ADD COLUMN hide_profile INTEGER NOT NULL DEFAULT 0")
    kol = {r["name"] for r in con.execute("PRAGMA table_info(photos)")}
    if "orig_rel" not in kol: con.execute("ALTER TABLE photos ADD COLUMN orig_rel TEXT")


def reset():
    """Alleen voor tests: verbinding sluiten zodat een volgende aanroep een nieuwe database opent."""
    global _con
    if _con is not None:
        _con.close()
        _con = None


@contextmanager
def tx():
    con = db()
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def one(sql, *args):
    return db().execute(sql, args).fetchone()


def all_(sql, *args):
    return db().execute(sql, args).fetchall()


def kv_get(k, default=None):
    r = one("SELECT v FROM kv WHERE k=?", k)
    return r["v"] if r else default


def kv_set(k, v):
    db().execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def log(action, user_id=None, photo_id=None, target_user=None, event_slug=None, detail=None):
    db().execute("INSERT INTO log(at,user_id,action,photo_id,target_user,event_slug,detail) VALUES(?,?,?,?,?,?,?)",
                 (now(), user_id, action, photo_id, target_user, event_slug, detail))

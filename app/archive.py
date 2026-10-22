"""De lijst van wedstrijden en riders komt van de archiefsite zelf (assets/wedstrijden.json en assets/riders.json, geschreven door
tools/build_site.py in de archief-repo). Zo is data/people.json het enige ridersregister.

Beide bestanden worden in DATA_DIR/archief bewaard en elke ARCHIEF_VERVERS_UREN uur opnieuw opgehaald (op de achtergrond), of
op verzoek van de beheerder. Lukt het ophalen niet, dan blijft de laatste kopie in gebruik.
"""
import json
import threading
import time
import unicodedata
import urllib.request

from . import config, db

_lock = threading.Lock()
_cache = {"events": None, "riders": None, "loaded": 0.0}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def _lees(naam):
    f = config.ARCHIEF_DIR / f"{naam}.json"
    if not f.exists(): return None
    try: return json.loads(f.read_text(encoding="utf-8"))
    except Exception: return None  # noqa: BLE001


def _haal(naam):
    url = f"{config.SITE_URL}/assets/{naam}.json"
    req = urllib.request.Request(url, headers={"User-Agent": f"windsurf-foto/{config.VERSIE}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
    parsed = json.loads(data.decode("utf-8"))   # eerst controleren, dan pas wegschrijven
    config.ARCHIEF_DIR.mkdir(parents=True, exist_ok=True)
    (config.ARCHIEF_DIR / f"{naam}.json").write_bytes(data)
    return parsed


def ververs(force=False) -> str:
    """Haalt beide bestanden opnieuw op. Geeft een korte statusregel (voor /beheer)."""
    with _lock:
        try:
            ev, rd = _haal("wedstrijden"), _haal("riders")
            _cache.update(events=ev, riders=rd, loaded=time.time())
            db.kv_set("archief.opgehaald", db.now()); db.kv_set("archief.fout", "")
            return f"opgehaald: {len(ev)} wedstrijden, {len(rd)} riders"
        except Exception as e:  # noqa: BLE001
            db.kv_set("archief.fout", f"{db.now()} {e}")
            return f"ophalen mislukt: {e}"


def _zorg():
    """Laadt de cache uit DATA_DIR als dat nog niet gebeurd is en ververst zo nodig op de achtergrond."""
    if _cache["events"] is None:
        with _lock:
            if _cache["events"] is None:
                _cache["events"] = _lees("wedstrijden") or []
                _cache["riders"] = _lees("riders") or []
                _cache["loaded"] = time.time()
                if not _cache["events"] or not _cache["riders"]:
                    threading.Thread(target=ververs, daemon=True).start()
                    return
    opgehaald = db.kv_get("archief.opgehaald")
    if (not opgehaald or opgehaald < _tijd_geleden(config.ARCHIEF_VERVERS_UREN)) and time.time() - _cache["loaded"] > 60:
        _cache["loaded"] = time.time()   # niet elke aanvraag opnieuw proberen
        threading.Thread(target=ververs, daemon=True).start()


def _tijd_geleden(uren):
    import datetime
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=uren)).isoformat(timespec="seconds")


def events() -> dict:
    _zorg()
    return {e["slug"]: e for e in (_cache["events"] or [])}


def riders() -> dict:
    _zorg()
    return {r["id"]: r for r in (_cache["riders"] or [])}


def event(slug):
    return events().get(slug)


def rider(pid):
    return riders().get(pid)


def rider_naam(pid):
    r = rider(pid)
    return r["name"] if r else pid


def event_naam(slug):
    e = event(slug)
    return e["name"] if e else slug


def zoek_riders(q: str, limiet=12):
    toks = norm(q).split()
    if not toks: return []
    uit = []
    for r in riders().values():
        hooi = " " + norm(r["name"] + " " + " ".join(r.get("aliases", []))) + " "
        zeilen = " " + norm(" ".join(r.get("sails", []))) + " "
        score = 0
        for t in toks:
            if t.isdigit() and (" " + t + " ") in zeilen: score += 3
            elif (" " + t) in hooi: score += 2
            elif t in hooi or t in zeilen: score += 1
            else: score = 0; break
        if score: uit.append((score, r))
    uit.sort(key=lambda p: (-p[0], p[1]["name"]))
    return [r for _, r in uit[:limiet]]


def zoek_events(q: str, limiet=12):
    toks = norm(q).split()
    evs = sorted(events().values(), key=lambda e: e.get("date") or f"{e['year']}-12-31", reverse=True)
    if not toks: return evs[:limiet]
    uit = [e for e in evs if all(t in norm(f"{e['name']} {e['year']} {e.get('location') or ''} {' '.join(e.get('classes', []))}") for t in toks)]
    return uit[:limiet]


def status() -> dict:
    _zorg()
    return {"wedstrijden": len(_cache["events"] or []), "riders": len(_cache["riders"] or []),
            "opgehaald": db.kv_get("archief.opgehaald"), "fout": db.kv_get("archief.fout") or "", "bron": config.SITE_URL}

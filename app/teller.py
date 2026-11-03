"""Bezoekersteller voor de archiefsite (en de andere pagina's op ned-61.com): zonder cookies, en zonder IP-adressen te bewaren.

Elke paginaweergave stuurt één klein bericht (navigator.sendBeacon) naar POST /api/tel met het pad, de verwijzer en de schermbreedte.
Per weergave bewaard: dag en uur (Nederlandse tijd), site (archief of ned), pad, bron (de verwijzer samengevat: google, facebook,
intern, of de domeinnaam), apparaat (telefoon, tablet of computer) en een bezoekerscode: 16 tekens van sha256(dagsleutel + IP + browser).
De dagsleutel wordt bij de eerste weergave van een nieuwe dag vervangen en de oude is dan weg: bezoekers per dag zijn te tellen,
iemand over meerdere dagen volgen kan niet. Na BEWAAR_DAGEN worden de losse regels samengevat tot dagtotalen.
Plan: docs/bezoekersplan.md in de archief-repo (keuze van Max, 3 november 2026: een eigen teller).
"""
import datetime
import hashlib
import ipaddress
import json
import re
import secrets
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from . import config, db, archive

NL = ZoneInfo("Europe/Amsterdam")
ARCHIEF_PAD = "/windsurf-archief/"
EIGEN_HOSTS = {"ned-61.com"}
BOT_RE = re.compile(r"bot|crawl|spider|slurp|preview|headless|lighthouse|pingdom|monitor|curl|wget|python|httpx|java/|go-http", re.I)
BRONNEN = [(r"(^|\.)google\.[a-z.]+$", "google"), (r"(^|\.)bing\.com$", "bing"), (r"(^|\.)duckduckgo\.com$", "duckduckgo"),
           (r"(^|\.)(facebook\.com|fb\.me|fb\.com)$", "facebook"), (r"(^|\.)instagram\.com$", "instagram"), (r"^t\.co$|(^|\.)(x|twitter)\.com$", "x"),
           (r"(^|\.)linkedin\.com$|^lnkd\.in$", "linkedin"), (r"(^|\.)whatsapp\.(com|net)$", "whatsapp")]
MAX_PER_BEZOEKER = 500   # weergaven per bezoekerscode per dag; daarboven telt het niet (iemand die de teller wil opblazen)
BEWAAR_DAGEN = 400

SCHEMA = """
CREATE TABLE IF NOT EXISTS hits (
  id INTEGER PRIMARY KEY,
  dag TEXT NOT NULL,          -- datum in Nederlandse tijd, JJJJ-MM-DD
  uur INTEGER NOT NULL,       -- 0..23, Nederlandse tijd
  site TEXT NOT NULL,         -- archief | ned (de andere pagina's op ned-61.com)
  pad TEXT NOT NULL,          -- pad zonder zoekvraag, bijv. /windsurf-archief/rider/pieter-eliens.html
  bron TEXT,                  -- google, facebook, intern, een domeinnaam; NULL = direct (geen verwijzer, ook WhatsApp en bladwijzers)
  apparaat TEXT,              -- telefoon | tablet | computer
  bezoeker TEXT NOT NULL      -- sha256(dagsleutel + IP + browser), 16 tekens; na die dag niet meer te herleiden
);
CREATE INDEX IF NOT EXISTS hits_dag ON hits(site, dag);
CREATE TABLE IF NOT EXISTS hits_dagtotaal (    -- samengevat na BEWAAR_DAGEN
  dag TEXT NOT NULL, site TEXT NOT NULL, weergaven INTEGER NOT NULL, bezoekers INTEGER NOT NULL, PRIMARY KEY (dag, site)
);
"""


def _nu():
    return datetime.datetime.now(NL)


def _schema():
    if db.kv_get("tel.schema") != "1":
        db.db().executescript(SCHEMA)
        db.kv_set("tel.schema", "1")


def _dagsleutel(dag):
    """De sleutel van vandaag; bij een nieuwe dag een nieuwe (de oude wordt overschreven en is dan weg)."""
    k = db.kv_get("tel.sleutel", "")
    if k.startswith(dag + ":"): return k.split(":", 1)[1]
    nieuw = secrets.token_hex(16)
    db.kv_set("tel.sleutel", f"{dag}:{nieuw}")
    return nieuw


def bron_van(ref):
    host = (urlsplit(ref).hostname or "").lower() if ref else ""
    if not host: return None
    if host.startswith("www."): host = host[4:]
    if host in EIGEN_HOSTS: return "intern"
    for pat, naam in BRONNEN:
        if re.search(pat, host): return naam
    return host[:80]


def apparaat_van(w):
    try: w = int(w)
    except (TypeError, ValueError): return None
    if w <= 0: return None
    return "telefoon" if w < 600 else ("tablet" if w < 1100 else "computer")


def site_van(pad):
    return "archief" if pad.startswith(ARCHIEF_PAD) else "ned"


def registreer(origin, ip, ua, body) -> bool:
    """Eén weergave verwerken. Geeft True als hij is geteld. Telt niet: een ander domein dan SITE_ORIGINS, bots, onleesbare berichten."""
    if (origin or "").rstrip("/") not in config.SITE_ORIGINS: return False
    if not ua or BOT_RE.search(ua): return False
    try:
        d = json.loads(body or b"{}")
        pad = str(d.get("p") or "")
    except (ValueError, AttributeError):
        return False
    pad = pad.split("?")[0].split("#")[0][:300]
    if not pad.startswith("/"): return False
    _schema()
    nu = _nu(); dag = nu.date().isoformat()
    try: publiek = bool(ip) and ipaddress.ip_address(ip).is_global
    except ValueError: publiek = False
    # per dag onthouden of het echte adres van de bezoeker binnenkomt (via de funnel); zo niet, dan is de bezoekerscode alleen browser + dag
    if db.kv_get("tel.ip_dag") != dag:
        db.kv_set("tel.ip_dag", dag); db.kv_set("tel.ip", "ja" if publiek else "nee")
    elif publiek and db.kv_get("tel.ip") != "ja":
        db.kv_set("tel.ip", "ja")
    bezoeker = hashlib.sha256(f"{_dagsleutel(dag)}|{ip or ''}|{ua}".encode()).hexdigest()[:16]
    if db.one("SELECT COUNT(*) n FROM hits WHERE dag=? AND bezoeker=?", dag, bezoeker)["n"] >= MAX_PER_BEZOEKER: return False
    db.db().execute("INSERT INTO hits(dag,uur,site,pad,bron,apparaat,bezoeker) VALUES(?,?,?,?,?,?,?)",
                    (dag, nu.hour, site_van(pad), pad, bron_van(str(d.get("r") or "")), apparaat_van(d.get("w")), bezoeker))
    if db.kv_get("tel.opgeruimd") != dag:
        db.kv_set("tel.opgeruimd", dag); opruimen()
    return True


def opruimen(vandaag=None) -> int:
    """Regels ouder dan BEWAAR_DAGEN samenvatten tot dagtotalen (weergaven en bezoekers per dag en site) en de losse regels weghalen."""
    _schema()
    grens = ((vandaag or _nu().date()) - datetime.timedelta(days=BEWAAR_DAGEN)).isoformat()
    with db.tx() as con:
        con.execute("INSERT OR REPLACE INTO hits_dagtotaal(dag,site,weergaven,bezoekers) "
                    "SELECT dag, site, COUNT(*), COUNT(DISTINCT bezoeker) FROM hits WHERE dag<? GROUP BY dag, site", (grens,))
        n = con.execute("DELETE FROM hits WHERE dag<?", (grens,)).rowcount
    if n: db.log("teller.samengevat", detail=f"{n} regels van voor {grens}")
    return n


def label(pad):
    """Leesbare naam voor een pagina van het archief: de rider, de wedstrijd of de uitslag."""
    m = re.match(r"^/windsurf-archief/(rider|wedstrijd|uitslag)/([^/]+)\.html$", pad)
    if m:
        soort, slug = m.groups()
        if soort == "rider": return f"rider: {archive.rider_naam(slug) or slug}"
        if soort == "wedstrijd": return f"wedstrijd: {(archive.event(slug) or {}).get('name') or slug}"
        return f"uitslag: {slug}"
    if pad in (ARCHIEF_PAD, ARCHIEF_PAD + "index.html"): return "startpagina archief"
    if pad.startswith(ARCHIEF_PAD): return pad[len(ARCHIEF_PAD):]
    return pad


def overzicht(site="archief", dagen=30) -> dict:
    """Cijfers voor /beheer?tab=bezoek. site = archief, ned of alles."""
    _schema()
    w, args = ("", []) if site == "alles" else ("AND site=?", [site])
    vandaag = _nu().date()
    van = (vandaag - datetime.timedelta(days=dagen - 1)).isoformat()
    per_dag = {r["dag"]: (r["v"], r["b"]) for r in db.all_(f"SELECT dag, COUNT(*) v, COUNT(DISTINCT bezoeker) b FROM hits WHERE dag>=? {w} GROUP BY dag", van, *args)}
    dagrij = []
    for i in range(dagen):
        d = (vandaag - datetime.timedelta(days=i)).isoformat()
        v, b = per_dag.get(d, (0, 0)); dagrij.append({"dag": d, "weergaven": v, "bezoekers": b})
    som = lambda n: {"bezoekers": sum(x["bezoekers"] for x in dagrij[:n]), "weergaven": sum(x["weergaven"] for x in dagrij[:n])}
    maanden = {}
    for r in db.all_(f"SELECT substr(dag,1,7) m, SUM(v) v, SUM(b) b FROM (SELECT dag, COUNT(*) v, COUNT(DISTINCT bezoeker) b FROM hits WHERE 1=1 {w} GROUP BY dag) GROUP BY m", *args):
        maanden[r["m"]] = [r["v"], r["b"]]
    for r in db.all_(f"SELECT substr(dag,1,7) m, SUM(weergaven) v, SUM(bezoekers) b FROM hits_dagtotaal WHERE 1=1 {w} GROUP BY m", *args):
        x = maanden.setdefault(r["m"], [0, 0]); x[0] += r["v"]; x[1] += r["b"]
    paginas = [{"pad": r["pad"], "naam": label(r["pad"]), "weergaven": r["v"], "bezoekers": r["b"]}
               for r in db.all_(f"SELECT pad, COUNT(*) v, COUNT(DISTINCT dag || bezoeker) b FROM hits WHERE dag>=? {w} GROUP BY pad ORDER BY v DESC, pad LIMIT 30", van, *args)]
    bronnen = [{"bron": r["bron"] or "direct", "bezoekers": r["b"]}
               for r in db.all_(f"SELECT bron, COUNT(DISTINCT dag || bezoeker) b FROM hits WHERE dag>=? {w} AND COALESCE(bron,'')!='intern' GROUP BY bron ORDER BY b DESC LIMIT 20", van, *args)]
    app = {r["a"] or "onbekend": r["b"] for r in db.all_(f"SELECT apparaat a, COUNT(DISTINCT dag || bezoeker) b FROM hits WHERE dag>=? {w} GROUP BY apparaat", van, *args)}
    tot = sum(app.values()) or 1
    return {"site": site, "dagen": dagrij, "max_dag": max([x["bezoekers"] for x in dagrij] + [1]),
            "vandaag": som(1), "week": som(7), "maand": som(dagen),
            "maanden": sorted(([m, v, b] for m, (v, b) in maanden.items()), reverse=True)[:13],
            "paginas": paginas, "bronnen": bronnen, "apparaten": sorted(((a, n, round(100 * n / tot)) for a, n in app.items()), key=lambda x: -x[1]),
            "ip_door": db.kv_get("tel.ip"), "sinds": (db.one("SELECT MIN(dag) d FROM hits")["d"])}

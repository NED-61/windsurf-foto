"""Gedeelde hulpjes voor de pagina's: templates, datumopmaak, foto-als-json, doorsturen met een bericht."""
import datetime
import zoneinfo
from pathlib import Path
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import config, db, auth, archive

TZ = zoneinfo.ZoneInfo("Europe/Amsterdam")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def fmt_dt(s, met_tijd=True):
    """ISO-tijd (UTC of zonder zone) naar '3 okt 2026, 14:05'."""
    if not s: return ""
    try:
        d = datetime.datetime.fromisoformat(s)
        if d.tzinfo: d = d.astimezone(TZ)
        mnd = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"][d.month - 1]
        return f"{d.day} {mnd} {d.year}" + (f", {d:%H:%M}" if met_tijd else "")
    except ValueError:
        return s


def fmt_bytes(n):
    if not n: return ""
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} kB"


templates.env.filters["dt"] = fmt_dt
templates.env.filters["datum"] = lambda s: fmt_dt(s, met_tijd=False)
templates.env.filters["mb"] = fmt_bytes
templates.env.globals.update(cfg=config, LICENTIES=config.LICENTIES, event_naam=archive.event_naam, rider_naam=archive.rider_naam)


def render(request: Request, naam: str, status_code: int = 200, **ctx):
    u = auth.current_user(request)
    ctx.setdefault("user", u)
    ctx.setdefault("csrf", auth.csrf_token(request))
    ctx.setdefault("bericht", request.query_params.get("bericht"))
    ctx.setdefault("wachtrij_n", db.one("SELECT COUNT(*) n FROM photos WHERE status='wacht'")["n"] if auth.is_admin(u) else 0)
    return templates.TemplateResponse(request, naam, ctx, status_code=status_code)


def redirect(url: str, bericht: str | None = None, status_code: int = 303):
    if bericht: url += ("&" if "?" in url else "?") + "bericht=" + quote(bericht)
    return RedirectResponse(url, status_code=status_code)


def thumb_maat(w, h, px=config.THUMB_PX):
    if not w or not h: return px, px
    f = px / max(w, h)
    return max(1, round(w * f)), max(1, round(h * f))


def foto_json(r, met_status=False) -> dict:
    base = config.APP_URL
    tw, th = thumb_maat(r["width"], r["height"])
    d = {"id": r["id"], "key": r["key"], "wedstrijd": r["event_slug"],
         "t": f"{base}/i/{r['key']}/t.jpg", "m": f"{base}/i/{r['key']}/m.jpg", "w": r["width"], "h": r["height"], "tw": tw, "th": th,
         "caption": r["caption"] or "", "credit": r["credit"] or "", "licence": r["licence"], "taken_at": r["taken_at"],
         "uploaded_at": r["uploaded_at"], "url": f"{base}/foto/{r['id']}",
         "tags": [{"person": t["person_id"], "name": t["name"] or archive.rider_naam(t["person_id"])}
                  for t in db.all_("SELECT person_id,name FROM tags WHERE photo_id=? AND status='zichtbaar'", r["id"])]}
    if met_status: d["status"] = r["status"]
    return d


def zichtbare_fotos(where: str, *args, limiet=None):
    sql = f"SELECT * FROM photos WHERE status='zichtbaar' AND {where} ORDER BY COALESCE(taken_at, uploaded_at), id"
    if limiet: sql += f" LIMIT {int(limiet)}"
    return db.all_(sql, *args)

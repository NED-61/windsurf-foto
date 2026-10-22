# windsurf-foto

De fotodienst van het [Windsurf Wedstrijdarchief](https://www.ned-61.com/windsurf-archief/). De uitslagen staan als statische site op Hostinger (repo [windsurf-archief](https://github.com/NED-61/windsurf-archief)); de foto's staan op de NAS thuis en komen via deze dienst op de site. Het plan staat in `docs/fotoplan.md` van de archief-repo.

Wat de dienst doet (fase F0 t/m F2):

- Foto's per wedstrijd bewaren: het origineel byte-identiek op de harde schijven, een kijkversie (1600 px) en een thumbnail (400 px) zonder EXIF op de SSD.
- Een openbare api waar de archiefsite de foto's van een wedstrijd ophaalt (`/api/wedstrijd/<slug>`), plus de afbeeldingen zelf (`/i/<key>/t.jpg`, `/i/<key>/m.jpg`) met cache-headers voor een maand.
- Accounts: registreren met e-mailbevestiging, inloggen, wachtwoord vergeten; een ridernaam claimen (beoordeeld door de beheerder, of direct met een claimcode).
- Uploaden: wedstrijd kiezen, foto's slepen, licentie en toestemming aanvinken; nieuwe accounts komen in de wachtrij, riders en vertrouwde accounts publiceren direct.
- Beheer op `/beheer`: wachtrij, meldingen, claims en claimcodes, accounts (vertrouwd, blokkeren), log, mail-outbox, archiefkoppeling.
- Een testset van 60 foto's (`/api/test`) voor de bandbreedtemeting van F0.

Nog niet (F3 en verder): taggen wie op een foto staat, foto's op de riderpagina, export naar `media/media.json`.

## Hoe het samenhangt

```
archiefsite (Hostinger)  --fetch /api/wedstrijd/<slug>-->  windsurf-foto (NAS, via Tailscale Funnel)
       |                                                        |
       |  assets/wedstrijden.json, assets/riders.json           |  haalt die twee bestanden elke 6 uur op:
       +---------------------------------------------------------+  de enige lijst van wedstrijden en riders
```

De dienst kent zelf geen wedstrijden en riders: die komen van de site (`tools/build_site.py` schrijft ze). Zonder die bestanden kan er niet geüpload worden (op `/beheer?tab=archief` staat of het ophalen lukt).

## Draaien

Op de NAS: zie [UNRAID.md](UNRAID.md). Lokaal:

```bash
python -m venv venv && venv/Scripts/pip install -r requirements.txt httpx pytest     # Windows; op Linux venv/bin/pip
set DATA_DIR=.data && set ADMIN_EMAIL=jij@example.org && set ADMIN_PASSWORD=een-lang-wachtwoord
venv/Scripts/python -m uvicorn app.main:app --reload
```

Dan <http://localhost:8000>. Zonder bereikbare archiefsite is de wedstrijdenlijst leeg; zet voor een test twee bestanden `wedstrijden.json` en `riders.json` in `.data/archief/` (zie `tests/test_app.py` voor het formaat).

Tests: `venv/Scripts/python -m pytest -q`.

## Instellingen (omgevingsvariabelen)

| Variabele | Betekenis | Standaard |
|---|---|---|
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | het beheeraccount, aangemaakt bij de eerste start | (verplicht) |
| `APP_URL` | het publieke adres van deze dienst, voor links in mails en in de api | `http://localhost:8000` |
| `SITE_URL` | de archiefsite, waar `assets/wedstrijden.json` en `riders.json` staan | `https://www.ned-61.com/windsurf-archief` |
| `SITE_ORIGINS` | sites die de api mogen aanroepen (CORS), kommagescheiden | `https://www.ned-61.com,https://ned-61.com` |
| `DATA_DIR` | database, verkleinde versies, cache (SSD) | `/data` |
| `ORIG_DIR` | originelen (harde schijven) | `/orig` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM` | mail versturen (Hostinger: `smtp.hostinger.com`, 465) | leeg: mails blijven in de outbox op `/beheer?tab=mail` |
| `SESSION_SECRET` | sleutel voor sessies; leeg = eenmalig aangemaakt in `DATA_DIR/secret.txt` | leeg |
| `MAX_UPLOAD_MB`, `DAGLIMIET_NIEUW` | grootste upload; uploads per dag voor een nieuw account | 40, 50 |
| `TESTSET` | `0` = de testset van F0 niet aanmaken | `1` |

## Indeling

```
app/main.py            app, opstart (beheeraccount, testset), koppen, foutpagina's
app/routes_public.py   /api/*, /i/<key>/<t|m>.jpg, /o/<id>, /, /wedstrijd/<slug>, /foto/<id>, melden
app/routes_account.py  registreren, bevestigen, inloggen, wachtwoord, /account, /claim, /upload, /api/upload, bewerken, verwijderen
app/routes_admin.py    /beheer en alle beheeracties
app/images.py          herkennen, hash, EXIF, verkleinen, testset
app/archive.py         wedstrijden en riders ophalen van de site
app/auth.py            wachtwoorden (scrypt), sessiecookie, csrf, limiet per adres
app/mail.py            SMTP en outbox
app/db.py              schema (users, claims, claim_codes, photos, tags, reports, log, outbox, kv)
app/templates/         Jinja2; app/static/site.css is een kopie uit de archief-repo (tools/site_assets/site.css)
tests/test_app.py      de hele keten in een tijdelijke map
```

Statussen van een foto: `wacht` (wachtrij), `zichtbaar`, `verborgen` (door beheer, met reden), `verwijderd` (door uploader of beheer; het origineel staat dan in `ORIG_DIR/verwijderd/`). Er wordt nooit iets echt weggegooid.

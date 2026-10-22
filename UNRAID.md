# windsurf-foto op de Unraid

Voor de server "Media server" (Unraid 7.3.2, LAN `192.168.2.34`), waar fvjl-playbook al op draait via Tailscale Funnel op poort 8443. Deze dienst komt ernaast, op het funnel-adres zonder poortnummer (443). Niets in de router hoeft open.

## Stap 1: het image laten bouwen (GitHub)

1. Repo gepusht? Op github.com, tabblad **Actions**, draait *Image bouwen*: eerst de tests, dan het image. Groen vinkje = klaar.
2. Repo-startpagina, rechts **Packages** → `windsurf-foto` → **Package settings** → onderaan **Change visibility** → **Public**. Anders kan Unraid het image niet ophalen. (De code is openbaar en bevat geen sleutels.)

Het image heet `ghcr.io/ned-61/windsurf-foto:latest`.

## Stap 2: mappen

In de Unraid-terminal (het icoontje `>_` rechtsboven):

```bash
mkdir -p /mnt/user/appdata/windsurf-foto /mnt/user/fotos/windsurf-archief
chown -R nobody:users /mnt/user/appdata/windsurf-foto /mnt/user/fotos/windsurf-archief
```

`appdata` staat op de SSD (cache): database, verkleinde versies. `fotos` is een share op de array (harde schijven): de originelen. Bestaat de share `fotos` nog niet, maak hem eerst aan onder **SHARES** → **Add Share** (naam `fotos`, primary storage Array).

## Stap 3: de container

**DOCKER** → **ADD CONTAINER**, rechtsboven **Advanced View** aan.

| Veld | Waarde |
|---|---|
| Name | `windsurf-foto` |
| Repository | `ghcr.io/ned-61/windsurf-foto:latest` |
| Network Type | bridge |
| Extra Parameters | `--user 99:100` |

Dan met **Add another Path, Port, Variable, Label or Device**:

| Type | Name | Container | Host / waarde |
|---|---|---|---|
| Port | web | 8000 | 8000 |
| Path | data | `/data` | `/mnt/user/appdata/windsurf-foto` |
| Path | orig | `/orig` | `/mnt/user/fotos/windsurf-archief` |
| Variable | ADMIN_EMAIL | `ADMIN_EMAIL` | jouw e-mailadres |
| Variable | ADMIN_PASSWORD | `ADMIN_PASSWORD` | een lang wachtwoord (minstens 10 tekens) |
| Variable | APP_URL | `APP_URL` | `https://homelab257.tailc2d973.ts.net` (het funnel-adres uit stap 4; eerst invullen, zo nodig later aanpassen) |
| Variable | TZ | `TZ` | `Europe/Amsterdam` |

Mail (later, mag ook pas bij F2 in gebruik):

| Type | Name | Container | Host / waarde |
|---|---|---|---|
| Variable | SMTP_HOST | `SMTP_HOST` | `smtp.hostinger.com` |
| Variable | SMTP_PORT | `SMTP_PORT` | `465` |
| Variable | SMTP_USER | `SMTP_USER` | de mailbox, bijv. `foto@ned-61.com` |
| Variable | SMTP_PASS | `SMTP_PASS` | het wachtwoord van die mailbox |
| Variable | MAIL_FROM | `MAIL_FROM` | hetzelfde adres |

De mailbox maak je in hPanel → E-mail. Zolang SMTP niet is ingevuld, worden mails niet verstuurd maar staan ze met de links op `/beheer?tab=mail`; je kunt een bevestigingslink dan zelf doorgeven.

**Apply.** Check vanaf een pc in huis: <http://192.168.2.34:8000/api/health> geeft `{"ok": true, ...}`. Inloggen op <http://192.168.2.34:8000/inloggen> met ADMIN_EMAIL en ADMIN_PASSWORD.

Gaat het mis:

- `permission denied` in de log: controleer de `chown` uit stap 2, of zet Extra Parameters tijdelijk op `--user 0:0`.
- `manifest unknown` of `denied`: het package staat nog niet op Public (stap 1.2).
- De pagina zegt dat de wedstrijdenlijst niet beschikbaar is: de archiefsite heeft `assets/wedstrijden.json` nog niet (dat komt mee met de volgende build van de site). Kijk op `/beheer?tab=archief`.

## Stap 4: publiek zetten met Tailscale Funnel

In de Unraid-terminal:

```bash
tailscale funnel --bg 8000
tailscale funnel status
```

De status toont het adres, bijvoorbeeld `https://homelab257.tailc2d973.ts.net` (poort 443). fvjl op 8443 blijft gewoon staan.

Is poort 443 al bezet (de status laat dat zien), gebruik dan 10000:

```bash
tailscale funnel --bg --https=10000 8000
```

Het adres is dan `https://homelab257.tailc2d973.ts.net:10000`. Zet in dat geval `APP_URL` in de container op precies dat adres (met `:10000`) en pas `FOTO_URL` in `tools/build_site.py` van de archief-repo aan.

Uitzetten kan altijd: `tailscale funnel --https=443 off`.

**Check:** open `https://homelab257.tailc2d973.ts.net/api/health` op je telefoon met wifi uit.

## Stap 5: de meting (F0)

1. Open op je telefoon, buiten huis of met wifi uit: `https://www.ned-61.com/windsurf-archief/fototest.html`. De pagina laadt 60 thumbnails van de NAS en toont hoeveel seconden dat duurde en hoeveel MB het was. Druk twee of drie keer op "Opnieuw" (de tweede keer komt uit de browsercache: daarom staat er een knop die de cache omzeilt).
2. Doe hetzelfde met drie apparaten tegelijk (telefoon, laptop op hotspot, iemand anders).
3. Klik een thumbnail aan: de grote versie (300 kB) moet in minder dan een seconde komen.

Vlot (de 60 thumbnails binnen een paar seconden, ook met drie tegelijk)? Dan is Funnel definitief. Hapert het, of duurt het tien seconden of meer, dan komt de cache-optie (Cloudflare, K1 in het fotoplan) alsnog op tafel voordat er foto's bij komen.

Staat de funnel op een ander adres dan `https://homelab257.tailc2d973.ts.net`, zet dat dan achter de testpagina: `fototest.html?foto=https://homelab257.tailc2d973.ts.net:10000`.

## Bijwerken

Na een push naar `main` bouwt GitHub een nieuw image. Op de Unraid: **DOCKER** → klik op het icoon van `windsurf-foto` → **Force update** (of **Check for Updates** bovenaan). Database en foto's staan in de twee mappen en blijven staan.

## Back-up

De database (`/mnt/user/appdata/windsurf-foto/fotos.sqlite3`) en de originelen (`/mnt/user/fotos/windsurf-archief/`) zijn het enige exemplaar. Maak er een kopie van buiten de NAS (de plugin *Appdata Backup* voor appdata; voor de originelen een rsync naar een USB-schijf of Backblaze B2, zie K8 in het fotoplan). De verkleinde versies zijn opnieuw te maken uit de originelen.

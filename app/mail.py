"""Mail versturen via SMTP (Hostinger: smtp.hostinger.com, poort 465, SSL).

Is SMTP niet ingesteld of mislukt het versturen, dan komt de mail in de outbox-tabel en staat hij op /beheer onder 'Mail',
met de link erin: zo kan de beheerder een bevestigingslink zelf doorgeven zolang de mail nog niet werkt.
"""
import smtplib
import ssl
import threading
from email.message import EmailMessage

from . import config, db

AFZENDER_NAAM = "Windsurf Wedstrijdarchief"


def _smtp_send(to, subject, body):
    msg = EmailMessage()
    msg["From"] = f"{AFZENDER_NAAM} <{config.MAIL_FROM}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if config.SMTP_PORT == 465:
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ssl.create_default_context(), timeout=20) as s:
            s.login(config.SMTP_USER, config.SMTP_PASS)
            s.send_message(msg)
    else:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(config.SMTP_USER, config.SMTP_PASS)
            s.send_message(msg)


def send(to: str, subject: str, body: str, achtergrond: bool = True):
    """Verstuurt (op de achtergrond, zodat een pagina niet op de mailserver wacht) en legt het resultaat vast in de outbox."""
    con = db.db()
    cur = con.execute("INSERT INTO outbox(at,to_addr,subject,body) VALUES(?,?,?,?)", (db.now(), to, subject, body))
    mid = cur.lastrowid
    if not config.SMTP_HOST:
        con.execute("UPDATE outbox SET error=? WHERE id=?", ("SMTP niet ingesteld", mid))
        return

    def werk():
        try:
            _smtp_send(to, subject, body)
            db.db().execute("UPDATE outbox SET sent=1, error=NULL WHERE id=?", (mid,))
        except Exception as e:  # noqa: BLE001
            db.db().execute("UPDATE outbox SET error=? WHERE id=?", (str(e)[:300], mid))

    if achtergrond: threading.Thread(target=werk, daemon=True).start()
    else: werk()


def admins():
    return [r["email"] for r in db.all_("SELECT email FROM users WHERE role='admin' AND blocked=0")]


def mail_admins(subject: str, body: str):
    for a in admins(): send(a, subject, body)


# ---------------------------------------------------------------- teksten
def bevestiging(naam, link):
    return ("Bevestig je e-mailadres",
            f"Hoi {naam},\n\nJe hebt een account gemaakt bij de foto's van het Windsurf Wedstrijdarchief. Bevestig je e-mailadres met deze link "
            f"(geldig 24 uur):\n\n{link}\n\nHeb je dit niet gedaan, dan kun je deze mail negeren.\n")


def wachtwoord_reset(naam, link):
    return ("Nieuw wachtwoord instellen",
            f"Hoi {naam},\n\nMet deze link stel je een nieuw wachtwoord in (geldig 2 uur):\n\n{link}\n\nHeb je dit niet aangevraagd, dan kun je deze mail negeren.\n")


def claim_beslist(naam, rider, goedgekeurd, toelichting):
    if goedgekeurd:
        return ("Je ridernaam is bevestigd", f"Hoi {naam},\n\nJe account is gekoppeld aan {rider}. Je foto's en tags zijn vanaf nu direct zichtbaar.\n\n{config.APP_URL}/account\n")
    return ("Je claim is niet goedgekeurd", f"Hoi {naam},\n\nJe claim op {rider} is niet goedgekeurd." + (f" Toelichting: {toelichting}" if toelichting else "") + f"\n\nVragen? Antwoord op deze mail.\n")


def foto_beslist(naam, n, zichtbaar, reden):
    if zichtbaar:
        return ("Je foto's staan online", f"Hoi {naam},\n\n{n} foto's van je zijn goedgekeurd en staan nu op de site.\n\n{config.APP_URL}/account\n")
    return ("Een foto van je is niet geplaatst", f"Hoi {naam},\n\n{n} foto's van je zijn niet geplaatst." + (f" Reden: {reden}" if reden else "") + f"\n\n{config.APP_URL}/account\n")

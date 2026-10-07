#!/usr/bin/env python3

import argparse
import getpass
import json
import os
import smtplib
import ssl
import sys
import time

from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path


SMTP_HOST = "mxe9aa.netcup.net"
SMTP_PORT = 465
SMTP_USER = "kontakt@digitalskills-campus.de"

FROM_NAME = "Hasib Gharibyar | Digital Skills Campus"
FROM_EMAIL = "kontakt@digitalskills-campus.de"

SENT_LOG = Path(".sent_outreach.json")


def build_subject(company: str) -> str:
    return f"IT-Sicherheitsangebote für {company}"


def build_body(company: str) -> str:
    return f"""Liebes Team von {company},

heute möchte ich die Gelegenheit nutzen, mich Ihnen kurz vorzustellen. Mein Name ist Hasib Gharibyar. Ich bin studierter Informatiker, Gründer von Digital Skills Campus und seit mehreren Jahren im Bereich IT-Sicherheit tätig.

Mit meiner Selbstständigkeit möchte ich insbesondere Kanzleien bei der Absicherung ihrer IT unterstützen. Gerade dort werden täglich sensible Mandatsdaten verarbeitet, weshalb bereits einzelne Fehlkonfigurationen bei E-Mail, Windows-Systemen oder extern erreichbaren Diensten erhebliche Folgen haben können.

Da ich Digital Skills Campus derzeit weiter aufbaue und mir einen Kundenstamm sowie Referenzen aufbauen möchte, biete ich ausgewählten Kanzleien in Karlsruhe aktuell folgende Leistungen zu vergünstigten Einführungskonditionen an:

🔐 Penetrationstest – 1.100 € Einführungspreis

Gezielte Prüfung einer klar abgegrenzten Webanwendung oder extern erreichbaren Infrastruktur – inklusive Schwachstellenbericht, Risikobewertung und konkreten Handlungsempfehlungen.

🖥️ Windows Security Assessment – 690 €

Prüfung zentraler Windows-Sicherheitskonfigurationen und typischer Fehlkonfigurationen – inklusive priorisiertem Maßnahmenbericht.

Falls eines der Angebote für Sie interessant ist, sende ich Ihnen gerne unverbindlich weitere Informationen zu Umfang und Ablauf.

Darüber hinaus biete ich bei Interesse auch eine langfristige Zusammenarbeit im Bereich Managed IT & Security sowie praxisnahe IT-Sicherheitsschulungen für Mitarbeitende an.

Mit freundlichen Grüßen

Hasib Gharibyar, M.Sc.
Digital Skills Campus
E-Mail: kontakt@digitalskills-campus.de
Web: https://digitalskills-campus.de
Tel.: 01575 5440113
"""


def load_sent() -> set[str]:
    if not SENT_LOG.exists():
        return set()

    try:
        data = json.loads(SENT_LOG.read_text(encoding="utf-8"))
        return set(data.get("sent", []))
    except Exception:
        return set()


def save_sent(sent: set[str]) -> None:
    SENT_LOG.write_text(
        json.dumps(
            {"sent": sorted(sent)},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def build_message(company: str, email: str) -> EmailMessage:
    msg = EmailMessage()

    msg["From"] = formataddr((FROM_NAME, FROM_EMAIL))
    msg["To"] = email
    msg["Reply-To"] = FROM_EMAIL
    msg["Subject"] = build_subject(company)

    msg.set_content(build_body(company))

    return msg


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "json_file",
        nargs="?",
        default="karlsruhe_kanzleien_outreach.json",
    )

    parser.add_argument(
        "--send",
        action="store_true",
        help="Mails tatsächlich versenden",
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Pause zwischen Mails in Sekunden",
    )

    args = parser.parse_args()

    path = Path(args.json_file)

    if not path.exists():
        print(f"Datei nicht gefunden: {path}", file=sys.stderr)
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    leads = data.get("leads", [])

    if not leads:
        print("Keine Leads gefunden.")
        return 1

    sent = load_sent()

    pending = [
        lead
        for lead in leads
        if lead["email"].lower() not in sent
    ]

    print()
    print(f"{len(leads)} Kanzleien geladen.")
    print(f"{len(sent)} bereits versendet.")
    print(f"{len(pending)} noch offen.")
    print()

    for lead in pending:
        print(
            f"- {lead['company']} "
            f"<{lead['email']}>"
        )

    if not pending:
        print("\nKeine neuen Mails zu versenden.")
        return 0

    # Standardmäßig nur Dry-Run
    if not args.send:
        print()
        print("DRY-RUN: Es wurde nichts versendet.")
        print()
        print("Zum tatsächlichen Versand:")
        print(
            f"python3 {Path(sys.argv[0]).name} "
            f"{path} --send"
        )
        return 0

    print()
    confirmation = input(
        f"Wirklich alle {len(pending)} Mails versenden? "
        "Tippe JA: "
    ).strip()

    if confirmation != "JA":
        print("Abgebrochen.")
        return 0

    password = os.environ.get("DSC_SMTP_PASSWORD")

    if not password:
        password = getpass.getpass(
            f"Passwort für {SMTP_USER}: "
        )

    context = ssl.create_default_context()

    successful = 0
    failed = 0

    with smtplib.SMTP_SSL(
        SMTP_HOST,
        SMTP_PORT,
        context=context,
    ) as smtp:

        smtp.login(SMTP_USER, password)

        for lead in pending:
            company = lead["company"]
            email = lead["email"].lower()

            msg = build_message(company, email)

            try:
                smtp.send_message(msg)

                sent.add(email)
                save_sent(sent)

                successful += 1

                print(
                    f"✓ {company} -> {email}"
                )

            except Exception as exc:
                failed += 1
                print(
                    f"✗ {company}: {exc}",
                    file=sys.stderr,
                )

            time.sleep(args.delay)

    print()
    print(f"Erfolgreich: {successful}")
    print(f"Fehler:      {failed}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
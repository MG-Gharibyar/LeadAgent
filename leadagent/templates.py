from __future__ import annotations

INTRO = (
    "Mein Name ist Hasib Gharibyar. Ich bin Informatiker und Ansprechpartner bei "
    "Digital Skills Campus in Karlsruhe."
)
SIGNATURE = (
    "Mit freundlichen Grüßen\n\n"
    "Hasib Gharibyar, M.Sc.\n"
    "Digital Skills Campus\n"
    "E-Mail: kontakt@digitalskills-campus.de\n"
    "Web: https://digitalskills-campus.de"
)

SECTORS = {
    "law_firm": """Guten Tag,

ich wende mich an {company}, weil IT-Sicherheit für kleine und mittlere Unternehmen zunehmend ein operatives Thema ist.

{intro}

Kanzleien verarbeiten täglich sensible Mandats- und Kommunikationsdaten. Gerade bei Windows, E-Mail, Microsoft 365 und extern erreichbaren Diensten können einzelne Fehlkonfigurationen unnötige Risiken schaffen.

Wir unterstützen kleine und mittlere Kanzleien mit individuell abgestimmten IT-Sicherheitsprüfungen. In einem kurzen unverbindlichen Austausch klären wir zunächst, welche Systeme und Themen für Sie tatsächlich relevant sind. Anschließend erhalten Sie einen klar abgegrenzten Vorschlag, zum Beispiel für ein Windows Security Assessment, eine Microsoft-365-Prüfung oder einen technischen Penetrationstest.

Wenn das Thema für Sie grundsätzlich interessant ist, können wir uns gerne einmal 15 Minuten unverbindlich austauschen.

{signature}
""",
    "medical_practice": """Guten Tag,

ich wende mich an {company}, weil IT-Sicherheit für kleine und mittlere Unternehmen zunehmend ein operatives Thema ist.

{intro}

Arzt- und Zahnarztpraxen, MVZ und Psychotherapiepraxen verarbeiten sensible Patientendaten und sind auf zuverlässig verfügbare IT angewiesen.

Wir unterstützen kleinere und mittlere Praxen individuell bei Windows-Sicherheit, Backup & Recovery und der Absicherung zentraler IT-Dienste. In einem kurzen unverbindlichen Austausch schauen wir zunächst, welche Systeme bei Ihnen im Einsatz sind und wo eine Prüfung sinnvoll wäre. Danach schlagen wir einen klar abgegrenzten passenden Umfang vor.

Wenn das Thema für Sie grundsätzlich interessant ist, können wir uns gerne einmal 15 Minuten unverbindlich austauschen.

{signature}
""",
    "tax_advisor": """Guten Tag,

ich wende mich an {company}, weil IT-Sicherheit für kleine und mittlere Unternehmen zunehmend ein operatives Thema ist.

{intro}

In Steuerberatung und Lohnbuchhaltung werden sensible Finanz- und Mandantendaten verarbeitet. Windows, E-Mail, Microsoft 365 und digitaler Dokumentenaustausch sollten deshalb sauber abgesichert und zuverlässig wiederherstellbar sein.

Wir unterstützen kleine und mittlere Kanzleien individuell bei Windows-Sicherheit, Microsoft-365-Sicherheit und Backup & Recovery. In einem kurzen unverbindlichen Austausch klären wir zunächst, welche Themen für Sie tatsächlich relevant sind, und schlagen anschließend einen klar abgegrenzten passenden Umfang vor.

Wenn das für Sie interessant ist, können wir uns gerne einmal 15 Minuten unverbindlich austauschen.

{signature}
""",
    "it_service_provider": """Guten Tag,

ich wende mich an {company}, weil IT-Sicherheit für kleine und mittlere Unternehmen zunehmend ein operatives Thema ist.

{intro}

Als Systemhaus oder Managed-Service-Provider kann bei einzelnen Kundenprojekten zusätzliche Kapazität für Security Assessments, Penetrationstests oder technische Reviews sinnvoll sein.

Digital Skills Campus unterstützt dabei projektbezogen und klar abgegrenzt, auf Wunsch auch als ergänzender Partner im Hintergrund. Umfang und Zusammenarbeit stimmen wir individuell auf das jeweilige Kundenprojekt ab.

Wäre ein kurzer unverbindlicher Austausch zu einer möglichen Zusammenarbeit interessant?

{signature}
""",
    "manufacturing_industry": """Guten Tag,

ich wende mich an {company}, weil IT-Sicherheit für kleine und mittlere Unternehmen zunehmend ein operatives Thema ist.

{intro}

In Fertigungs- und Produktionsumgebungen sind stabile Systeme, verlässliche Datensicherung und sauber abgesicherte Zugänge besonders wichtig, weil IT-Ausfälle schnell operative Auswirkungen haben.

Wir unterstützen kleine und mittlere Unternehmen individuell bei Windows-Sicherheit, Backup & Recovery sowie klar abgegrenzten technischen Security Assessments. In einem kurzen unverbindlichen Austausch schauen wir zunächst, welche Themen bei Ihnen tatsächlich relevant sind, und schlagen anschließend einen passenden Umfang vor.

Wenn das Thema für Sie grundsätzlich interessant ist, können wir uns gerne einmal 15 Minuten unverbindlich austauschen.

{signature}
""",
    "electrical_engineering": """Guten Tag,

ich wende mich an {company}, weil IT-Sicherheit für kleine und mittlere Unternehmen zunehmend ein operatives Thema ist.

{intro}

Bei technisch geprägten Betrieben treffen Büro-IT, mobile Geräte, Projektunterlagen und häufig externe Zugänge aufeinander. Schon kleine Fehlkonfigurationen können dabei unnötige Risiken erzeugen.

Wir unterstützen kleine und mittlere Unternehmen individuell bei Windows-Sicherheit, Microsoft-365-Sicherheit und Backup & Recovery. In einem kurzen unverbindlichen Austausch klären wir zunächst den tatsächlichen Bedarf und schlagen danach einen klar abgegrenzten passenden Umfang vor.

Wenn das Thema für Sie grundsätzlich interessant ist, können wir uns gerne einmal 15 Minuten unverbindlich austauschen.

{signature}
""",
    "logistics": """Guten Tag,

ich wende mich an {company}, weil IT-Sicherheit für kleine und mittlere Unternehmen zunehmend ein operatives Thema ist.

{intro}

In Transport und Logistik hängen Disposition, Kommunikation und operative Abläufe stark von verfügbarer IT ab. Belastbare Backups und sauber konfigurierte Systeme sind deshalb besonders wichtig.

Wir unterstützen kleine und mittlere Unternehmen individuell mit IT-Sicherheitsassessments, Windows-Sicherheitsprüfungen und Backup-&-Recovery-Reviews. In einem kurzen unverbindlichen Austausch schauen wir zunächst, welche Themen bei Ihnen tatsächlich relevant sind, und schlagen anschließend einen klar abgegrenzten passenden Umfang vor.

Wenn das Thema für Sie grundsätzlich interessant ist, können wir uns gerne einmal 15 Minuten unverbindlich austauschen.

{signature}
""",
    "property_management": """Guten Tag,

ich wende mich an {company}, weil IT-Sicherheit für kleine und mittlere Unternehmen zunehmend ein operatives Thema ist.

{intro}

In der Immobilienverwaltung werden viele sensible Dokumente, E-Mails, Zugänge und personenbezogene Daten verarbeitet. Eine saubere Absicherung von Microsoft 365, Windows und Datensicherung ist deshalb besonders relevant.

Wir unterstützen kleine und mittlere Unternehmen individuell bei Microsoft-365-Sicherheit, Windows-Sicherheit und Backup & Recovery. In einem kurzen unverbindlichen Austausch klären wir zunächst den tatsächlichen Bedarf und schlagen danach einen passenden klar abgegrenzten Umfang vor.

Wenn das Thema für Sie grundsätzlich interessant ist, können wir uns gerne einmal 15 Minuten unverbindlich austauschen.

{signature}
""",
    "technical_trade": """Guten Tag,

ich wende mich an {company}, weil IT-Sicherheit für kleine und mittlere Unternehmen zunehmend ein operatives Thema ist.

{intro}

Bei technischem Handel und Industriebedarf greifen Warenwirtschaft, E-Mail, E-Commerce und interne Arbeitsplätze oft eng ineinander. Eine pragmatische Sicherheitsprüfung kann hier schnell konkrete Verbesserungen sichtbar machen.

Wir unterstützen kleine und mittlere Unternehmen individuell bei Windows- und Microsoft-365-Sicherheit, Backup & Recovery sowie technischen Security Assessments. In einem kurzen unverbindlichen Austausch schauen wir zunächst, welche Themen bei Ihnen tatsächlich relevant sind, und schlagen anschließend einen passenden Umfang vor.

Wenn das Thema für Sie grundsätzlich interessant ist, können wir uns gerne einmal 15 Minuten unverbindlich austauschen.

{signature}
""",
    "fitness_studio": """Guten Tag,

ich wende mich an {company}, weil IT-Sicherheit auch für Fitness- und Gesundheitsstudios zunehmend ein operatives Thema ist.

{intro}

Fitnessstudios arbeiten heute häufig mit digitaler Mitgliederverwaltung, Online-Buchungen, Zutrittslösungen, Apps, Zahlungsdaten, E-Mail und vernetzten Arbeitsplätzen. Verfügbarkeit und sauber abgesicherte Zugänge sind deshalb sowohl für den laufenden Studiobetrieb als auch für den Schutz von Mitgliederdaten wichtig.

Wir unterstützen Fitness- und Gesundheitsstudios individuell bei Windows- und Microsoft-365-Sicherheit, Backup & Recovery sowie klar abgegrenzten technischen Security Assessments. In einem kurzen unverbindlichen Austausch schauen wir zunächst, welche Systeme und Themen bei Ihnen tatsächlich relevant sind, und schlagen anschließend einen passenden Umfang vor.

Wenn das Thema für Sie grundsätzlich interessant ist, können wir uns gerne einmal 15 Minuten unverbindlich austauschen.

{signature}
""",
}


def render(sector: str, company: str, region: str = "") -> tuple[str, str]:
    if sector == "tax_advisory":
        sector = "tax_advisor"
    if (
        sector not in SECTORS
        or not company.strip()
        or any(c in company for c in "\r\n")
        or any(c in region for c in "\r\n")
    ):
        raise ValueError("Invalid sector, company or region")
    subject = f"Kurzer Austausch zur IT-Sicherheit bei {company}"
    if sector == "it_service_provider":
        subject = f"Security-Assessment-Partnerschaft für {company}"
    return subject, SECTORS[sector].format(
        company=company,
        region_label=f"{region.strip()} und Umgebung" if region.strip() else "Ihrer Region",
        intro=INTRO,
        signature=SIGNATURE,
    )

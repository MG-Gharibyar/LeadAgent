from __future__ import annotations

LAW_BODY = "Liebes Team von {company},\n\nheute möchte ich die Gelegenheit nutzen, mich Ihnen kurz vorzustellen. Mein Name ist Hasib Gharibyar. Ich bin studierter Informatiker, Gründer von Digital Skills Campus und seit mehreren Jahren im Bereich IT-Sicherheit tätig.\n\nMit meiner Selbstständigkeit möchte ich insbesondere Kanzleien bei der Absicherung ihrer IT unterstützen. Gerade dort werden täglich sensible Mandatsdaten verarbeitet, weshalb bereits einzelne Fehlkonfigurationen bei E-Mail, Windows-Systemen oder extern erreichbaren Diensten erhebliche Folgen haben können.\n\nDa ich Digital Skills Campus derzeit weiter aufbaue und mir einen Kundenstamm sowie Referenzen aufbauen möchte, biete ich ausgewählten Kanzleien in {region_label} aktuell folgende Leistungen zu vergünstigten Einführungskonditionen an:\n\n🔐 Penetrationstest – 1.100 € Einführungspreis\n\nGezielte Prüfung einer klar abgegrenzten Webanwendung oder extern erreichbaren Infrastruktur – inklusive Schwachstellenbericht, Risikobewertung und konkreten Handlungsempfehlungen.\n\n🖥️ Windows Security Assessment – 690 €\n\nPrüfung zentraler Windows-Sicherheitskonfigurationen und typischer Fehlkonfigurationen – inklusive priorisiertem Maßnahmenbericht.\n\nFalls eines der Angebote für Sie interessant ist, sende ich Ihnen gerne unverbindlich weitere Informationen zu Umfang und Ablauf.\n\nDarüber hinaus biete ich bei Interesse auch eine langfristige Zusammenarbeit im Bereich Managed IT & Security sowie praxisnahe IT-Sicherheitsschulungen für Mitarbeitende an.\n\nMit freundlichen Grüßen\n\nHasib Gharibyar, M.Sc.\nDigital Skills Campus\nE-Mail: kontakt@digitalskills-campus.de\nWeb: https://digitalskills-campus.de\nTel.: 01575 5440113\n"

INTRO = "Mein Name ist Hasib Gharibyar, Gründer von Digital Skills Campus und seit mehreren Jahren im Bereich IT-Sicherheit tätig."
SIGNATURE = "Mit freundlichen Grüßen\n\nHasib Gharibyar, M.Sc.\nDigital Skills Campus\nE-Mail: kontakt@digitalskills-campus.de\nWeb: https://digitalskills-campus.de"
SECTORS = {
    "law_firm": LAW_BODY,
    "medical_practice": """Liebes Team von {company},

"""
    + INTRO
    + """

Arzt- und Zahnarztpraxen, MVZ und Psychotherapiepraxen verarbeiten sensible Patientendaten. Windows-Arbeitsplätze, E-Mail und externe Systeme sollten sicher funktionieren; verlässliche Wiederherstellung hilft, Ausfälle und die Folgen von Ransomware zu begrenzen.

Windows Security Assessment – 690 €: Prüfung zentraler Windows-Sicherheitskonfigurationen mit priorisiertem Maßnahmenbericht.

Backup & Recovery Review: Prüfung der Sicherungs- und Wiederherstellungsabläufe, inklusive konkreter Verbesserungen. Umfang und Preis stimmen wir vorab ab.

Bei Bedarf ergänzen wir dies durch einen klar abgegrenzten Penetrationstest, Managed IT & Security oder praktische Sicherheitsschulungen.

Gerne sende ich Ihnen unverbindlich Informationen zu Umfang und Ablauf.

"""
    + SIGNATURE,
    "tax_advisor": """Liebes Team von {company},

"""
    + INTRO
    + """

In der Steuerberatung und Lohnbuchhaltung werden sensible Finanz- und Mandantendaten verarbeitet. Windows, E-Mail, Microsoft 365 und digitaler Dokumentenaustausch erfordern sichere Identitäten und Zugriffe sowie verlässliche Backups.

Windows Security Assessment – 690 €: Prüfung zentraler Windows-Sicherheitskonfigurationen mit priorisiertem Maßnahmenbericht.

Microsoft 365 Security Assessment: Prüfung der Identitäts-, Zugriffs- und Mandanteneinstellungen. Alternativ prüfen wir extern erreichbare Dienste mit einem klar abgegrenzten Penetrationstest. Umfang und Preis stimmen wir vorab ab.

Optional unterstütze ich Sie langfristig mit Managed IT & Security und praktischen Schulungen für Mitarbeitende.

Gerne sende ich Ihnen unverbindlich Informationen zu Umfang und Ablauf.

"""
    + SIGNATURE,
    "it_service_provider": """Liebes Team von {company},

"""
    + INTRO
    + """

Als Systemhaus betreuen Sie möglicherweise Windows-, Microsoft-365- und Backup-Umgebungen Ihrer Kunden. Falls für einzelne Projekte zusätzliche Security-Assessment- oder Penetrationstest-Kapazität gefragt ist, möchte ich DSC als spezialisierten Partner vorstellen.

White-Label-Penetrationstests: klar abgegrenzte Prüfungen für Ihre SMB-Kunden mit Schwachstellenbericht und priorisierten Maßnahmen.

Windows Security Assessment: ergänzende Prüfung von Windows-Konfigurationen und Zugriffssicherheit für Ihre Kundenprojekte.

Auch Security Reviews und eine langfristige Zusammenarbeit sind möglich. Umfang, Zusammenarbeit und Konditionen stimmen wir projektbezogen ab.

Wäre eine kurze Abstimmung zu einer möglichen Kooperation für Sie interessant?

"""
    + SIGNATURE,
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
    region_label = f"{region.strip()} und Umgebung" if region.strip() else "Ihrer Region"
    subject = f"IT-Sicherheitsangebote für {company}"
    if sector == "it_service_provider":
        subject = f"Security-Assessment-Partnerschaft für {company}"
    return subject, SECTORS[sector].format(company=company, region_label=region_label)

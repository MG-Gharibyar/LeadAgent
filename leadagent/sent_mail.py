"""Archive immutable SMTP messages through IMAP; never invoke SMTP during repair."""

from __future__ import annotations

import base64
import getpass
import imaplib
import os
import re
import ssl
import sys
from collections.abc import Callable
from contextlib import suppress
from email import policy
from email.parser import BytesParser
from typing import Any

from .database import Database
from .models import utcnow

IMAP_HOST = "mxe9aa.netcup.net"
IMAP_PORT = 993
IMAP_USER = "kontakt@digitalskills-campus.de"


def mailbox_password() -> str:
    password = os.environ.get("DSC_SMTP_PASSWORD", "")
    if not password:
        if not sys.stdin.isatty():
            raise ValueError("Set DSC_SMTP_PASSWORD securely for IMAP mailbox access")
        password = getpass.getpass(f"Passwort für {IMAP_USER}: ")
    if not password:
        raise ValueError("Mailbox password required")
    return password


def quote(value: str) -> str:
    if any(c in value for c in "\r\n\x00"):
        raise ValueError("Invalid IMAP string")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def discover_sent_folder(client: imaplib.IMAP4_SSL) -> str:
    pattern = (
        "* RETURN (SPECIAL-USE)"
        if {b"LIST-EXTENDED", b"SPECIAL-USE"} <= set(client.capabilities)
        else "*"
    )
    status, rows = client.list('""', pattern)
    if status != "OK":
        raise ValueError("IMAP LIST failed")
    folders: set[str] = set()
    for row in rows or []:
        literal = None
        if isinstance(row, tuple):
            header, literal = row
        elif isinstance(row, bytes):
            header = row
        else:
            continue
        match = re.fullmatch(rb'\(([^)]*)\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(.+)', header)
        if not match:
            continue
        flags = {flag.lower() for flag in match[1].split()}
        if b"\\sent" not in flags or b"\\noselect" in flags:
            continue
        name = literal if literal is not None else match[2]
        if not isinstance(name, bytes):
            raise ValueError("Invalid IMAP mailbox response")
        if name.startswith(b'"') and name.endswith(b'"'):
            name = re.sub(rb"\\(.)", rb"\1", name[1:-1])
        folders.add(name.decode("ascii"))
    if len(folders) != 1:
        raise ValueError(
            "Expected exactly one selectable IMAP \\Sent mailbox; no folder name guessed"
        )
    return folders.pop()


def display_folder(folder: str) -> str:
    # IMAP4rev1 LIST names use modified UTF-7; retain wire names for commands.
    def decode(match: re.Match[str]) -> str:
        value = match[1]
        if not value:
            return "&"
        encoded = value.replace(",", "/")
        return base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-16-be")

    return re.sub(r"&([^-]*)-", decode, folder)


def connect(password: str) -> imaplib.IMAP4_SSL:
    client = imaplib.IMAP4_SSL(
        IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context(), timeout=30
    )
    try:
        status, _ = client.login(IMAP_USER, password)
        if status != "OK":
            raise ValueError("IMAP authentication failed")
        return client
    except BaseException:
        client.shutdown()
        raise


def disconnect(client: imaplib.IMAP4_SSL) -> None:
    # A logout failure after APPEND success must not obscure the success boundary.
    with suppress(OSError, imaplib.IMAP4.error):
        client.logout()


def detect_sent(password: str) -> str:
    client = connect(password)
    try:
        return display_folder(discover_sent_folder(client))
    finally:
        disconnect(client)


def append_sent(password: str, rfc822: bytes, message_id: str) -> str:
    headers = BytesParser(policy=policy.default).parsebytes(rfc822, headersonly=True)
    if not message_id or str(headers["Message-ID"]) != message_id or not headers["Date"]:
        raise ValueError("Immutable message requires matching Message-ID and Date")
    client = connect(password)
    try:
        folder = discover_sent_folder(client)
        status, _ = client.select(quote(folder), readonly=True)
        if status != "OK":
            raise ValueError("Cannot examine IMAP Sent mailbox")
        status, data = client.uid("SEARCH", "HEADER", "Message-ID", quote(message_id))
        if status != "OK" or not data or not isinstance(data[0], bytes):
            raise ValueError("Cannot check Sent mailbox for duplicate Message-ID")
        for uid in data[0].split():
            # HEADER search is substring-based: verify exact identity before skipping APPEND.
            status, fetched = client.uid(
                "FETCH", uid.decode("ascii"), "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])"
            )
            if status != "OK":
                raise ValueError("Cannot verify existing Sent copy")
            verified_header = False
            for item in fetched or []:
                if isinstance(item, tuple) and isinstance(item[1], bytes):
                    existing = BytesParser(policy=policy.default).parsebytes(item[1])
                    verified_header = bool(existing["Message-ID"])
                    if str(existing["Message-ID"]) == message_id:
                        return display_folder(folder)
            if not verified_header:
                raise ValueError("Cannot verify existing Sent-copy Message-ID; no blind APPEND")
        status, _ = client.append(quote(folder), r"\Seen", None, rfc822)
        if status != "OK":
            raise ValueError("IMAP APPEND failed")
        return display_folder(folder)
    finally:
        disconnect(client)


SentCopyTransport = Callable[[bytes, str], str]


def save_sent_copy(db: Database, delivery_id: int, transport: SentCopyTransport) -> str:
    # Serialize repairs and initial copies. After a crash/uncertain APPEND, search the
    # immutable Message-ID first; never blindly append a second copy or invoke SMTP.
    with db.transaction():
        row = db.connection.execute(
            "SELECT * FROM deliveries WHERE id=?", (delivery_id,)
        ).fetchone()
        if row is None or row["mode"] != "LIVE" or row["state"] != "ACCEPTED":
            raise ValueError("Only SMTP-accepted deliveries support Sent-copy repair")
        if row["sent_copy_status"] == "SAVED":
            return str(row["sent_folder"])
        if not row["rfc822"]:
            raise ValueError(
                "Exact RFC822 unavailable for legacy delivery; cannot reconstruct or resend"
            )
        try:
            folder = transport(bytes(row["rfc822"]), str(row["message_id"]))
            if not folder:
                raise ValueError("Sent-copy transport did not report a folder")
        except Exception as exc:
            db.connection.execute(
                "UPDATE deliveries SET sent_copy_status='FAILED',sent_copy_error=? WHERE id=?",
                (type(exc).__name__, delivery_id),
            )
            db.audit(row["lead_id"], "SENT_COPY_FAILED", "system", type(exc).__name__)
            folder = ""
        else:
            db.connection.execute(
                "UPDATE deliveries SET sent_copy_status='SAVED',sent_folder=?,sent_copied_at=?,sent_copy_error='' WHERE id=?",
                (folder, utcnow(), delivery_id),
            )
            db.audit(row["lead_id"], "SENT_COPY_SAVED", "system", str(delivery_id))
    if not folder:
        print(
            f"WARNUNG: SMTP erfolgreich, IMAP-Sent-Kopie für Zustellung {delivery_id} fehlgeschlagen. Kein erneuter Versand. Reparatur: python -m leadagent outreach repair-sent",
            file=sys.stderr,
        )
    return folder


def repair_sent(db: Database, transport: SentCopyTransport) -> list[dict[str, Any]]:
    rows = db.connection.execute(
        "SELECT id FROM deliveries WHERE mode='LIVE' AND state='ACCEPTED' AND sent_copy_status IN ('PENDING','FAILED') ORDER BY id"
    ).fetchall()
    results = []
    for row in rows:
        folder = save_sent_copy(db, int(row["id"]), transport)
        results.append(
            {
                "delivery_id": row["id"],
                "sent_copy_status": "SAVED" if folder else "FAILED",
                "sent_folder": folder,
            }
        )
    return results

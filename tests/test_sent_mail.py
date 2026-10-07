from email import policy
from unittest.mock import MagicMock, patch

import pytest

from leadagent.mail import Mailer, SMTPSettings, build_message, smtp_transport
from leadagent.outreach import approve, set_permission
from leadagent.sent_mail import (
    append_sent,
    detect_sent,
    discover_sent_folder,
    repair_sent,
    save_sent_copy,
)


def ready(db, config, lead):
    config.mail.automatic_sending_enabled = True
    set_permission(db, lead.id, "CONSENTED", "Synthetic written request", "reviewer")
    approve(db, lead.id, "reviewer", config)


def server(folder=b"Sent Messages"):
    client = MagicMock()
    client.list.return_value = (
        "OK",
        [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Sent) "/" "' + folder + b'"'],
    )
    client.select.return_value = ("OK", [b"0"])
    client.uid.return_value = ("OK", [b""])
    client.append.return_value = ("OK", [b"APPENDUID 1 2"])
    return client


@pytest.mark.parametrize("folder", [b"Sent", b"Gesendet", b"Sent Messages", b"INBOX.Sent"])
def test_special_use_detection(folder):
    client = server(folder)
    assert discover_sent_folder(client) == folder.decode()
    with patch("leadagent.sent_mail.connect", return_value=client):
        assert detect_sent("synthetic") == folder.decode()
    client.append.assert_not_called()


def test_literal_folder_and_quoted_name():
    client = server()
    client.list.return_value = ("OK", [(b'(\\Sent) "/" {13}', b"Sent Messages")])
    assert discover_sent_folder(client) == "Sent Messages"
    client.list.return_value = ("OK", [b'(\\Sent) NIL "Sent \\"Archive\\""'])
    assert discover_sent_folder(client) == 'Sent "Archive"'


@pytest.mark.parametrize(
    "rows",
    [
        [b'(\\HasNoChildren) "/" "Sent"'],
        [b'(\\Sent \\Noselect) "/" "Gesendet"'],
        [b'(\\Sent) "/" "One"', b'(\\Sent) "/" "Two"'],
    ],
)
def test_no_name_guessing(rows):
    client = server()
    client.list.return_value = ("OK", rows)
    with pytest.raises(ValueError, match="exactly one"):
        discover_sent_folder(client)


def test_append_exact_message_and_date(qualified):
    message = build_message(qualified)
    raw = message.as_bytes(policy=policy.SMTP)
    client = server(b"Gesendet")
    with patch("leadagent.sent_mail.connect", return_value=client):
        assert append_sent("synthetic", raw, str(message["Message-ID"])) == "Gesendet"
    client.append.assert_called_once_with('"Gesendet"', r"\Seen", None, raw)
    client.select.assert_called_once_with('"Gesendet"', readonly=True)
    assert b"Date: " in raw and b"\r\n" in raw


def test_existing_exact_id_is_not_appended(qualified):
    message = build_message(qualified)
    raw = message.as_bytes(policy=policy.SMTP)
    client = server()
    client.uid.side_effect = [
        ("OK", [b"42"]),
        ("OK", [(b"42", f"Message-ID: {message['Message-ID']}\r\n\r\n".encode())]),
    ]
    with patch("leadagent.sent_mail.connect", return_value=client):
        assert append_sent("synthetic", raw, str(message["Message-ID"])) == "Sent Messages"
    client.append.assert_not_called()


def test_substring_id_does_not_suppress_exact_copy(qualified):
    message = build_message(qualified)
    client = server()
    client.uid.side_effect = [
        ("OK", [b"42"]),
        ("OK", [(b"42", b"Message-ID: <other@synthetic.example>\r\n\r\n")]),
    ]
    with patch("leadagent.sent_mail.connect", return_value=client):
        append_sent("synthetic", message.as_bytes(policy=policy.SMTP), str(message["Message-ID"]))
    client.append.assert_called_once()


@pytest.mark.parametrize("failure", [False, True])
def test_smtp_success_imap_outcome_and_repair(db, config, qualified, failure, capsys):
    ready(db, config, qualified)
    smtp = MagicMock()
    observed = []

    def copy(raw, message_id):
        # SMTP acceptance is already durable before IMAP starts.
        assert db.history(qualified.id)[0]["state"] == "ACCEPTED"
        assert db.get(qualified.id).initial_delivery_confirmed
        observed.append(raw)
        if failure:
            raise OSError("Private IMAP response")
        return "Gesendet"

    result = Mailer(db, config, smtp, copy).send(qualified.id, live=True)
    assert result.state == "ACCEPTED"
    assert result.sent_copy_status == ("FAILED" if failure else "SAVED")
    row = db.connection.execute("SELECT * FROM deliveries").fetchone()
    assert row["rfc822"] == observed[0] == smtp.call_args.args[0].as_bytes(policy=policy.SMTP)
    assert "Private" not in str(db.history(qualified.id))
    if failure:
        assert "repair-sent" in capsys.readouterr().err
    repair = MagicMock(return_value="Gesendet")
    assert len(repair_sent(db, repair)) == (1 if failure else 0)
    assert db.history(qualified.id)[0]["sent_copy_status"] == "SAVED"
    assert repair_sent(db, repair) == []
    smtp.assert_called_once()
    with pytest.raises(ValueError, match="already"):
        Mailer(db, config, smtp, repair).send(qualified.id, live=True)


def test_smtp_failure_never_attempts_imap(db, config, qualified):
    ready(db, config, qualified)
    copy = MagicMock(return_value="Gesendet")
    result = Mailer(db, config, MagicMock(side_effect=OSError("synthetic failure")), copy).send(
        qualified.id, live=True
    )
    assert result.state == "FAILED_OR_UNCERTAIN"
    copy.assert_not_called()
    assert repair_sent(db, copy) == []


def test_uncertain_append_repair_searches_before_append(db, config, qualified):
    ready(db, config, qualified)
    client = server()
    copies = {}

    def uid(command, *args):
        if command == "SEARCH":
            return "OK", [b"1" if copies else b""]
        raw = next(iter(copies.values()))
        return "OK", [(b"1", raw)]

    def append(folder, flags, date, raw):
        copies[1] = raw
        raise OSError("Disconnected after server stored the message")

    client.uid.side_effect = uid
    client.append.side_effect = append
    with patch("leadagent.sent_mail.connect", return_value=client):

        def transport(raw, mid):
            return append_sent("synthetic", raw, mid)

        result = Mailer(db, config, lambda message: None, transport).send(qualified.id, True)
        assert result.sent_copy_status == "FAILED"
        assert repair_sent(db, transport)[0]["sent_copy_status"] == "SAVED"
    client.append.assert_called_once()
    assert len(copies) == 1


def test_concurrent_repairs_serialize_and_skip_saved(db, config, qualified):
    ready(db, config, qualified)
    copy = MagicMock(return_value="Gesendet")
    result = Mailer(db, config, lambda message: None, copy).send(qualified.id, True)
    assert save_sent_copy(db, result.delivery_id, copy) == "Gesendet"
    copy.assert_called_once()


def test_smtp_uses_persisted_wire_bytes(qualified):
    settings = SMTPSettings(
        "smtp.synthetic.example",
        465,
        "synthetic",
        "synthetic",
        "ssl",
        "kontakt@digitalskills-campus.de",
        "Hasib",
    )
    message = build_message(qualified, settings)
    client = MagicMock()
    client.sendmail.return_value = {}
    with patch("leadagent.mail.smtplib.SMTP_SSL", return_value=client):
        smtp_transport(settings, message)
    assert client.sendmail.call_args.args[2] == message.as_bytes(policy=policy.SMTP)


def test_repair_cli_never_uses_smtp(db, config, qualified, tmp_path, monkeypatch):
    from argparse import Namespace

    from leadagent.batch_outreach import dispatch

    monkeypatch.chdir(tmp_path)
    ready(db, config, qualified)
    Mailer(db, config, lambda message: None, MagicMock(side_effect=OSError())).send(
        qualified.id, True
    )
    monkeypatch.setenv("DSC_SMTP_PASSWORD", "synthetic")
    with (
        patch("leadagent.batch_outreach.append_sent", return_value="Gesendet"),
        patch("leadagent.mail.smtp_transport") as smtp,
    ):
        assert dispatch(Namespace(outreach_command="repair-sent"), db, config) == 0
        smtp.assert_not_called()
    assert db.history(qualified.id)[0]["sent_copy_status"] == "SAVED"


def test_saved_copy_not_duplicated_after_restart(db, config, qualified):
    from leadagent.database import Database

    ready(db, config, qualified)
    copy = MagicMock(return_value="Gesendet")
    result = Mailer(db, config, lambda message: None, copy).send(qualified.id, True)
    reopened = Database(config.database)
    assert save_sent_copy(reopened, result.delivery_id, copy) == "Gesendet"
    copy.assert_called_once()
    reopened.close()


def test_explicit_special_use_list_extension():
    client = server(b"INBOX.Gesendet")
    client.capabilities = (b"IMAP4rev1", b"LIST-EXTENDED", b"SPECIAL-USE")
    assert discover_sent_folder(client) == "INBOX.Gesendet"
    client.list.assert_called_once_with('""', "* RETURN (SPECIAL-USE)")


def test_malformed_duplicate_search_fetch_fails_closed(qualified):
    message = build_message(qualified)
    client = server()
    client.uid.side_effect = [("OK", [b"1"]), ("OK", [None])]
    with (
        patch("leadagent.sent_mail.connect", return_value=client),
        pytest.raises(ValueError, match="no blind APPEND"),
    ):
        append_sent("synthetic", message.as_bytes(policy=policy.SMTP), str(message["Message-ID"]))
    client.append.assert_not_called()


def test_two_concurrent_repairs_append_only_once(db, config, qualified):
    from concurrent.futures import ThreadPoolExecutor

    from leadagent.database import Database

    ready(db, config, qualified)
    result = Mailer(db, config, lambda message: None, MagicMock(side_effect=OSError())).send(
        qualified.id, True
    )
    copy = MagicMock(return_value="Gesendet")

    def repair(_):
        local = Database(config.database)
        try:
            return save_sent_copy(local, result.delivery_id, copy)
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert list(workers.map(repair, range(2))) == ["Gesendet", "Gesendet"]
    copy.assert_called_once()

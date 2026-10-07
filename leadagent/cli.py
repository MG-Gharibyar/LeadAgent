from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import asdict
from datetime import UTC, datetime

from .config import Config, load_config
from .database import Database
from .logging import configure_logging
from .mail import Mailer, blockers, contacted
from .models import Permission, Status
from .outreach import approve, followup_due, prepare_draft, set_permission, set_status
from .pipeline import discover
from .providers import provider_from_config
from .report import generate_report
from .research import Researcher
from .web import PublicWebClient


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="DSC evidence-based prospect research and gated outreach"
    )
    root.add_argument("--config", default="config.yaml")
    root.add_argument(
        "--database", help="Override SQLite path (use an isolated path for demonstrations)"
    )
    sub = root.add_subparsers(dest="command", required=True)
    discovery = sub.add_parser(
        "discover", help="Discover, research, rank, draft and generate daily report"
    )
    discovery.add_argument("--provider", choices=["seeds", "brave", "fixture"])
    discovery.add_argument("--input", help="Seed/fixture JSON path")
    report = sub.add_parser("report")
    report.add_argument("--date")
    sub.add_parser("leads")
    sub.add_parser("stats")
    show = sub.add_parser("show")
    show.add_argument("lead_id", type=int)
    for name in (
        "approve",
        "reject",
        "block",
        "unsubscribe",
        "responded",
        "interested",
        "customer",
    ):
        command = sub.add_parser(name)
        command.add_argument("lead_id", type=int)
        command.add_argument("--actor", required=True, help="Named human operator")
        command.add_argument("--reason", default="")
    permission = sub.add_parser(
        "permission", help="Record an actual documented email permission basis"
    )
    permission.add_argument("lead_id", type=int)
    permission.add_argument("state", choices=[p.value for p in Permission])
    permission.add_argument("--basis", required=True)
    permission.add_argument("--actor", required=True)
    manual = sub.add_parser(
        "contacted", help="Record an initial contact already made outside this system"
    )
    manual.add_argument("lead_id", type=int)
    manual.add_argument("--actor", required=True)
    manual.add_argument("--basis", required=True)
    followups = sub.add_parser("followups", help="List eligible follow-ups; never sends")
    followups.add_argument(
        "--prepare", action="store_true", help="Create a fresh draft requiring separate approval"
    )
    mail = sub.add_parser("mail")
    mail_sub = mail.add_subparsers(dest="mail_command", required=True)
    for name in ("preview", "approve", "send"):
        command = mail_sub.add_parser(name)
        command.add_argument("lead_id", type=int)
        if name == "approve":
            command.add_argument("--actor", required=True)
        if name == "send":
            command.add_argument(
                "--live",
                action="store_true",
                help="Requires config sending enabled and all approval/permission gates",
            )
    batch = mail_sub.add_parser("send-approved")
    batch.add_argument("--live", action="store_true")
    return root


def dispatch(args: argparse.Namespace, db: Database, config: Config) -> int:
    command = args.command
    if command == "discover":
        if args.provider:
            config.discovery.provider = args.provider
        if args.input:
            config.discovery.seeds_file = args.input
        config.validate()
        run_result = discover(
            db,
            config,
            provider_from_config(config.discovery),
            Researcher(PublicWebClient(config.discovery)),
        )
        paths = generate_report(db, config)
        print(json.dumps({**asdict(run_result), "reports": [str(path) for path in paths]}))
    elif command == "report":
        for path in generate_report(db, config, args.date):
            print(path)
    elif command == "leads":
        for lead in db.all():
            print(
                f"{lead.id}\t{lead.final_score}\t{lead.segment}\t{lead.outreach_status}\t{lead.company_name}"
            )
    elif command == "show":
        lead = db.get(args.lead_id)
        print(
            json.dumps(
                {**lead.to_dict(), "delivery_history": db.history(args.lead_id)},
                ensure_ascii=False,
                indent=2,
            )
        )
    elif command == "stats":
        leads = db.all()
        today = datetime.now(UTC).date().isoformat()
        print(
            json.dumps(
                {
                    "total": len(leads),
                    "qualified_today": sum(lead.qualified_at.startswith(today) for lead in leads),
                    "suppressed": sum(lead.suppressed for lead in leads),
                    "contacted": sum(lead.contact_count > 0 for lead in leads),
                    "followups_due": sum(followup_due(lead, config) for lead in leads),
                }
            )
        )
    elif command == "permission":
        set_permission(db, args.lead_id, args.state, args.basis, args.actor)
        print("Permission basis recorded; any previous approval invalidated.")
    elif command in {"reject", "block", "unsubscribe", "responded", "interested", "customer"}:
        statuses = {
            "reject": Status.REJECTED,
            "block": Status.DO_NOT_CONTACT,
            "unsubscribe": Status.DO_NOT_CONTACT,
            "responded": Status.RESPONDED,
            "interested": Status.INTERESTED,
            "customer": Status.CUSTOMER,
        }
        set_status(db, args.lead_id, statuses[command], args.actor, args.reason)
        print(statuses[command].value)
    elif command == "contacted":
        contacted(db, args.lead_id, config, args.actor, args.basis)
        print("External initial contact recorded; initial outreach slot consumed.")
    elif command == "approve" or (command == "mail" and args.mail_command == "approve"):
        approve(db, args.lead_id, args.actor, config)
        print("Draft approved. Email permission and live-send policy are checked separately.")
    elif command == "followups":
        with db.transaction():
            for lead in db.all():
                if followup_due(lead, config):
                    if args.prepare and lead.draft_kind != "followup":
                        prepare_draft(lead, "followup")
                        db.save(lead)
                        db.audit(lead.id, "FOLLOWUP_DRAFTED", "operator")
                    print(f"{lead.id}\t{lead.company_name}\t{lead.next_contact_allowed_at}\tREVIEW")
    elif command == "mail":
        if args.mail_command == "preview":
            lead = db.get(args.lead_id)
            print(
                f"Subject: {lead.draft_subject}\n\n{lead.draft_text}\n\nGate: {'; '.join(blockers(lead, config)) or 'Clear'}"
            )
        elif args.mail_command == "send":
            result = Mailer(db, config).send(args.lead_id, args.live)
            print(json.dumps(asdict(result)))
            return 1 if result.state == "FAILED_OR_UNCERTAIN" else 0
        elif args.mail_command == "send-approved":
            failures = 0
            for lead in db.all():
                if not lead.approved_draft_hash:
                    continue
                try:
                    result = Mailer(db, config).send(lead.id or 0, args.live)
                    print(json.dumps({"lead_id": lead.id, **asdict(result)}))
                    failures += result.state == "FAILED_OR_UNCERTAIN"
                except ValueError as exc:
                    # No SMTP exception body is printed; policy error messages contain no secrets.
                    print(json.dumps({"lead_id": lead.id, "state": "BLOCKED", "reason": str(exc)}))
                    failures += 1
            return 1 if failures else 0
    return 0


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    configure_logging()
    args = parser().parse_args(argv)
    db = None
    try:
        config = load_config(args.config)
        db = Database(args.database or config.database)
        return dispatch(args, db, config)
    except (ValueError, OSError, sqlite3.Error) as exc:
        # File/API exceptions can contain private URLs or query data. Limit their details.
        detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(f"Error: {detail}", file=sys.stderr)
        return 1
    finally:
        if db:
            db.close()

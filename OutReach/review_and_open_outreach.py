#!/usr/bin/env python3
"""Compatibility entry point: all storage and sending use LeadAgent's shared engine."""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from leadagent.cli import main  # noqa: E402


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "json_file",
        nargs="?",
        default=str(Path(__file__).parent / "karlsruhe_kanzleien_outreach.json"),
    )
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--sector", default="law_firm")
    parser.add_argument("--actor", help="Required for live batch confirmation")
    parser.add_argument(
        "--config", default=str(Path(__file__).resolve().parents[1] / "config.yaml")
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Compatibility option; configured minimum interval remains authoritative",
    )
    args = parser.parse_args()
    if args.send and not args.actor:
        parser.error("--send requires --actor")
    args.json_file = str(Path(args.json_file).resolve())
    args.config = str(Path(args.config).resolve())
    os.chdir(Path(__file__).resolve().parents[1])
    argv = [
        "--config",
        args.config,
        "outreach",
        "send" if args.send else "preview",
        args.sector,
        "--input",
        args.json_file,
    ]
    if args.send:
        argv += ["--actor", args.actor]
    return main(argv)


if __name__ == "__main__":
    raise SystemExit(run())

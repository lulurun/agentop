"""`agentop email-code`: check the Email code plugin's settings by sending a test mail."""

from __future__ import annotations

import smtplib
import sys

from . import mail


def register_cli(cli) -> None:
    parser = cli.command(
        "email-code",
        lambda args: run(args, cli.data_dir),
        help="Check the Email code sign-in settings",
        description=f"Settings are in {mail.config_path(cli.data_dir)}. See the plugin's mail.py for the keys.",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("test", help="Send a test mail to the address codes go to")


def run(args, data_dir) -> None:
    path = mail.config_path(data_dir)
    try:
        config = mail.load(data_dir)
    except mail.ConfigError as exc:
        sys.exit(f"Not set up: {exc}")
    if config.password and mail.readable_by_others(path):
        print(f"Warning: {path} holds the SMTP password and others can read it. Run: chmod 600 {path}", file=sys.stderr)
    try:
        mail.send(config, "agentop: email code test", "This is a test from agentop. Sign-in codes will come to this address.\n")
    except (OSError, smtplib.SMTPException) as exc:
        sys.exit(f"Couldn't send through {config.host}:{config.port}: {type(exc).__name__}: {exc}")
    print(f"Sent a test mail to {config.to} through {config.host}:{config.port} ({config.security}).")

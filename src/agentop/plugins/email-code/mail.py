"""The Email code plugin's settings, and sending a mail with them.

Settings are in `~/.agentop/plugins/email-code/config.toml`, read on each send,
so a change needs no restart. It holds the SMTP password, so keep it mode 0600.

    to = "you@example.com"            # the one address codes go to
    smtp_host = "smtp.gmail.com"
    smtp_port = 587                   # 465: SSL. Otherwise STARTTLS
    smtp_user = "you@gmail.com"
    smtp_password = "app password"    # for Gmail, an app password
    # from = "agentop <you@gmail.com>"  # default: smtp_user, or else `to`
    # smtp_security = "starttls"        # "ssl", "starttls", or "none" (a mail server on this machine only)
"""

from __future__ import annotations

import ipaddress
import smtplib
import ssl
import stat
import tomllib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

SEND_TIMEOUT_SECONDS = 20
SECURITY = ("ssl", "starttls", "none")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class MailConfig:
    to: str
    sender: str
    host: str
    port: int
    security: str
    user: str | None
    password: str | None


def config_path(data_dir: Path) -> Path:
    return data_dir / "config.toml"


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def readable_by_others(path: Path) -> bool:
    try:
        return bool(path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO))
    except OSError:
        return False


def load(data_dir: Path) -> MailConfig:
    path = config_path(data_dir)
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"{path} doesn't exist") from None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path} can't be read: {exc}") from None

    def text(key: str, required: bool = False) -> str | None:
        value = data.get(key)
        if value is None or value == "":
            if required:
                raise ConfigError(f"{path}: '{key}' is not set")
            return None
        if not isinstance(value, str):
            raise ConfigError(f"{path}: '{key}' must be a string")
        return value.strip()

    to = text("to", required=True)
    if "@" not in to:
        raise ConfigError(f"{path}: 'to' is not an email address")
    host = text("smtp_host", required=True)
    port = data.get("smtp_port", 587)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        raise ConfigError(f"{path}: 'smtp_port' must be a port number")
    security = text("smtp_security") or ("ssl" if port == 465 else "starttls")
    if security not in SECURITY:
        raise ConfigError(f"{path}: 'smtp_security' must be one of {', '.join(SECURITY)}")
    if security == "none" and not _is_loopback(host):
        raise ConfigError(f"{path}: 'smtp_security' = \"none\" is only for a mail server on this machine, not {host}")
    user = text("smtp_user")
    password = text("smtp_password")
    if bool(user) != bool(password):
        raise ConfigError(f"{path}: set both 'smtp_user' and 'smtp_password', or neither")
    return MailConfig(
        to=to,
        sender=text("from") or user or to,
        host=host,
        port=port,
        security=security,
        user=user,
        password=password,
    )


def send(config: MailConfig, subject: str, body: str) -> None:
    """Send one mail to `config.to`. Raises OSError or smtplib.SMTPException if it can't."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = config.to
    message["Message-ID"] = make_msgid(domain="agentop")
    message.set_content(body)
    context = ssl.create_default_context()
    if config.security == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(config.host, config.port, timeout=SEND_TIMEOUT_SECONDS, context=context)
    else:
        server = smtplib.SMTP(config.host, config.port, timeout=SEND_TIMEOUT_SECONDS)
    with server:
        if config.security == "starttls":
            server.starttls(context=context)
        if config.user and config.password:
            server.login(config.user, config.password)
        server.send_message(message)


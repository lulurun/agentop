"""Email code: sign in with a code sent to one address set up on the service.

The sign-in screen asks for a code (`POST /send`, no token). The service mails
a 6-digit code to the address in config.toml and answers only that it was
sent, with a request ID. The browser sends both back (`POST /verify`), and gets
an ordinary client token through `api.sign_in`. Design section 7.4.

Off by default: it does nothing until config.toml is set up (see mail.py) and
the plugin is turned on in the service's Status tab.
"""

from __future__ import annotations

import smtplib

from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import mail
from .codes import CODE_TTL_SECONDS, Codes, TooSoon

NOT_SET_UP = "Email codes aren't set up on this service."


class SendBody(BaseModel):
    name: str = Field(default="Client", max_length=80)


class VerifyBody(BaseModel):
    request: str = Field(max_length=200)
    code: str = Field(max_length=20)
    name: str = Field(default="Client", min_length=1, max_length=80)


def message(code: str, name: str) -> tuple[str, str]:
    minutes = int(CODE_TTL_SECONDS // 60)
    subject = f"agentop sign-in code: {code}"
    body = (
        f"Your agentop sign-in code is {code}\n\n"
        f"It works for {minutes} minutes, once, in the browser that asked for it: {name}.\n\n"
        "If you didn't ask for it, someone who can reach your service's sign-in page did. "
        "They can't sign in without this code, so you can ignore this mail.\n"
    )
    return subject, body


def register(api) -> None:
    codes = Codes()
    path = mail.config_path(api.data_dir)
    try:
        if mail.load(api.data_dir).password and mail.readable_by_others(path):
            api.log.warning("%s holds the SMTP password and others can read it: chmod 600 it", path)
    except mail.ConfigError as exc:
        api.log.warning("Not set up, so no code can be sent: %s", exc)

    @api.public_router.post("/send")
    def send(body: SendBody):
        # A plain function: FastAPI runs it in a thread, so SMTP never blocks the event loop.
        try:
            config = mail.load(api.data_dir)
        except mail.ConfigError as exc:
            api.log.warning("A code was asked for, but: %s", exc)
            raise HTTPException(503, NOT_SET_UP) from None
        try:
            request, code = codes.issue()
        except TooSoon as exc:
            raise HTTPException(429, "A code was sent a moment ago. Wait a little before asking for another.", {"Retry-After": str(int(exc.retry_after) + 1)}) from None
        subject, text = message(code, body.name or "Client")
        try:
            mail.send(config, subject, text)
        except (OSError, smtplib.SMTPException) as exc:
            codes.cancel()
            api.log.warning("The code couldn't be sent: %s: %s", type(exc).__name__, exc)
            raise HTTPException(502, "The code couldn't be sent. The service's log says why.") from None
        api.log.info("Sent a sign-in code for %s", body.name)
        return {"request": request, "expires_in": int(CODE_TTL_SECONDS)}

    @api.public_router.post("/verify")
    def verify(body: VerifyBody):
        if not codes.check(body.request, body.code):
            raise HTTPException(401, "Wrong or expired code.")
        api.log.info("Signed in %s with an email code", body.name)
        return api.sign_in(body.name)

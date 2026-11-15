"""A tiny SMTP server for tests: takes every mail, without TLS or a login, and keeps it.

Run as a script for the e2e tests, `python tests/fake_smtp.py PORT OUT`, and each mail
is appended to OUT as one JSON line. In pytest, use FakeSmtp as a context manager.
"""

from __future__ import annotations

import json
import socket
import socketserver
import sys
import threading
from email import message_from_bytes, policy
from typing import Self


class _Handler(socketserver.StreamRequestHandler):
    def reply(self, line: str) -> None:
        self.wfile.write(f"{line}\r\n".encode())

    def handle(self) -> None:
        self.reply("220 fake ESMTP")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            verb = line.decode(errors="replace").strip().split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                self.reply("250 fake")
            elif verb == "DATA":
                self.reply("354 go on")
                data = b""
                while (chunk := self.rfile.readline()) not in (b".\r\n", b".\n", b""):
                    data += chunk[1:] if chunk.startswith(b"..") else chunk
                self.server.on_mail(message_from_bytes(data, policy=policy.default))
                self.reply("250 kept")
            elif verb == "QUIT":
                self.reply("221 bye")
                return
            else:
                self.reply("250 ok")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakeSmtp:
    def __init__(self, port: int = 0, on_mail=None):
        self.mails: list = []
        self._server = _Server(("127.0.0.1", port), _Handler)
        self._server.on_mail = on_mail or self.mails.append
        self.port = self._server.server_address[1]

    def __enter__(self) -> Self:
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._server.server_close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


if __name__ == "__main__":
    port, out = int(sys.argv[1]), sys.argv[2]

    def keep(message) -> None:
        with open(out, "a") as f:
            f.write(json.dumps({"to": message["To"], "subject": message["Subject"], "body": message.get_content()}) + "\n")

    server = FakeSmtp(port, keep)
    print("ready", flush=True)
    server._server.serve_forever()

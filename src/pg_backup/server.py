"""The k3s server, reached as root over ssh: kubectl there, with each argument quoted for the
server's shell, as ssh joins its arguments into one line that shell splits again."""

from __future__ import annotations

import shlex
import subprocess


class ServerError(Exception):
    def __init__(self, what: str, output: str = ""):
        super().__init__(what)
        self.output = output


class Server:
    def __init__(self, host: str):
        self.host = host

    def run(self, command: str, input: str | bytes | None = None, check: bool = True) -> str:
        """Runs one shell command line on the server and returns what it printed."""
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", f"root@{self.host}", command],
            input=input.encode() if isinstance(input, str) else input,
            capture_output=True,
        )
        out = result.stdout.decode(errors="replace")
        if check and result.returncode != 0:
            raise ServerError(f"on {self.host}: {command} failed ({result.returncode})", out + result.stderr.decode(errors="replace"))
        return out

    def kubectl(self, *args: str, input: str | None = None, check: bool = True) -> str:
        return self.run("k3s kubectl " + shlex.join(args), input=input, check=check)

    def ok(self, *args: str) -> bool:
        try:
            self.kubectl(*args)
        except ServerError:
            return False
        return True

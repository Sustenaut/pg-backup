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

    def pipe(self, source: tuple[str, ...], sink: tuple[str, ...]) -> None:
        """kubectl source's output into kubectl sink's input, each its own ssh, joined here. It
        fails if either does: a source that stops early leaves the sink a short input, which it
        must refuse (pg_restore does)."""
        ssh = ["ssh", "-o", "BatchMode=yes", f"root@{self.host}"]
        first = subprocess.Popen([*ssh, "k3s kubectl " + shlex.join(source)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        second = subprocess.run([*ssh, "k3s kubectl " + shlex.join(sink)], stdin=first.stdout, capture_output=True)
        first.stdout.close()
        source_err = first.stderr.read().decode(errors="replace")
        first.stderr.close()
        if first.wait() != 0:
            raise ServerError(f"on {self.host}: kubectl {' '.join(source[:4])} … failed ({first.returncode})", source_err)
        if second.returncode != 0:
            raise ServerError(f"on {self.host}: kubectl {' '.join(sink[:4])} … failed ({second.returncode})",
                              (second.stdout + second.stderr).decode(errors="replace"))

    def ok(self, *args: str) -> bool:
        try:
            self.kubectl(*args)
        except ServerError:
            return False
        return True

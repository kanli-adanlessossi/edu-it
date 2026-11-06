"""``FakeLabWorld``: a small in-memory process and filesystem model for ``FakeK8sApi`` exec.

It makes the shared Sandbox_Port contract suite run honestly over ``FakeK8sApi`` without a
cluster (kubernetes-deployment Req 1.9, 5.7). It is installed as the ``exec_handler`` of the
fake API and understands exactly the command shapes ``K8sExecAdapter`` produces:

- the exec wrapper (``sh -c 'cd "$1" ...' sh <cwd> env EDU_IT_EXEC=<marker> K=V <command>``),
  after which the command itself is interpreted;
- ``tar -x`` (archive on stdin) and ``tar -c`` (archive on stdout), optionally behind
  ``timeout -s KILL <n>``, over one flat per-pod filesystem;
- the post-timeout kill script (a no-op that succeeds);
- ``true`` and ``false`` and, for ``echo``, its arguments on stdout.

Any other command exits 127 with ``command not found``. This is a call-and-data model: it has
no kernel, users or process isolation, so it is **not** security evidence (hands-on-labs
Requirement 10.12). Isolation, grading-isolation, tamper-resistance and Check-abuse behavior
are evidenced on a real cluster (kubernetes-deployment task 6.23).
"""

from __future__ import annotations

import io
import re
import tarfile
from collections.abc import Sequence
from posixpath import join, normpath

from edu_it_infrastructure.fakes.k8s_api import FakeK8sApi
from edu_it_infrastructure.sandbox.k8s_api import ExecOutput, PodView

__all__ = ["FakeLabWorld"]

_ENV_PAIR = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_NOT_FOUND = 127


class FakeLabWorld:
    """Per-pod in-memory filesystem and a tiny command interpreter."""

    def __init__(self, api: FakeK8sApi) -> None:
        self._api = api
        self._files: dict[str, dict[str, bytes]] = {}
        api.exec_handler = self.handle

    # ------------------------------------------------------------------ introspection

    def files(self, pod: str) -> dict[str, bytes]:
        """Copy of the files of ``pod`` (path to content)."""

        return dict(self._files.get(pod, {}))

    # ------------------------------------------------------------------ handler

    def handle(self, pod: PodView, container: str, command: Sequence[str]) -> ExecOutput | None:
        argv = list(command)
        if _is_kill_script(argv):
            return ExecOutput(0)
        argv = _unwrap(argv)
        if argv[:1] == ["/usr/bin/timeout"] and len(argv) > 4:
            argv = argv[4:]
        if not argv:
            return ExecOutput(0)
        program = argv[0]
        if program == "/bin/tar":
            return self._tar(pod, argv[1:])
        if program in ("/bin/true", "true"):
            return ExecOutput(0)
        if program in ("/bin/false", "false"):
            return ExecOutput(1)
        if program in ("/bin/echo", "echo"):
            return ExecOutput(0, (" ".join(argv[1:]) + "\n").encode())
        return ExecOutput(_NOT_FOUND, b"", f"{program}: command not found\n".encode())

    # ------------------------------------------------------------------ tar

    def _tar(self, pod: PodView, args: list[str]) -> ExecOutput:
        files = self._files.setdefault(pod.name, {})
        directory = _option(args, "-C") or "/"
        if "-x" in args:
            data = self._api.exec_stdin[-1] or b""
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tf:
                for member in tf.getmembers():
                    if not member.isreg():
                        continue
                    handle = tf.extractfile(member)
                    content = handle.read() if handle is not None else b""
                    files[normpath(join(directory, member.name))] = content
            return ExecOutput(0)
        # tar -c -C dir -f - NAME
        name = args[-1]
        root = normpath(join(directory, name))
        selected = {
            path: content
            for path, content in files.items()
            if path == root or path.startswith(root.rstrip("/") + "/")
        }
        if not selected:
            message = f"tar: {name}: Cannot stat: No such file or directory\n"
            return ExecOutput(2, b"", message.encode())
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as tf:
            for path, content in sorted(selected.items()):
                arcname = name if path == root else name + path[len(root) :]
                info = tarfile.TarInfo(arcname)
                info.size = len(content)
                info.mode = 0o644
                tf.addfile(info, io.BytesIO(content))
        return ExecOutput(0, out.getvalue())


def _option(args: Sequence[str], flag: str) -> str | None:
    for index, arg in enumerate(args[:-1]):
        if arg == flag:
            return args[index + 1]
    return None


def _is_kill_script(argv: Sequence[str]) -> bool:
    return len(argv) > 2 and argv[0] == "/bin/sh" and argv[1] == "-c" and "kill -s KILL" in argv[2]


def _unwrap(argv: list[str]) -> list[str]:
    """Strip the exec wrapper (cwd change, ``env`` and its ``NAME=value`` pairs)."""

    if len(argv) > 5 and argv[:2] == ["/bin/sh", "-c"] and argv[3] == "sh":
        rest = argv[5:]
        if rest[:1] == ["/usr/bin/env"]:
            rest = rest[1:]
            while rest and _ENV_PAIR.match(rest[0]):
                rest = rest[1:]
        return rest
    return argv

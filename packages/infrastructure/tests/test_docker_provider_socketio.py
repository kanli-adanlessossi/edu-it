"""The Docker provider writes to the hijacked exec socket on Linux hosts.

On a unix-socket daemon (Linux, CI) ``exec_start(socket=True)`` returns a
``socket.SocketIO`` wrapper, not a raw socket. ``SocketIO`` has no ``sendall`` or
``shutdown``; those live on its ``_sock``. Before the fix, ``copy_in`` sent no tar data,
``tar -x`` waited for stdin until ``timeout -s KILL`` fired (exit 137), and terminal
writes raised ``SandboxGone``. Docker Desktop on Windows returns an ``NpipeSocket``, which
has ``sendall``, so the bug did not show there. These tests use a real ``SocketIO`` over a
``socketpair`` and need no Docker daemon.
"""

from __future__ import annotations

import io
import socket
from collections.abc import Iterator
from contextlib import contextmanager

from edu_it_infrastructure.sandbox import docker_provider as dp


@contextmanager
def _hijacked_pair() -> Iterator[tuple[socket.SocketIO, socket.socket]]:
    """A ``SocketIO`` like the Docker SDK returns on unix sockets, and its peer."""
    ours, peer = socket.socketpair()
    wrapped = socket.SocketIO(ours, "rwb")
    try:
        yield wrapped, peer
    finally:
        wrapped.close()
        ours.close()
        peer.close()


def _read_all(peer: socket.socket) -> bytes:
    peer.settimeout(5)
    chunks = []
    while chunk := peer.recv(65536):
        chunks.append(chunk)
    return b"".join(chunks)


def test_stdin_stream_reaches_the_peer_and_ends_with_eof_over_socketio() -> None:
    payload = b"x" * (3 * dp._CHUNK + 17)
    with _hijacked_pair() as (sock, peer):
        dp._send_stream(sock, io.BytesIO(payload))
        dp._half_close(sock)
        # recv returns b"" only after the half-close, so this also proves the EOF.
        assert _read_all(peer) == payload


def test_terminal_write_reaches_the_peer_over_socketio() -> None:
    with _hijacked_pair() as (sock, peer):
        terminal = dp._DockerTerminal(api=None, exec_id="e1", sock=sock)
        terminal.write(b"echo hello\n")
        peer.settimeout(5)
        assert peer.recv(64) == b"echo hello\n"

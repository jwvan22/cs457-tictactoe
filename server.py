#!/usr/bin/env python3
"""CS 457 Tic-Tac-Toe server.

Single-threaded, non-blocking server built on a selectors event loop
(docs/concurrency_design.md). This module is the only layer that
touches sockets. Framing and validation live in protocol/, and all game
rules live in game/fsm.py, which returns Outbound actions that this
loop performs.

Usage:
    python3 server.py -h
    python3 server.py -p 5555
"""

import argparse
import logging
import selectors
import signal
import socket
import sys
import time
from dataclasses import dataclass, field
from itertools import count

from game.fsm import GameFSM, Outbound
from protocol.framing import FrameReader, FrameTooLargeError, encode_message
from protocol.messages import (
    ErrorCode, MsgType, ProtocolError, decode_and_validate, make_error,
)

log = logging.getLogger("server")

LISTEN_HOST = "0.0.0.0"
LISTEN_BACKLOG = 5
RECV_SIZE = 4096
MAX_OUTBUF_BYTES = 64 * 1024
MIN_PORT, MAX_PORT = 1024, 65535

# TCP keepalive: first probe after 30 s idle, every 10 s, give up after 3.
KEEPALIVE_IDLE, KEEPALIVE_INTERVAL, KEEPALIVE_COUNT = 30, 10, 3

DISCONNECT_ERRORS = (ConnectionResetError, ConnectionAbortedError,
                     BrokenPipeError, TimeoutError)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def port_number(text: str) -> int:
    """argparse type: an integer port from 1024 to 65535."""
    try:
        port = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"'{text}' is not a number") from None
    if not MIN_PORT <= port <= MAX_PORT:
        raise argparse.ArgumentTypeError(
            f"{port} is out of range, use {MIN_PORT} to {MAX_PORT}")
    return port


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse -h and -p. argparse prints usage and exits 2 on bad input."""
    parser = argparse.ArgumentParser(
        prog="server.py",
        description="Two-player Tic-Tac-Toe server. Listens on all "
                    "interfaces (0.0.0.0) and runs one game series at a "
                    "time.",
        epilog="Example: python3 server.py -p 5555",
    )
    parser.add_argument(
        "-p", "--port", type=port_number, required=True,
        help=f"TCP port to listen on ({MIN_PORT}-{MAX_PORT})",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="also log every frame sent and received",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Per-connection record (concurrency_design.md section 2.1)
# ---------------------------------------------------------------------------

@dataclass
class Connection:
    """State the event loop keeps for one accepted client socket."""

    conn_id: str
    sock: socket.socket
    addr: tuple
    reader: FrameReader = field(default_factory=FrameReader)
    outbuf: bytearray = field(default_factory=bytearray)
    close_after_flush: bool = False
    closed: bool = False

    def label(self) -> str:
        return f"{self.conn_id} {self.addr[0]}:{self.addr[1]}"


def enable_keepalive(sock: socket.socket) -> None:
    """Turn on TCP keepalive so a silently dead peer is detected."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    # These options are Linux names. macOS lacks TCP_KEEPIDLE, so each
    # one is set only if this platform defines it.
    for name, value in (("TCP_KEEPIDLE", KEEPALIVE_IDLE),
                        ("TCP_KEEPINTVL", KEEPALIVE_INTERVAL),
                        ("TCP_KEEPCNT", KEEPALIVE_COUNT)):
        if hasattr(socket, name):
            sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), value)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class GameServer:
    """Owns the selector, the listening socket, and every connection."""

    def __init__(self, port: int, verbose: bool = False) -> None:
        self.port = port
        self.verbose = verbose
        self.sel = selectors.DefaultSelector()
        self.fsm = GameFSM()
        self.registry: dict[str, Connection] = {}
        self._ids = count(1)
        self.lsock: socket.socket | None = None

    # -- setup (FSM INIT) --------------------------------------------------

    def listen(self) -> None:
        """Create, bind, and register the listening socket (T1 / T2)."""
        lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            lsock.bind((LISTEN_HOST, self.port))
            lsock.listen(LISTEN_BACKLOG)
        except OSError as exc:
            # T2: bind failure ends the process with a nonzero status.
            lsock.close()
            log.error("Cannot listen on %s:%d: %s",
                      LISTEN_HOST, self.port, exc.strerror or exc)
            sys.exit(1)
        lsock.setblocking(False)
        self.sel.register(lsock, selectors.EVENT_READ, data=None)
        self.lsock = lsock
        log.info("Listening on %s:%d", LISTEN_HOST, self.port)
        self.fsm.start()                                           # T1

    # -- main loop (section 3.5) -------------------------------------------

    def run(self) -> None:
        """Serve forever. Returns on Ctrl+C after closing every socket."""
        try:
            while True:
                events = self.sel.select(timeout=self._timer_delay())
                for key, mask in events:
                    if key.data is None:
                        self._accept(key.fileobj)
                    else:
                        self._service(key.data, mask)
                self._apply(self.fsm.handle_tick(time.monotonic()))
        except KeyboardInterrupt:
            log.info("Shutting down")
        finally:
            for conn in list(self.registry.values()):
                self._close(conn)
            if self.lsock:
                self.sel.unregister(self.lsock)
                self.lsock.close()
            self.sel.close()

    def _timer_delay(self) -> float | None:
        """Seconds until the FSM's next timer, or None to wait forever."""
        deadline = self.fsm.next_deadline()
        if deadline is None:
            return None
        return max(0.0, deadline - time.monotonic())

    # -- accept (section 3.2) ----------------------------------------------

    def _accept(self, lsock: socket.socket) -> None:
        try:
            sock, addr = lsock.accept()
        except BlockingIOError:
            return
        sock.setblocking(False)
        enable_keepalive(sock)
        conn = Connection(conn_id=f"c{next(self._ids)}", sock=sock,
                          addr=addr)
        self.registry[conn.conn_id] = conn
        # Read interest only. Write interest is added when output is queued.
        self.sel.register(sock, selectors.EVENT_READ, data=conn)
        log.info("Accepted %s", conn.label())

    # -- service (section 3.4) ---------------------------------------------

    def _service(self, conn: Connection, mask: int) -> None:
        if conn.closed:
            return                          # stale event for a closed socket
        if mask & selectors.EVENT_READ and not conn.close_after_flush:
            self._read(conn)
        if mask & selectors.EVENT_WRITE and not conn.closed:
            self._flush(conn)

    def _read(self, conn: Connection) -> None:
        try:
            chunk = conn.sock.recv(RECV_SIZE)
        except BlockingIOError:
            return                          # spurious wakeup
        except DISCONNECT_ERRORS as exc:
            log.info("%s dropped (%s)", conn.label(), type(exc).__name__)
            self._client_disconnected(conn)
            return
        if not chunk:
            log.info("%s closed the connection (EOF)", conn.label())
            self._client_disconnected(conn)
            return

        try:
            frames = conn.reader.feed(chunk)
        except FrameTooLargeError:
            log.warning("%s sent %d+ bytes with no newline", conn.label(),
                        conn.reader.max_frame_bytes)
            self._enqueue(conn, make_error(
                ErrorCode.MESSAGE_TOO_LARGE,
                "Message exceeds 4096 bytes without a newline",
                fatal=True), close_after=True)
            self._apply(self.fsm.handle_disconnect(conn.conn_id))
            return

        for frame in frames:
            if conn.closed or conn.close_after_flush:
                break
            self._dispatch(conn, frame)

    def _dispatch(self, conn: Connection, frame: bytes) -> None:
        """Validate one frame and hand it to the matching FSM handler."""
        if self.verbose:
            log.debug("RX %s %s", conn.conn_id,
                      frame.decode("utf-8", "replace"))
        try:
            msg = decode_and_validate(frame, sender_is_client=True)
        except ProtocolError as err:
            # Bad-frame halves of T5, T11, T23: reply, state untouched.
            log.info("Rejected frame from %s: %s", conn.conn_id, err)
            self._enqueue(conn, make_error(err.code, err.detail))
            return

        msg_type = MsgType(msg["msg_type"])
        if msg_type is MsgType.CONNECT:
            self._apply(self.fsm.handle_connect(conn.conn_id, msg))
        elif msg_type is MsgType.MOVE:
            self._apply(self.fsm.handle_move(conn.conn_id, msg))
        elif msg_type is MsgType.DISCONNECT:
            # Blueprint 5.1: act on DISCONNECT immediately, ignore the EOF.
            log.info("%s sent DISCONNECT (%s)", conn.label(),
                     msg["payload"]["reason"] or "no reason")
            self._client_disconnected(conn)

    # -- output (section 3.4) ----------------------------------------------

    def _apply(self, actions: list[Outbound]) -> None:
        """Carry out the FSM's Outbound list."""
        for action in actions:
            conn = self.registry.get(action.conn_id)
            if conn is None or conn.closed:
                continue                    # already gone, nothing to do
            if action.message is not None:
                self._enqueue(conn, action.message, action.close_after)
            elif action.close_after:
                conn.close_after_flush = True
                if not conn.outbuf:
                    self._close(conn)

    def _enqueue(self, conn: Connection, message: dict,
                 close_after: bool = False) -> None:
        """Append a frame to the outbound buffer and request EVENT_WRITE."""
        if self.verbose:
            log.debug("TX %s %s", conn.conn_id,
                      encode_message(message)[:-1].decode("utf-8"))
        conn.outbuf += encode_message(message)
        if close_after:
            conn.close_after_flush = True
        if len(conn.outbuf) > MAX_OUTBUF_BYTES:
            log.warning("%s is not reading, dropping it", conn.label())
            self._client_disconnected(conn)
            return
        self.sel.modify(conn.sock,
                        selectors.EVENT_READ | selectors.EVENT_WRITE,
                        data=conn)

    def _flush(self, conn: Connection) -> None:
        """Send as much of outbuf as the kernel accepts. Never sendall()."""
        try:
            sent = conn.sock.send(conn.outbuf)
        except BlockingIOError:
            return                          # kernel buffer full, retry later
        except DISCONNECT_ERRORS as exc:
            log.info("%s dropped during send (%s)", conn.label(),
                     type(exc).__name__)
            self._client_disconnected(conn)
            return
        del conn.outbuf[:sent]
        if conn.outbuf:
            return
        if conn.close_after_flush:
            self._close(conn)
        else:
            self.sel.modify(conn.sock, selectors.EVENT_READ, data=conn)

    # -- teardown (section 3.6) --------------------------------------------

    def _client_disconnected(self, conn: Connection) -> None:
        """CLIENT_DISCONNECTED: tell the FSM, then close this socket."""
        if conn.closed:
            return
        conn.closed = True
        actions = self.fsm.handle_disconnect(conn.conn_id)
        self._close(conn)
        self._apply(actions)

    def _close(self, conn: Connection) -> None:
        """Unregister and close one socket. Safe to call twice."""
        conn.closed = True
        if conn.conn_id not in self.registry:
            return
        try:
            self.sel.unregister(conn.sock)  # always before close()
        except (KeyError, ValueError):
            pass
        conn.sock.close()
        del self.registry[conn.conn_id]
        log.info("Closed %s", conn.label())


def _on_sigterm(signum, frame) -> None:
    """Treat SIGTERM (kill, systemd stop) like Ctrl+C for a clean exit."""
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    signal.signal(signal.SIGTERM, _on_sigterm)
    server = GameServer(args.port, verbose=args.verbose)
    server.listen()
    server.run()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""CS 457 Tic-Tac-Toe client.

A thin client: it sends CONNECT, MOVE, and DISCONNECT, and renders
whatever the server says. It never applies a move locally. A selectors
loop watches both the server socket and the keyboard, so a server
message such as a forfeit is shown right away even while the player is
in the middle of typing.

Usage:
    python3 client.py -h
    python3 client.py -i server.vanallen.edu -p 5555
"""

import argparse
import selectors
import socket
import sys

from protocol.framing import FrameReader, FrameTooLargeError, encode_message
from protocol.messages import (
    ALIAS_PATTERN, MsgType, ProtocolError, decode_and_validate,
    make_connect, make_disconnect, make_move,
)

MIN_PORT, MAX_PORT = 1024, 65535
CONNECT_TIMEOUT = 10.0
RECV_SIZE = 4096

LOST_ERRORS = (ConnectionResetError, ConnectionAbortedError,
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


def alias_text(text: str) -> str:
    """argparse type: 1 to 16 letters, digits, or underscores."""
    if not ALIAS_PATTERN.match(text):
        raise argparse.ArgumentTypeError(
            "use 1 to 16 letters, digits, or underscores")
    return text


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse -h, -i, -p, and optional -n."""
    parser = argparse.ArgumentParser(
        prog="client.py",
        description="Two-player Tic-Tac-Toe client.",
        epilog="Example: python3 client.py -i server.vanallen.edu -p 5555",
    )
    parser.add_argument(
        "-i", "--host", dest="hostname", required=True,
        help="server DNS hostname or IP address",
    )
    parser.add_argument(
        "-p", "--port", type=port_number, required=True,
        help=f"server TCP port ({MIN_PORT}-{MAX_PORT})",
    )
    parser.add_argument(
        "-n", "--name", type=alias_text, default=None,
        help="display name (asked for at startup if omitted)",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Connection setup
# ---------------------------------------------------------------------------

def fail(message: str, hint: str | None = None) -> None:
    """Print a clean error and exit with status 1. No traceback."""
    print(f"[ERROR] {message}", file=sys.stderr)
    if hint:
        print(hint, file=sys.stderr)
    sys.exit(1)


def resolve(hostname: str) -> str:
    """Resolve a hostname to an IPv4 address through DNS.

    Exits cleanly if the name does not resolve or the resolver is
    unreachable. An IP address string passes through unchanged.
    """
    try:
        return socket.gethostbyname(hostname)
    except socket.gaierror:
        fail(f"Unable to resolve DNS hostname '{hostname}'.",
             "Please check your Cisco router DNS configuration "
             "('ip dns server' / 'ip host') or verify domain spelling.")
    except UnicodeError:
        # Raised for malformed names such as "server..edu".
        fail(f"'{hostname}' is not a valid hostname.",
             "Please verify domain spelling.")


def connect(server_ip: str, port: int, hostname: str) -> socket.socket:
    """Open the TCP connection, exiting cleanly on any failure."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT)
    target = f"{hostname} ({server_ip}) port {port}"
    try:
        sock.connect((server_ip, port))
    except ConnectionRefusedError:
        sock.close()
        fail(f"Connection refused by {target}.",
             "Is the server running and listening on that port?")
    except (socket.timeout, TimeoutError):
        sock.close()
        fail(f"Timed out connecting to {target}.",
             "Check routing and firewall rules between the subnets.")
    except OSError as exc:
        sock.close()
        fail(f"Cannot reach {target}: {exc.strerror or exc}.")
    sock.settimeout(None)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    return sock


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_board(board: list) -> str:
    """Draw the board with row and column numbers."""
    lines = ["      0   1   2"]
    for r, row in enumerate(board):
        cells = [" " if cell == "-" else cell for cell in row]
        lines.append(f"  {r}   {cells[0]} | {cells[1]} | {cells[2]}")
        if r < 2:
            lines.append("     ---+---+---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Game client
# ---------------------------------------------------------------------------

class GameClient:
    """Tracks what the server has told us and reacts to input."""

    def __init__(self, sock: socket.socket, alias: str) -> None:
        self.sock = sock
        self.alias = alias
        self.reader = FrameReader()
        self.sel = selectors.DefaultSelector()
        self.my_id: str | None = None
        self.my_mark: str | None = None
        self.opponent = "opponent"
        self.active_player: str | None = None
        self.in_round = False
        self.running = True
        self.exit_code = 0

    def send(self, message: dict) -> None:
        """Send one frame. The client socket is blocking, so sendall()."""
        self.sock.sendall(encode_message(message))

    def scoreline(self, scores: dict, round_number: int) -> str:
        if self.my_id is None:
            return ""
        theirs = "Player_2" if self.my_id == "Player_1" else "Player_1"
        return (f"Round {round_number}  |  Score: {self.alias} "
                f"{scores[self.my_id]}, {self.opponent} {scores[theirs]}, "
                f"draws {scores['draws']}")

    def prompt(self) -> None:
        """Print the turn status line."""
        if not self.in_round:
            return
        if self.active_player == self.my_id:
            print(f"\n>> Your turn ({self.my_mark}). Enter row and column "
                  "(for example: 1 2), or q to quit: ", end="", flush=True)
        else:
            print(f"\n.. Waiting for {self.opponent} to move...", flush=True)

    # -- server messages ---------------------------------------------------

    def on_message(self, msg: dict) -> None:
        payload = msg["payload"]
        msg_type = MsgType(msg["msg_type"])

        if msg_type is MsgType.LOBBY_WAIT:
            print(f"Connected. {payload['message']}...", flush=True)

        elif msg_type is MsgType.GAME_START:
            self.my_id = payload["your_id"]
            self.my_mark = payload["your_mark"]
            self.opponent = payload["opponent_alias"]
            first = ("you start" if payload["first_turn"] == self.my_id
                     else f"{self.opponent} starts")
            print(f"\n=== Round {payload['round']}: {self.alias} "
                  f"({self.my_mark}) vs {self.opponent} ===")
            print(f"You are {self.my_id}. X moves first, so {first}.")

        elif msg_type is MsgType.STATE_UPDATE:
            self.active_player = payload["active_player"]
            self.in_round = self.active_player is not None
            last = payload["last_move"]
            print()
            if last:
                who = ("You" if last["player_id"] == self.my_id
                       else self.opponent)
                print(f"{who} played row {last['row']}, "
                      f"column {last['col']}.")
            print(render_board(payload["board"]))
            print(self.scoreline(payload["scores"], payload["round"]))
            self.prompt()

        elif msg_type is MsgType.ERROR:
            print(f"\n[server] {payload['code']}: {payload['detail']}",
                  flush=True)
            if payload["fatal"]:
                self.stop(1)
            else:
                self.prompt()

        elif msg_type is MsgType.GAME_OVER:
            self.in_round = False
            self.show_game_over(payload)

    def show_game_over(self, payload: dict) -> None:
        result, winner = payload["result"], payload["winner"]
        print()
        if result == "DRAW":
            print("*** Round over: draw. ***")
        elif winner == self.my_id:
            print(f"*** You win! ({payload['reason']}) ***")
        else:
            print(f"*** {self.opponent} wins. ({payload['reason']}) ***")
        print(self.scoreline(payload["scores"], payload["round"]))
        if payload["series_continues"]:
            print("Next round starts in 3 seconds...", flush=True)
        else:
            print("Series over. Thanks for playing.", flush=True)
            self.stop(0)

    # -- keyboard ----------------------------------------------------------

    def on_input(self, line: str) -> None:
        text = line.strip().lower()
        if text in ("q", "quit", "exit"):
            self.quit("user quit")
            return
        if not text:
            self.prompt()
            return
        if self.my_id is None:
            print("The game has not started yet.", flush=True)
            return
        parts = text.replace(",", " ").split()
        if len(parts) != 2 or not all(p.lstrip("-").isdigit()
                                      for p in parts):
            print("Enter two numbers, row then column, like: 0 2",
                  flush=True)
            self.prompt()
            return
        # The server decides whether the move is legal, including turn
        # order and range. The client only sends it.
        self.send(make_move(self.my_id, int(parts[0]), int(parts[1])))

    # -- lifecycle ---------------------------------------------------------

    def quit(self, reason: str) -> None:
        """Send DISCONNECT (blueprint 3.8), then close."""
        try:
            self.send(make_disconnect(self.my_id, reason))
        except OSError:
            pass
        print("\nYou left the game.")
        self.stop(0)

    def stop(self, code: int) -> None:
        self.running = False
        self.exit_code = code

    def read_socket(self) -> None:
        try:
            chunk = self.sock.recv(RECV_SIZE)
        except LOST_ERRORS:
            chunk = b""
        if not chunk:
            # Blueprint 5.5: lost the server before GAME_OVER.
            print("\n[ERROR] Connection to the server was lost.",
                  file=sys.stderr)
            self.stop(1)
            return
        try:
            frames = self.reader.feed(chunk)
        except FrameTooLargeError:
            print("\n[ERROR] Server sent an oversized message.",
                  file=sys.stderr)
            self.stop(1)
            return
        for frame in frames:
            try:
                msg = decode_and_validate(frame, sender_is_client=False)
            except ProtocolError as err:
                print(f"\n[warning] Ignored bad frame from server: {err}",
                      file=sys.stderr)
                continue
            self.on_message(msg)
            if not self.running:
                return

    def run(self) -> int:
        self.send(make_connect(self.alias))
        self.sel.register(self.sock, selectors.EVENT_READ, "socket")
        self.sel.register(sys.stdin, selectors.EVENT_READ, "stdin")
        try:
            while self.running:
                for key, _ in self.sel.select():
                    if key.data == "socket":
                        self.read_socket()
                    else:
                        line = sys.stdin.readline()
                        if line == "":           # Ctrl+D or closed stdin
                            self.quit("input closed")
                        else:
                            self.on_input(line)
                    if not self.running:
                        break
        except KeyboardInterrupt:
            self.quit("user quit")
        except LOST_ERRORS:
            print("\n[ERROR] Connection to the server was lost.",
                  file=sys.stderr)
            self.exit_code = 1
        finally:
            self.sel.close()
            self.sock.close()
        return self.exit_code


def ask_alias() -> str:
    """Ask for a display name until a valid one is entered."""
    while True:
        try:
            alias = input("Enter your name (1-16 letters, digits, _): ")
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        alias = alias.strip()
        if ALIAS_PATTERN.match(alias):
            return alias
        print("Invalid name, try again.")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    server_ip = resolve(args.hostname)
    alias = args.name or ask_alias()
    sock = connect(server_ip, args.port, args.hostname)
    print(f"Connected to {args.hostname} ({server_ip}) port {args.port} "
          f"as {alias}.", flush=True)
    sys.exit(GameClient(sock, alias).run())


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Scripted localhost demo: one server, two players, one late joiner.

Starts real server.py and client.py processes, types a fixed sequence
of moves into each client, and writes every process's console output to
a log file. Covers out-of-turn, occupied-cell, and out-of-range moves, a
round win, the 3 second intermission, GAME_FULL for a third client,
round 2 with swapped marks, and a forfeit when a player quits.

Usage (from the repo root):
    python3 tools/run_demo.py [-p PORT] [-o LOGFILE]
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STEP_DELAY = 0.4


def spawn(args: list[str]) -> subprocess.Popen:
    """Start a repo script with piped stdin and merged stdout/stderr."""
    return subprocess.Popen(
        [sys.executable, "-u", *args], cwd=REPO, text=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-p", "--port", default="5555")
    parser.add_argument("-o", "--output", default="demo_run.log")
    args = parser.parse_args()
    port = args.port
    typed: list[str] = []

    def say(proc: subprocess.Popen, who: str, line: str) -> None:
        typed.append(f"[{who} typed] {line}")
        proc.stdin.write(line + "\n")
        proc.stdin.flush()
        time.sleep(STEP_DELAY)

    server = spawn(["server.py", "-p", port])
    time.sleep(0.5)
    alice = spawn(["client.py", "-i", "localhost", "-p", port, "-n",
                   "Alice"])
    time.sleep(0.5)
    bob = spawn(["client.py", "-i", "localhost", "-p", port, "-n", "Bob"])
    time.sleep(0.6)

    # Round 1: Alice holds X and moves first.
    say(bob, "Bob", "1 1")        # OUT_OF_TURN
    say(alice, "Alice", "1 1")
    say(bob, "Bob", "1 1")        # CELL_OCCUPIED
    say(bob, "Bob", "0 0")
    say(alice, "Alice", "3 0")    # INVALID_COORDINATES
    say(alice, "Alice", "0 2")
    say(bob, "Bob", "2 2")
    say(alice, "Alice", "2 0")    # Alice wins on the anti-diagonal
    time.sleep(0.5)
    carol = spawn(["client.py", "-i", "localhost", "-p", port, "-n",
                   "Carol"])      # GAME_FULL during intermission
    time.sleep(3.2)

    # Round 2: marks swap, so Bob holds X and moves first.
    say(bob, "Bob", "0 0")
    say(alice, "Alice", "1 1")
    say(bob, "Bob", "quit")       # forfeit, series over
    time.sleep(0.8)
    server.terminate()

    with open(args.output, "w") as log:
        for name, proc in (("SERVER", server), ("CLIENT 1: Alice", alice),
                           ("CLIENT 2: Bob", bob),
                           ("CLIENT 3: Carol", carol)):
            try:
                out = proc.communicate(timeout=3)[0]
            except subprocess.TimeoutExpired:
                proc.kill()
                out = proc.communicate()[0]
            log.write(f"\n{'=' * 18} {name} {'=' * 18}\n{out}")
            log.write(f"[exit status {proc.returncode}]\n")
        log.write(f"\n{'=' * 18} KEYBOARD INPUT, IN ORDER {'=' * 18}\n")
        log.write("\n".join(typed) + "\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()

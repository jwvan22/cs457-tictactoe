# CS 457 Final Project

Two-player networked Tic-Tac-Toe over TCP, written in Python 3.

- Game: 3x3 Tic-Tac-Toe, turn based, two players, played as a series of rounds
- Language: Python 3.10 or later (standard library only)
- Target server domain: server.vanallen.edu
- Statement of Work: see `SOW_VanAllen_Jake.md`

## Running

Start the server. It listens on all interfaces (0.0.0.0).

```
python3 server.py -p 5555
python3 server.py -p 5555 -v     # also log every frame sent and received
```

Start two clients, each in its own terminal. `-i` takes a DNS hostname or an IP address.

```
python3 client.py -i localhost -p 5555 -n Alice
python3 client.py -i localhost -p 5555 -n Bob
```

In Sprint 4 the client is pointed at the lab DNS name instead:

```
python3 client.py -i server.vanallen.edu -p 5555
```

| Flag | Program | Meaning |
|---|---|---|
| `-h` | both | Show help and exit |
| `-p PORT` | both | Required. TCP port, 1024 to 65535 |
| `-i HOST` | client | Required. Server hostname, resolved with `socket.gethostbyname()` |
| `-n NAME` | client | Optional display name. Asked for at startup if omitted |
| `-v` | server | Optional. Log every frame |

During play, type a row and column such as `1 2`, or `q` to quit. If the hostname does not resolve, the client prints an error that points to the router DNS configuration and exits with status 1, without a traceback.

## Testing

```
python3 -m unittest -v           # unit tests for framing, messages, board, and FSM
python3 -m flake8 .              # PEP 8 check
python3 tools/run_demo.py        # scripted two-client game on localhost, writes demo_run.log
```

The Sprint 3 execution log is in `docs/sprint3_execution_log.txt`.

## Structure

| Path | Purpose |
|---|---|
| `server.py` | Authoritative server. Single-threaded `selectors` event loop and the only code that touches server sockets |
| `client.py` | Player client. Watches the socket and the keyboard with `selectors` |
| `protocol/framing.py` | Newline-delimited JSON framing and the per-connection `FrameReader` |
| `protocol/messages.py` | Message types, error codes, validation, and message builders |
| `game/board.py` | Board rules, no network code |
| `game/fsm.py` | `GameFSM`, the server state machine (T1 to T23), no network code |
| `tests/` | `unittest` suites |
| `tools/run_demo.py` | Scripted localhost demo used for the execution log |
| `docs/` | Protocol blueprint, FSM specification, concurrency design, AI prompt strategy |

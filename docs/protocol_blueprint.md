# Application Protocol Blueprint

**Project:** cs457-tictactoe
**Author:** Jake VanAllen
**Sprint:** 1 (Protocol and FSM Design)
**Target Server Domain:** `server.vanallen.edu`

This document defines the application-layer protocol spoken between the Tic-Tac-Toe server and its two clients. It is the single source of truth for message structure, framing, validation, and connection termination. Any code that reads or writes protocol messages, whether written by hand or generated with AI assistance, must conform to this document exactly.

---

## 1. Transport and Framing

### 1.1 Transport

| Property | Value |
|---|---|
| Transport protocol | TCP |
| Address family | IPv4 (`AF_INET`) |
| Server listening port | `5555` (configurable at launch) |
| Server role | Authoritative. The server owns the board, validates every move, and decides all outcomes. |
| Client role | Thin. Clients send requests and render whatever state the server sends. Clients never apply moves locally. |

### 1.2 Serialization

Every message is a single JSON object encoded as UTF-8. Messages are serialized in compact form with no whitespace between tokens, equivalent to Python's `json.dumps(msg, separators=(",", ":"))`.

### 1.3 Framing Rule: Newline-Delimited JSON

TCP delivers a continuous byte stream with no message boundaries. Two messages sent back to back may arrive in one `recv()` call (coalescing), and one message may be split across several `recv()` calls (fragmentation). This protocol marks message boundaries as follows:

> **Each message is exactly one JSON object followed by exactly one newline byte, `\n` (0x0A). The newline is the only frame terminator.**

**Why this is collision-free.** A standards-compliant JSON encoder never emits a raw 0x0A byte inside a value. A newline inside a string is escaped as the two characters `\` and `n` (bytes 0x5C 0x6E). Compact serialization also removes any newlines between tokens. As a result, a raw 0x0A byte can only appear on the wire as a frame terminator, so no escaping scheme is required.

**Sender rules**

1. Build the message as a dictionary that matches the schemas in Section 3.
2. Serialize it with compact JSON and encode it as UTF-8.
3. Append a single `\n` byte.
4. Transmit the full frame with `sendall()`, never `send()`, so a partial write cannot leave half a frame on the wire.

**Receiver rules**

1. Keep one persistent byte buffer per connection.
2. Append every chunk returned by `recv()` to that buffer.
3. While the buffer contains a `\n`, split off everything before the first `\n` as one complete frame, remove it and the newline from the buffer, and decode it.
4. Keep any bytes after the last `\n` in the buffer. They are the beginning of the next frame.
5. If the buffer grows beyond **4096 bytes** without containing a `\n`, treat the stream as corrupt: send `ERROR` with code `MESSAGE_TOO_LARGE` and `fatal: true`, then close the connection.
6. If `recv()` returns `b""`, the peer has closed the connection. See Section 5.

**Reference receive loop (Python)**

```python
MAX_FRAME_BYTES = 4096

def read_frames(sock, buffer: bytearray):
    """Yield complete decoded messages. Returns when the peer closes."""
    while True:
        chunk = sock.recv(1024)
        if not chunk:                      # EOF: peer closed (TCP FIN)
            return
        buffer.extend(chunk)
        while b"\n" in buffer:
            frame, _, rest = bytes(buffer).partition(b"\n")
            buffer[:] = rest
            yield json.loads(frame.decode("utf-8"))
        if len(buffer) > MAX_FRAME_BYTES:
            raise FrameTooLargeError()
```

### 1.4 Wire Stream Examples

**Normal stream.** Two frames sent back to back appear on the wire as one continuous byte sequence:

```
{"msg_type":"CONNECT","player_id":null,"payload":{"alias":"Alice"},"timestamp":1727000000}\n{"msg_type":"LOBBY_WAIT","player_id":"SERVER","payload":{"players_connected":1,"message":"Waiting for an opponent"},"timestamp":1727000000}\n
```

**Coalescing.** A single `recv()` returns both a `STATE_UPDATE` and a `GAME_OVER`. The receiver splits on the first `\n`, processes `STATE_UPDATE`, then finds a second `\n` and processes `GAME_OVER`, all from one chunk.

```
recv() #1 -> {"msg_type":"STATE_UPDATE", ... }\n{"msg_type":"GAME_OVER", ... }\n
```

**Fragmentation.** A single `MOVE` frame is split across two `recv()` calls. After the first call the buffer holds no `\n`, so nothing is decoded. After the second call the terminator arrives and the complete frame is extracted.

```
recv() #1 -> {"msg_type":"MOVE","player_id":"Pla
recv() #2 -> yer_1","payload":{"row":0,"col":2},"timestamp":1727000005}\n
```

---

## 2. Common Message Envelope

Every message, in both directions, uses the same four top-level fields. No other top-level fields are permitted.

| Field | Type | Required | Description |
|---|---|---|---|
| `msg_type` | string | Yes | One of the eight values in Section 3. Uppercase, exact match. |
| `player_id` | string or null | Yes | Sender identity. Clients use their server-assigned ID (`"Player_1"` or `"Player_2"`). Server messages use `"SERVER"`. `CONNECT` uses `null` because no ID has been assigned yet. |
| `payload` | object | Yes | Message-specific body defined in Section 3. Uses `{}` when a message carries no data. |
| `timestamp` | integer | Yes | Unix epoch time in whole seconds at the moment the sender created the message. Informational only; never used for ordering or validation. |

**Board representation.** Wherever the board appears, it is a 3x3 array of rows. Each cell holds `"X"`, `"O"`, or `"-"` for empty. Rows and columns are zero-indexed, so `board[0][2]` is the top-right cell.

```json
[["X","-","-"],["-","O","-"],["-","-","-"]]
```

---

## 3. Message Types

### 3.1 Summary

| `msg_type` | Direction | Purpose |
|---|---|---|
| `CONNECT` | Client to Server | Request to join the game with a display alias. |
| `LOBBY_WAIT` | Server to Client | Tell the first player the server is waiting for an opponent. |
| `GAME_START` | Server to each Client | Announce the match and assign each client its ID and mark. |
| `MOVE` | Client to Server | Active player submits a cell. |
| `STATE_UPDATE` | Server to both Clients | Broadcast the authoritative board and whose turn is next. |
| `ERROR` | Server to Client | Reject a message and explain why. |
| `DISCONNECT` | Client to Server | Announce an intentional quit. |
| `GAME_OVER` | Server to both Clients | Announce the final outcome. |

### 3.2 CONNECT (Client to Server)

Sent once, immediately after the TCP connection is established.

| Payload field | Type | Constraints |
|---|---|---|
| `alias` | string | 1 to 16 characters, letters, digits, and underscores only. |

```json
{"msg_type":"CONNECT","player_id":null,"payload":{"alias":"Alice"},"timestamp":1727000000}
```

### 3.3 LOBBY_WAIT (Server to Client)

Sent to the first client after its `CONNECT` is accepted, while no opponent is connected.

| Payload field | Type | Constraints |
|---|---|---|
| `players_connected` | integer | Always `1` when sent. |
| `message` | string | Human-readable status for display. |

```json
{"msg_type":"LOBBY_WAIT","player_id":"SERVER","payload":{"players_connected":1,"message":"Waiting for an opponent"},"timestamp":1727000000}
```

### 3.4 GAME_START (Server to each Client)

Sent to both clients once the second `CONNECT` is accepted. Each client receives its own copy with its own assignment. Roles follow connection order: the first client to connect is `Player_1` and plays `X`, the second is `Player_2` and plays `O`. `X` always moves first.

| Payload field | Type | Constraints |
|---|---|---|
| `your_id` | string | `"Player_1"` or `"Player_2"`. The client uses this as its `player_id` on every later message. |
| `your_mark` | string | `"X"` or `"O"`. |
| `opponent_alias` | string | The other player's alias. |
| `first_turn` | string | Always `"Player_1"`. |

```json
{"msg_type":"GAME_START","player_id":"SERVER","payload":{"your_id":"Player_1","your_mark":"X","opponent_alias":"Bob","first_turn":"Player_1"},"timestamp":1727000003}
```

A `STATE_UPDATE` containing the empty board immediately follows every `GAME_START`.

### 3.5 MOVE (Client to Server)

| Payload field | Type | Constraints |
|---|---|---|
| `row` | integer | 0, 1, or 2. |
| `col` | integer | 0, 1, or 2. |

```json
{"msg_type":"MOVE","player_id":"Player_1","payload":{"row":0,"col":2},"timestamp":1727000005}
```

The server applies validation in this order and stops at the first failure:

1. The game is in progress. Otherwise `ERROR` `NOT_IN_GAME`.
2. `player_id` matches the ID assigned to this connection. Otherwise `ERROR` `PLAYER_MISMATCH`.
3. It is this player's turn. Otherwise `ERROR` `OUT_OF_TURN`.
4. `row` and `col` are both integers from 0 to 2. Booleans are not accepted as integers. Otherwise `ERROR` `INVALID_COORDINATES`.
5. The target cell is `"-"`. Otherwise `ERROR` `CELL_OCCUPIED`.

A rejected move does not change the board and does not change whose turn it is.

### 3.6 STATE_UPDATE (Server to both Clients)

Sent after every accepted move, and once after `GAME_START` with an empty board.

| Payload field | Type | Constraints |
|---|---|---|
| `board` | array | 3x3 board as defined in Section 2. |
| `active_player` | string or null | ID of the player who moves next. `null` when the game has ended. |
| `move_number` | integer | Count of accepted moves so far, 0 to 9. |
| `last_move` | object or null | `{"player_id": string, "row": int, "col": int}`, or `null` before the first move. |

```json
{"msg_type":"STATE_UPDATE","player_id":"SERVER","payload":{"board":[["-","-","X"],["-","-","-"],["-","-","-"]],"active_player":"Player_2","move_number":1,"last_move":{"player_id":"Player_1","row":0,"col":2}},"timestamp":1727000005}
```

The game is a single match, so no running score is tracked. The outcome is reported in `GAME_OVER`.

### 3.7 ERROR (Server to Client)

Sent only to the client whose message was rejected.

| Payload field | Type | Constraints |
|---|---|---|
| `code` | string | One of the codes in the table below. |
| `detail` | string | Human-readable explanation for display. |
| `fatal` | boolean | If `true`, the server closes the connection immediately after sending. |

| Code | Fatal | Trigger |
|---|---|---|
| `MALFORMED_MESSAGE` | No | Frame is not valid UTF-8 JSON, is not an object, is missing an envelope field, has an extra top-level field, or has a field of the wrong type. |
| `UNKNOWN_MSG_TYPE` | No | `msg_type` is not one of the eight defined types, or is a server-only type sent by a client. |
| `NOT_IN_GAME` | No | `MOVE` received while the game is not in progress. |
| `PLAYER_MISMATCH` | No | `player_id` does not match the ID the server assigned to this connection. |
| `OUT_OF_TURN` | No | `MOVE` received from the player who is not active. |
| `INVALID_COORDINATES` | No | `row` or `col` is missing, not an integer, or outside 0 to 2. |
| `CELL_OCCUPIED` | No | Target cell already holds a mark. |
| `INVALID_ALIAS` | No | `CONNECT` alias fails the constraints in Section 3.2. |
| `ALREADY_CONNECTED` | No | A second `CONNECT` arrives on a connection that has already joined. |
| `GAME_FULL` | Yes | A third client attempts to join while two players are seated. |
| `MESSAGE_TOO_LARGE` | Yes | Receive buffer exceeds 4096 bytes without a frame terminator. |

```json
{"msg_type":"ERROR","player_id":"SERVER","payload":{"code":"OUT_OF_TURN","detail":"It is Player_2's turn","fatal":false},"timestamp":1727000006}
```

### 3.8 DISCONNECT (Client to Server)

Sent by a client that is quitting intentionally, immediately before it closes its socket.

| Payload field | Type | Constraints |
|---|---|---|
| `reason` | string | Free text, 0 to 64 characters. May be empty. |

```json
{"msg_type":"DISCONNECT","player_id":"Player_2","payload":{"reason":"user quit"},"timestamp":1727000020}
```

### 3.9 GAME_OVER (Server to both Clients)

Sent once when the match ends. The server closes both connections after sending it.

| Payload field | Type | Constraints |
|---|---|---|
| `result` | string | `"WIN"`, `"DRAW"`, or `"FORFEIT"`. |
| `winner` | string or null | Winning player ID. `null` for a draw. For a forfeit, the remaining player. |
| `winning_line` | array or null | Three `[row, col]` pairs for a `WIN`, otherwise `null`. |
| `final_board` | array | Board at the moment the game ended. |
| `reason` | string | Human-readable summary, for example `"Player_2 disconnected"`. |

```json
{"msg_type":"GAME_OVER","player_id":"SERVER","payload":{"result":"WIN","winner":"Player_1","winning_line":[[0,0],[1,1],[2,2]],"final_board":[["X","O","-"],["O","X","-"],["-","-","X"]],"reason":"Player_1 completed a diagonal"},"timestamp":1727000030}
```

The server checks for a win before checking for a draw, so a move that fills the last cell and completes a line is reported as a `WIN`.

---

## 4. Example Session on the Wire

A short game from connection to forfeit, shown as one frame per line. On the wire each line ends with `\n` and the frames run together as a single stream per connection.

```
C1 -> S  {"msg_type":"CONNECT","player_id":null,"payload":{"alias":"Alice"},"timestamp":1727000000}
S -> C1  {"msg_type":"LOBBY_WAIT","player_id":"SERVER","payload":{"players_connected":1,"message":"Waiting for an opponent"},"timestamp":1727000000}
C2 -> S  {"msg_type":"CONNECT","player_id":null,"payload":{"alias":"Bob"},"timestamp":1727000003}
S -> C1  {"msg_type":"GAME_START","player_id":"SERVER","payload":{"your_id":"Player_1","your_mark":"X","opponent_alias":"Bob","first_turn":"Player_1"},"timestamp":1727000003}
S -> C2  {"msg_type":"GAME_START","player_id":"SERVER","payload":{"your_id":"Player_2","your_mark":"O","opponent_alias":"Alice","first_turn":"Player_1"},"timestamp":1727000003}
S -> C*  {"msg_type":"STATE_UPDATE","player_id":"SERVER","payload":{"board":[["-","-","-"],["-","-","-"],["-","-","-"]],"active_player":"Player_1","move_number":0,"last_move":null},"timestamp":1727000003}
C2 -> S  {"msg_type":"MOVE","player_id":"Player_2","payload":{"row":1,"col":1},"timestamp":1727000004}
S -> C2  {"msg_type":"ERROR","player_id":"SERVER","payload":{"code":"OUT_OF_TURN","detail":"It is Player_1's turn","fatal":false},"timestamp":1727000004}
C1 -> S  {"msg_type":"MOVE","player_id":"Player_1","payload":{"row":0,"col":2},"timestamp":1727000005}
S -> C*  {"msg_type":"STATE_UPDATE","player_id":"SERVER","payload":{"board":[["-","-","X"],["-","-","-"],["-","-","-"]],"active_player":"Player_2","move_number":1,"last_move":{"player_id":"Player_1","row":0,"col":2}},"timestamp":1727000005}
C2 -> S  {"msg_type":"DISCONNECT","player_id":"Player_2","payload":{"reason":"user quit"},"timestamp":1727000020}
S -> C1  {"msg_type":"GAME_OVER","player_id":"SERVER","payload":{"result":"FORFEIT","winner":"Player_1","winning_line":null,"final_board":[["-","-","X"],["-","-","-"],["-","-","-"]],"reason":"Player_2 disconnected"},"timestamp":1727000020}
```

`C*` means the server sends an identical frame to both clients.

---

## 5. Connection Termination and Socket Lifecycle

The server must detect every way a connection can end and route each one into the state machine defined in `fsm_specification.md`. All three paths below produce the same internal event, `CLIENT_DISCONNECTED(player_id)`, so the FSM handles a disconnect the same way no matter how it was detected.

### 5.1 Graceful Application Disconnect

The client sends `DISCONNECT`, then calls `close()`, which starts the TCP FIN handshake. On receiving `DISCONNECT`, the server raises `CLIENT_DISCONNECTED` immediately rather than waiting for the FIN, so the opponent is notified as quickly as possible. The EOF that follows is then ignored for that connection.

### 5.2 Transport-Layer Close Without DISCONNECT (EOF)

If a client process exits or closes its socket without sending `DISCONNECT`, the operating system still completes a clean FIN close. The server sees this as `recv()` returning `b""`. This is the POSIX end-of-file signal, not an error, and no exception is raised.

The receive loop must check for it on every call:

```python
chunk = sock.recv(1024)
if not chunk:
    raise_event("CLIENT_DISCONNECTED", player_id)
    break
```

Without this check, the loop would call `recv()` forever on a closed socket, receiving `b""` each time and consuming 100% CPU.

### 5.3 Abrupt Termination (RST, Crash, Link Failure)

If a client is killed (`kill -9`), loses power, or loses its network path (for example, a CML link is cut), no FIN is sent. The server detects this when a socket operation fails. The following exceptions are caught around every `recv()` and `sendall()` call and converted into `CLIENT_DISCONNECTED`:

| Exception | Meaning |
|---|---|
| `ConnectionResetError` | The peer sent a TCP RST. |
| `BrokenPipeError` | The server wrote to a socket whose peer has already closed. |
| `ConnectionAbortedError` | The local stack aborted the connection. |
| `TimeoutError` / `socket.timeout` | No data within the configured idle timeout. |

A silent link failure produces no packets at all, so without a timeout the server could wait on `recv()` forever. Every client socket therefore uses an idle timeout of **120 seconds**. A timeout is treated as an abrupt disconnect.

### 5.4 Server Response to a Disconnect

| Game state when the disconnect occurs | Server action |
|---|---|
| Waiting for players, lone player leaves | Remove the player, close the socket, and return to waiting with zero players. No `GAME_OVER` is sent. |
| Game in progress | Send `GAME_OVER` with `result: "FORFEIT"` and the remaining player as `winner` to the remaining client, then close both sockets. |
| Game already over | Close the socket. No further messages are sent. |

If sending `GAME_OVER` to the remaining player also fails, the server logs the failure, closes both sockets, and resets without retrying.

### 5.5 Client Response to Server Loss

If the client's `recv()` returns `b""` or raises one of the exceptions above before a `GAME_OVER` arrives, the client prints that the connection to the server was lost and exits cleanly. Clients never attempt to reconnect to a game in progress.

### 5.6 Normal End of Game and Reset

After sending `GAME_OVER`, the server closes both client sockets, clears the board, releases both player slots, and returns to waiting for players. The listening socket stays open throughout, so a new pair of clients can connect and play the next round without restarting the server. Each client closes its own socket when it receives `GAME_OVER`. Either side may close first, and both orders are handled by the EOF rule.

---

## 6. Validation Summary

Incoming frames are checked in this order. The first failing check determines the `ERROR` code returned.

1. Frame decodes as UTF-8. Otherwise `MALFORMED_MESSAGE`.
2. Frame parses as JSON and the result is an object. Otherwise `MALFORMED_MESSAGE`.
3. Exactly the four envelope fields are present with the types in Section 2. Otherwise `MALFORMED_MESSAGE`.
4. `msg_type` is a type the client is allowed to send: `CONNECT`, `MOVE`, or `DISCONNECT`. Otherwise `UNKNOWN_MSG_TYPE`.
5. The payload matches that message's schema in Section 3. Otherwise the message-specific code (`INVALID_ALIAS`, `INVALID_COORDINATES`, or `MALFORMED_MESSAGE`).
6. The message is legal in the current game state, as defined in `fsm_specification.md`. Otherwise the state-specific code (`NOT_IN_GAME`, `OUT_OF_TURN`, `CELL_OCCUPIED`, and so on).

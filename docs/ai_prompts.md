# AI Prompting and Constraint Strategy

**Project:** cs457-tictactoe
**Author:** Jake VanAllen
**Sprint:** 1 (Protocol and FSM Design)
**Governing specifications:** [`protocol_blueprint.md`](protocol_blueprint.md), [`fsm_specification.md`](fsm_specification.md)

This document records how AI coding assistants are used on this project and how they are constrained so that generated code implements this project's protocol exactly, rather than generic socket boilerplate. The blueprint and FSM specification are the contract. An AI tool is treated as a junior developer who must implement that contract and nothing else.

---

## 1. Constraint Strategy

Six rules govern every AI-assisted coding session.

1. **Spec first, always.** The full text of `protocol_blueprint.md`, and `fsm_specification.md` when relevant, is pasted into the session before any request. The model is told the documents are authoritative and that it may not invent anything they do not define.
2. **One function or module per request.** Prompts ask for a single, named unit with a fixed signature, never "write the server" or "write a game." Small requests are easier to verify line by line.
3. **Closed vocabularies.** Message types, field names, error codes, and state names are listed explicitly in the prompt. Anything outside those lists must be rejected by the generated code.
4. **Standard library only.** No third-party packages. Only `socket`, `json`, `threading`, `selectors`, `enum`, `time`, `logging`, and `unittest` are allowed unless a later sprint approves otherwise.
5. **Tests are part of the deliverable.** Every request also asks for `unittest` cases that exercise the exact wire examples and error codes from the blueprint.
6. **Human review before commit.** Generated code is checked against the review checklist in Section 4. Code that fails any item is corrected or regenerated, not committed.

---

## 2. Master System Prompt

This prompt opens every session. The specification documents are pasted where indicated.

```text
You are implementing one component of a two-player networked Tic-Tac-Toe game
in Python 3.12 for a computer networks course. The attached documents
protocol_blueprint.md and fsm_specification.md are the complete and
authoritative specification. Follow them exactly.

Hard constraints:
- Transport is TCP. Framing is newline-delimited JSON: each message is one
  compact JSON object (json.dumps with separators=(",", ":")) encoded as
  UTF-8 and followed by exactly one b"\n". Senders use sendall().
- Receivers keep a persistent per-connection bytearray buffer, append every
  recv() chunk, and extract frames only on b"\n". Never assume one recv()
  equals one message. Handle coalesced and fragmented frames.
- If the buffer exceeds 4096 bytes with no newline, raise FrameTooLargeError.
- recv() returning b"" means EOF. Check it on every call.
- Every message has exactly four top-level keys: msg_type, player_id,
  payload, timestamp. Reject any frame with missing or extra keys.
- The only valid msg_type values are: CONNECT, LOBBY_WAIT, GAME_START, MOVE,
  STATE_UPDATE, ERROR, DISCONNECT, GAME_OVER.
- The only valid ERROR codes are: MALFORMED_MESSAGE, UNKNOWN_MSG_TYPE,
  NOT_IN_GAME, PLAYER_MISMATCH, OUT_OF_TURN, INVALID_COORDINATES,
  CELL_OCCUPIED, INVALID_ALIAS, ALREADY_CONNECTED, GAME_FULL,
  MESSAGE_TOO_LARGE.
- The only valid server states are: INIT, WAITING_FOR_PLAYERS, GAME_START,
  PLAYER_TURN, EVALUATE_MOVE, GAME_OVER, CLEANUP.
- Use only the Python standard library.

Do not:
- Add message types, fields, error codes, or states not in the specification.
- Use pickle, eval, or any serialization other than json.
- Write generic echo-server or chat-server scaffolding.
- Use print() for diagnostics; use the logging module.
- Swallow exceptions silently. ConnectionResetError, BrokenPipeError,
  ConnectionAbortedError, and socket.timeout must be converted into a
  CLIENT_DISCONNECTED event.

If the specification is ambiguous or silent on something you need, stop and
ask a question instead of guessing. Produce only the code requested, with
type hints and docstrings, followed by unittest test cases.
```

---

## 3. Task Prompts

Each task prompt is sent after the master prompt and names exactly one unit of work.

### 3.1 Framing Layer

```text
Write module protocol/framing.py containing exactly:

1. encode_message(msg: dict) -> bytes
   Serialize with json.dumps(msg, separators=(",", ":"), ensure_ascii=False),
   encode as UTF-8, append b"\n". Do not validate here.

2. class FrameReader:
   __init__(self, max_frame_bytes: int = 4096)
   feed(self, chunk: bytes) -> list[bytes]
     Append chunk to an internal bytearray. Return every complete frame
     (without its b"\n") in arrival order. Keep any trailing partial bytes.
     Raise FrameTooLargeError if the remaining buffer exceeds
     max_frame_bytes with no newline.

3. class FrameTooLargeError(Exception)

FrameReader must not touch sockets. Socket I/O lives elsewhere.

Tests must cover: two frames in one chunk, one frame split across three
chunks, a chunk ending exactly on b"\n", an empty chunk, and the
4096-byte overflow case. Use the wire examples from section 1.4 of
protocol_blueprint.md as test data.
```

### 3.2 Message Validation

```text
Write module protocol/messages.py containing:

1. An Enum MsgType with exactly the eight message types.
2. An Enum ErrorCode with exactly the eleven error codes.
3. class ProtocolError(Exception) carrying an ErrorCode and a detail string.
4. decode_and_validate(frame: bytes, sender_is_client: bool) -> dict
   Apply the checks in section 6 of protocol_blueprint.md in that exact
   order and raise ProtocolError with the code for the first failure.
   When sender_is_client is True, only CONNECT, MOVE, and DISCONNECT are
   allowed; any other valid type raises UNKNOWN_MSG_TYPE.
   For MOVE, row and col must be int and not bool, in range 0..2.
   For CONNECT, alias must match ^[A-Za-z0-9_]{1,16}$.
5. Builder functions, one per server message type, that return dicts
   matching section 3 of the blueprint exactly, for example
   make_error(code: ErrorCode, detail: str, fatal: bool) -> dict.

Do not check game state here (turn order, occupied cells). That belongs
to the FSM.

Tests: one passing case per message type using the blueprint samples, and
one failing case per error code that this module can raise.
```

### 3.3 Game State Machine

```text
Write module server/game_fsm.py containing class GameFSM that implements
fsm_specification.md. It holds the game context from section 2 and exposes:

  handle_connect(conn_id: str, msg: dict) -> list[Outbound]
  handle_move(conn_id: str, msg: dict) -> list[Outbound]
  handle_disconnect(conn_id: str) -> list[Outbound]

Outbound is a dataclass (conn_id: str, message: dict, close_after: bool).
GameFSM never touches sockets. It returns the messages to send and the
caller performs the I/O.

Every transition T1 through T19 in section 4 of the specification must
correspond to identifiable code. Add a comment with the transition ID
(for example "# T14") at each one. Implement move checks in the order in
section 5, and check for a win before checking for a draw.

Tests: one test per transition ID, plus each scenario in the section 6
edge-case table.
```

---

## 4. Review Checklist for Generated Code

Generated code is rejected and regenerated if any answer is "no."

| Check | What to look for |
|---|---|
| Framing | Frames are split only on `b"\n"` from a persistent buffer. No code assumes one `recv()` is one message. |
| EOF | Every `recv()` result is checked for `b""`. |
| Exceptions | `ConnectionResetError`, `BrokenPipeError`, `ConnectionAbortedError`, and timeouts all lead to `CLIENT_DISCONNECTED`. None are caught with a bare `except:`. |
| Vocabulary | Every message type, field name, error code, and state name appears in the specification. Nothing extra. |
| Envelope | Exactly four top-level keys on every outgoing message. |
| Validation order | Matches section 6 of the blueprint and section 5 of the FSM spec. |
| Separation | Framing, validation, and game logic are separate modules. The FSM performs no socket I/O. |
| Dependencies | Standard library only. |
| Tests | Present, runnable with `python3 -m unittest`, and they use the blueprint's sample frames. |

---

## 5. Example Correction Loop

The most likely failure from an unconstrained AI tool is a receiver that calls `sock.recv(4096)` once and passes the result straight to `json.loads()`. That code works on a quiet local link and fails as soon as two messages coalesce or one fragments, which is exactly the boundary problem the framing rule exists to solve. It would fail the "Framing" row of the checklist.

If generated code shows this pattern, the follow-up prompt is:

```text
Your receiver assumes one recv() returns exactly one JSON message. That
violates section 1.3 of protocol_blueprint.md. Rewrite it using a
persistent bytearray buffer that accumulates chunks and extracts frames
only at b"\n", keeping any trailing partial frame for the next call. Add
tests for two frames in one chunk and one frame split across two chunks.
```

The pattern is the same for any failure: name the violated section of the specification, state the required behavior, and require a test that proves the fix.

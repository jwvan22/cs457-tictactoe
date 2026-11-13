"""Message vocabulary, validation, and builders.

Implements sections 2, 3, and 6 of protocol_blueprint.md. Validation
here covers structure only: the envelope, the message type, and each
payload's shape. Game-state rules such as turn order and occupied cells
belong to the FSM (fsm_specification.md section 5).
"""

import json
import re
import time
from enum import Enum

SERVER_ID = "SERVER"
ENVELOPE_FIELDS = frozenset({"msg_type", "player_id", "payload", "timestamp"})
ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,16}$")
MAX_REASON_LENGTH = 64


class MsgType(str, Enum):
    """The eight message types defined in blueprint section 3.1."""

    CONNECT = "CONNECT"
    LOBBY_WAIT = "LOBBY_WAIT"
    GAME_START = "GAME_START"
    MOVE = "MOVE"
    STATE_UPDATE = "STATE_UPDATE"
    ERROR = "ERROR"
    DISCONNECT = "DISCONNECT"
    GAME_OVER = "GAME_OVER"


CLIENT_TYPES = frozenset({MsgType.CONNECT, MsgType.MOVE, MsgType.DISCONNECT})


class ErrorCode(str, Enum):
    """The eleven error codes defined in blueprint section 3.7."""

    MALFORMED_MESSAGE = "MALFORMED_MESSAGE"
    UNKNOWN_MSG_TYPE = "UNKNOWN_MSG_TYPE"
    NOT_IN_GAME = "NOT_IN_GAME"
    PLAYER_MISMATCH = "PLAYER_MISMATCH"
    OUT_OF_TURN = "OUT_OF_TURN"
    INVALID_COORDINATES = "INVALID_COORDINATES"
    CELL_OCCUPIED = "CELL_OCCUPIED"
    INVALID_ALIAS = "INVALID_ALIAS"
    ALREADY_CONNECTED = "ALREADY_CONNECTED"
    GAME_FULL = "GAME_FULL"
    MESSAGE_TOO_LARGE = "MESSAGE_TOO_LARGE"


class ProtocolError(Exception):
    """A rejected frame, carrying the error code to send back."""

    def __init__(self, code: ErrorCode, detail: str) -> None:
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.detail = detail


def _is_int(value: object) -> bool:
    """True for real integers. bool is a subclass of int, so exclude it."""
    return isinstance(value, int) and not isinstance(value, bool)


# ---------------------------------------------------------------------------
# Validation (blueprint section 6, checks 1 through 5)
# ---------------------------------------------------------------------------

def decode_and_validate(frame: bytes, sender_is_client: bool) -> dict:
    """Decode one frame and validate it, raising ProtocolError on failure.

    Checks run in the order of blueprint section 6 and stop at the first
    failure. When sender_is_client is True, only CONNECT, MOVE, and
    DISCONNECT are accepted and their payloads are checked. When False
    (the client reading server frames), any of the eight types passes.
    """
    malformed = ErrorCode.MALFORMED_MESSAGE

    # Check 1: UTF-8.
    try:
        text = frame.decode("utf-8")
    except UnicodeDecodeError:
        raise ProtocolError(malformed, "Frame is not valid UTF-8") from None

    # Check 2: JSON object.
    try:
        msg = json.loads(text)
    except json.JSONDecodeError:
        raise ProtocolError(malformed, "Frame is not valid JSON") from None
    if not isinstance(msg, dict):
        raise ProtocolError(malformed, "Frame is not a JSON object")

    # Check 3: exactly the four envelope fields, with the right types.
    keys = set(msg)
    if keys != ENVELOPE_FIELDS:
        missing = sorted(ENVELOPE_FIELDS - keys)
        extra = sorted(keys - ENVELOPE_FIELDS)
        raise ProtocolError(
            malformed,
            f"Envelope fields wrong (missing {missing}, extra {extra})",
        )
    if not isinstance(msg["msg_type"], str):
        raise ProtocolError(malformed, "msg_type must be a string")
    if msg["player_id"] is not None and not isinstance(msg["player_id"], str):
        raise ProtocolError(malformed, "player_id must be a string or null")
    if not isinstance(msg["payload"], dict):
        raise ProtocolError(malformed, "payload must be an object")
    if not _is_int(msg["timestamp"]):
        raise ProtocolError(malformed, "timestamp must be an integer")

    # Check 4: a known type that this sender is allowed to send.
    try:
        msg_type = MsgType(msg["msg_type"])
    except ValueError:
        raise ProtocolError(
            ErrorCode.UNKNOWN_MSG_TYPE, f"Unknown msg_type {msg['msg_type']!r}"
        ) from None
    if sender_is_client and msg_type not in CLIENT_TYPES:
        raise ProtocolError(
            ErrorCode.UNKNOWN_MSG_TYPE,
            f"{msg_type.value} may only be sent by the server",
        )

    # Check 5: payload schema for client message types.
    if sender_is_client:
        _validate_client_payload(msg_type, msg["payload"])
    return msg


def _validate_client_payload(msg_type: MsgType, payload: dict) -> None:
    """Check payload shape for CONNECT, MOVE, and DISCONNECT."""
    malformed = ErrorCode.MALFORMED_MESSAGE

    if msg_type is MsgType.CONNECT:
        if set(payload) - {"alias"}:
            raise ProtocolError(malformed, "CONNECT payload has extra fields")
        alias = payload.get("alias")
        if not isinstance(alias, str) or not ALIAS_PATTERN.match(alias):
            raise ProtocolError(
                ErrorCode.INVALID_ALIAS,
                "Alias must be 1 to 16 letters, digits, or underscores",
            )

    elif msg_type is MsgType.MOVE:
        # Coordinate values are checked by the FSM after PLAYER_MISMATCH
        # and OUT_OF_TURN, per blueprint 3.5 and FSM spec section 5.
        if set(payload) - {"row", "col"}:
            raise ProtocolError(malformed, "MOVE payload has extra fields")

    elif msg_type is MsgType.DISCONNECT:
        if set(payload) != {"reason"}:
            raise ProtocolError(malformed, "DISCONNECT payload needs reason")
        reason = payload["reason"]
        if not isinstance(reason, str) or len(reason) > MAX_REASON_LENGTH:
            raise ProtocolError(
                malformed, "reason must be a string of 0 to 64 characters"
            )


def valid_coordinate(value: object) -> bool:
    """True if value is an integer 0, 1, or 2 (booleans rejected)."""
    return _is_int(value) and 0 <= value <= 2


# ---------------------------------------------------------------------------
# Builders (blueprint section 3)
# ---------------------------------------------------------------------------

def _envelope(msg_type: MsgType, player_id: str | None, payload: dict) -> dict:
    """Wrap a payload in the four-field envelope from blueprint section 2."""
    return {
        "msg_type": msg_type.value,
        "player_id": player_id,
        "payload": payload,
        "timestamp": int(time.time()),
    }


# Client to server

def make_connect(alias: str) -> dict:
    """CONNECT (3.2). player_id is null because no ID is assigned yet."""
    return _envelope(MsgType.CONNECT, None, {"alias": alias})


def make_move(player_id: str, row: int, col: int) -> dict:
    """MOVE (3.5)."""
    return _envelope(MsgType.MOVE, player_id, {"row": row, "col": col})


def make_disconnect(player_id: str | None, reason: str = "") -> dict:
    """DISCONNECT (3.8)."""
    return _envelope(
        MsgType.DISCONNECT, player_id, {"reason": reason[:MAX_REASON_LENGTH]}
    )


# Server to client

def make_lobby_wait(message: str = "Waiting for an opponent") -> dict:
    """LOBBY_WAIT (3.3). Only sent while one player is seated."""
    return _envelope(
        MsgType.LOBBY_WAIT, SERVER_ID,
        {"players_connected": 1, "message": message},
    )


def make_game_start(your_id: str, your_mark: str, opponent_alias: str,
                    first_turn: str, round_number: int,
                    scores: dict) -> dict:
    """GAME_START (3.4), one personalized copy per player."""
    return _envelope(MsgType.GAME_START, SERVER_ID, {
        "your_id": your_id,
        "your_mark": your_mark,
        "opponent_alias": opponent_alias,
        "first_turn": first_turn,
        "round": round_number,
        "scores": dict(scores),
    })


def make_state_update(board: list, active_player: str | None,
                      move_number: int, last_move: dict | None,
                      round_number: int, scores: dict) -> dict:
    """STATE_UPDATE (3.6), broadcast identically to both players."""
    return _envelope(MsgType.STATE_UPDATE, SERVER_ID, {
        "board": [list(row) for row in board],
        "active_player": active_player,
        "move_number": move_number,
        "last_move": dict(last_move) if last_move else None,
        "round": round_number,
        "scores": dict(scores),
    })


def make_error(code: ErrorCode, detail: str, fatal: bool = False) -> dict:
    """ERROR (3.7), sent only to the client whose message was rejected."""
    return _envelope(MsgType.ERROR, SERVER_ID, {
        "code": code.value,
        "detail": detail,
        "fatal": fatal,
    })


def make_game_over(result: str, winner: str | None,
                   winning_line: list | None, final_board: list,
                   reason: str, round_number: int, scores: dict,
                   series_continues: bool) -> dict:
    """GAME_OVER (3.9)."""
    return _envelope(MsgType.GAME_OVER, SERVER_ID, {
        "result": result,
        "winner": winner,
        "winning_line": [list(cell) for cell in winning_line]
        if winning_line else None,
        "final_board": [list(row) for row in final_board],
        "reason": reason,
        "round": round_number,
        "scores": dict(scores),
        "series_continues": series_continues,
    })

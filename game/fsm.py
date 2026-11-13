"""Server-side game state machine (docs/fsm_specification.md).

GameFSM owns the single game context and never touches sockets. Each
handler returns a list of Outbound items that tell the event loop what
to send and which connections to close. Because the selectors loop calls
these handlers one at a time, no locking is needed (concurrency_design.md
section 4.2).

Each transition from the specification's table is marked with its ID,
for example "# T14". Three transitions live in server.py instead,
because they happen before a message reaches the FSM: T2 (bind failure),
and the bad-frame halves of T5, T11, and T23, where a frame that fails
protocol validation gets an ERROR and the game state is untouched.
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from game import board as rules
from protocol.messages import (
    ErrorCode, make_error, make_game_over, make_game_start, make_lobby_wait,
    make_state_update, valid_coordinate,
)

log = logging.getLogger(__name__)

PLAYER_1 = "Player_1"
PLAYER_2 = "Player_2"
TURN_LIMIT_SECONDS = 120.0
INTERMISSION_SECONDS = 3.0


class State(Enum):
    """The seven server states from fsm_specification.md section 3."""

    INIT = "INIT"
    WAITING_FOR_PLAYERS = "WAITING_FOR_PLAYERS"
    GAME_START = "GAME_START"
    PLAYER_TURN = "PLAYER_TURN"
    EVALUATE_MOVE = "EVALUATE_MOVE"
    GAME_OVER = "GAME_OVER"
    CLEANUP = "CLEANUP"


@dataclass
class Outbound:
    """One action for the event loop.

    If message is set, it is queued for conn_id. If close_after is True,
    the connection is closed once its outbound buffer drains. A message
    of None with close_after True means close without sending anything.
    """

    conn_id: str
    message: dict | None
    close_after: bool = False


def _other(player_id: str) -> str:
    """Return the opponent's player ID."""
    return PLAYER_2 if player_id == PLAYER_1 else PLAYER_1


class GameFSM:
    """Tic-Tac-Toe series state machine for exactly two seated players."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Create the context in INIT. clock is injectable for tests."""
        self._clock = clock
        self.state = State.INIT
        self._reset_context()

    # ------------------------------------------------------------------
    # Context helpers
    # ------------------------------------------------------------------

    def _reset_context(self) -> None:
        """Set every context variable to its initial value (spec 2)."""
        self.seats: dict[str, str] = {}          # player_id -> conn_id
        self.aliases: dict[str, str] = {}        # player_id -> alias
        self.board = rules.new_board()
        self.active_player: str | None = None
        self.move_number = 0
        self.pending_move: dict | None = None
        self.round = 1
        self.scores = {PLAYER_1: 0, PLAYER_2: 0, "draws": 0}
        self.marks = {PLAYER_1: "X", PLAYER_2: "O"}
        self.result: str | None = None
        self.last_move: dict | None = None
        self.next_round_at: float | None = None
        self.turn_deadline: float | None = None

    def _set_state(self, new_state: State, transition: str) -> None:
        """Change state and log the transition for the execution log."""
        if new_state is not self.state:
            log.info("FSM %s -> %s (%s)", self.state.value,
                     new_state.value, transition)
        else:
            log.info("FSM %s stays (%s)", self.state.value, transition)
        self.state = new_state

    def player_for(self, conn_id: str) -> str | None:
        """Return the player ID seated on conn_id, or None."""
        for player_id, seat_conn in self.seats.items():
            if seat_conn == conn_id:
                return player_id
        return None

    def next_deadline(self) -> float | None:
        """Earliest pending timer, so the event loop knows how long to wait."""
        deadlines = [d for d in (self.turn_deadline, self.next_round_at)
                     if d is not None]
        return min(deadlines) if deadlines else None

    def _error(self, conn_id: str, code: ErrorCode, detail: str,
               fatal: bool = False) -> Outbound:
        """Build an ERROR for one connection."""
        log.info("ERROR %s to %s: %s", code.value, conn_id, detail)
        return Outbound(conn_id, make_error(code, detail, fatal), fatal)

    def _broadcast(self, message: dict,
                   close_after: bool = False) -> list[Outbound]:
        """Send an identical message to both seated players."""
        return [Outbound(conn_id, message, close_after)
                for conn_id in self.seats.values()]

    def _state_update(self) -> dict:
        """Build a STATE_UPDATE from the current context."""
        return make_state_update(self.board, self.active_player,
                                 self.move_number, self.last_move,
                                 self.round, self.scores)

    # ------------------------------------------------------------------
    # INIT
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Called by the server once bind() and listen() succeed.

        T2 (bind failure) is handled in server.py, which logs the error
        and exits with a nonzero status before the FSM is started.
        """
        if self.state is State.INIT:
            self._set_state(State.WAITING_FOR_PLAYERS, "T1")   # T1

    # ------------------------------------------------------------------
    # CONNECT
    # ------------------------------------------------------------------

    def handle_connect(self, conn_id: str, msg: dict) -> list[Outbound]:
        """Handle a structurally valid CONNECT (alias already checked)."""
        alias = msg["payload"]["alias"]

        if self.player_for(conn_id) is not None:
            # T4: the same connection is already seated.
            return [self._error(conn_id, ErrorCode.ALREADY_CONNECTED,
                                "This connection has already joined")]

        if self.state is State.WAITING_FOR_PLAYERS:
            if PLAYER_1 not in self.seats:
                # T3: first player takes seat 1 and waits.
                self.seats[PLAYER_1] = conn_id
                self.aliases[PLAYER_1] = alias
                self._set_state(State.WAITING_FOR_PLAYERS, "T3")
                log.info("%s seated as %s (%s)", conn_id, PLAYER_1, alias)
                return [Outbound(conn_id, make_lobby_wait())]

            # T7: second player fills the game.
            self.seats[PLAYER_2] = conn_id
            self.aliases[PLAYER_2] = alias
            log.info("%s seated as %s (%s)", conn_id, PLAYER_2, alias)
            self._set_state(State.GAME_START, "T7")
            return self._start_round(self._clock())

        # T12 (PLAYER_TURN) and T23 (GAME_OVER): both seats are taken.
        transition = "T12" if self.state is State.PLAYER_TURN else "T23"
        log.info("Rejecting %s, game full (%s)", conn_id, transition)
        return [self._error(conn_id, ErrorCode.GAME_FULL,
                            "Two players are already seated", fatal=True)]

    def _start_round(self, now: float) -> list[Outbound]:
        """GAME_START entry actions, then move to PLAYER_TURN."""
        # T8: personalized GAME_START, then the empty board to both.
        first = PLAYER_1 if self.marks[PLAYER_1] == "X" else PLAYER_2
        out = []
        for player_id, conn_id in self.seats.items():
            out.append(Outbound(conn_id, make_game_start(
                your_id=player_id,
                your_mark=self.marks[player_id],
                opponent_alias=self.aliases[_other(player_id)],
                first_turn=first,
                round_number=self.round,
                scores=self.scores,
            )))
        self.active_player = first
        self.turn_deadline = now + TURN_LIMIT_SECONDS
        out.extend(self._broadcast(self._state_update()))
        self._set_state(State.PLAYER_TURN, "T8")
        # T9: the FSM performs no I/O, so a send failure or drop during
        # setup reaches us later through handle_disconnect() while in
        # PLAYER_TURN, and the forfeit path below handles it.
        return out

    # ------------------------------------------------------------------
    # MOVE
    # ------------------------------------------------------------------

    def handle_move(self, conn_id: str, msg: dict) -> list[Outbound]:
        """Handle a structurally valid MOVE."""
        player_id = self.player_for(conn_id)

        if self.state is not State.PLAYER_TURN or player_id is None:
            # T5 (lobby), T23 (intermission), or an unseated connection.
            return [self._error(conn_id, ErrorCode.NOT_IN_GAME,
                                "No game is in progress for you")]

        self.pending_move = msg                                   # T10
        self._set_state(State.EVALUATE_MOVE, "T10")
        try:
            return self._evaluate_move(conn_id, player_id)
        finally:
            self.pending_move = None

    def _evaluate_move(self, conn_id: str,
                       player_id: str) -> list[Outbound]:
        """Apply the move checks in spec section 5 order, then score."""
        msg = self.pending_move
        payload = msg["payload"]
        row, col = payload.get("row"), payload.get("col")

        # T14: checks 1 through 4, stop at the first failure.
        failure = None
        if msg["player_id"] != player_id:
            failure = (ErrorCode.PLAYER_MISMATCH,
                       f"This connection is {player_id}")
        elif player_id != self.active_player:
            failure = (ErrorCode.OUT_OF_TURN,
                       f"It is {self.active_player}'s turn")
        elif not (valid_coordinate(row) and valid_coordinate(col)):
            failure = (ErrorCode.INVALID_COORDINATES,
                       "row and col must be integers from 0 to 2")
        elif not rules.is_empty(self.board, row, col):
            failure = (ErrorCode.CELL_OCCUPIED,
                       f"Cell ({row}, {col}) is already taken")
        if failure:
            self._set_state(State.PLAYER_TURN, "T14")
            return [self._error(conn_id, *failure)]

        mark = self.marks[player_id]
        rules.place(self.board, row, col, mark)
        self.move_number += 1
        self.last_move = {"player_id": player_id, "row": row, "col": col}
        log.info("%s (%s) played row %d col %d", player_id, mark, row, col)

        line = rules.winning_line(self.board, mark)
        if line:
            # T16: win is checked before draw.
            self.scores[player_id] += 1
            self.result = "WIN"
            self._set_state(State.GAME_OVER, "T16")
            return self._finish_round(
                winner=player_id, line=line,
                reason=f"{player_id} completed a line")

        if rules.is_full(self.board):
            # T17: board full with no line.
            self.scores["draws"] += 1
            self.result = "DRAW"
            self._set_state(State.GAME_OVER, "T17")
            return self._finish_round(winner=None, line=None,
                                      reason="Board is full")

        # T15: play continues.
        self.active_player = _other(player_id)
        self.turn_deadline = self._clock() + TURN_LIMIT_SECONDS
        self._set_state(State.PLAYER_TURN, "T15")
        return self._broadcast(self._state_update())

    def _finish_round(self, winner: str | None, line: list | None,
                      reason: str) -> list[Outbound]:
        """T18: GAME_OVER entry for a WIN or DRAW, start intermission."""
        self.active_player = None
        self.turn_deadline = None
        self.next_round_at = self._clock() + INTERMISSION_SECONDS
        out = self._broadcast(self._state_update())
        out.extend(self._broadcast(make_game_over(
            result=self.result, winner=winner, winning_line=line,
            final_board=self.board, reason=reason,
            round_number=self.round, scores=self.scores,
            series_continues=True,
        )))
        self._set_state(State.GAME_OVER, "T18")
        return out

    # ------------------------------------------------------------------
    # Disconnects (DISCONNECT, EOF, RST, keepalive, frame overflow)
    # ------------------------------------------------------------------

    def handle_disconnect(self, conn_id: str,
                          reason: str | None = None) -> list[Outbound]:
        """React to CLIENT_DISCONNECTED for any connection.

        The event loop closes conn_id itself. Unseated connections and
        connections from an already finished series need no action.
        """
        player_id = self.player_for(conn_id)
        if player_id is None:
            return []
        reason = reason or f"{player_id} disconnected"

        if self.state is State.WAITING_FOR_PLAYERS:
            # T6: lone lobby player leaves, free the seat.
            del self.seats[player_id]
            self.aliases.pop(player_id, None)
            self._set_state(State.WAITING_FOR_PLAYERS, "T6")
            return []

        if self.state is State.PLAYER_TURN:
            # T13: forfeit, opponent gets the point.
            winner = _other(player_id)
            self.scores[winner] += 1
            self.result = "FORFEIT"
            self._set_state(State.GAME_OVER, "T13")
            return self._end_series(winner, reason, "T20")

        if self.state is State.GAME_OVER:
            # T22: leaving during intermission, no score change.
            self.result = "FORFEIT"
            return self._end_series(_other(player_id), reason, "T22")

        return []

    def _end_series(self, winner: str, reason: str,
                    transition: str) -> list[Outbound]:
        """T20/T22: final GAME_OVER to the remaining player, then CLEANUP."""
        self.active_player = None
        self.turn_deadline = None
        self.next_round_at = None
        out = [Outbound(self.seats[winner], make_game_over(
            result="FORFEIT", winner=winner, winning_line=None,
            final_board=self.board, reason=reason,
            round_number=self.round, scores=self.scores,
            series_continues=False,
        ), close_after=True)]
        self._set_state(State.CLEANUP, transition)
        self._cleanup()
        return out

    def _cleanup(self) -> None:
        """T19: reset the context and reopen the lobby.

        Socket closing is done by the event loop: the leaving player's
        socket is already closing, and the remaining player's GAME_OVER
        is marked close_after.
        """
        log.info("Series over, final scores %s", self.scores)
        self._reset_context()
        self._set_state(State.WAITING_FOR_PLAYERS, "T19")

    # ------------------------------------------------------------------
    # Timers
    # ------------------------------------------------------------------

    def handle_tick(self, now: float) -> list[Outbound]:
        """Fire any due timers: intermission end or turn limit."""
        if (self.state is State.GAME_OVER and self.next_round_at is not None
                and now >= self.next_round_at
                and len(self.seats) == 2):
            # T21: next round, swap marks so the first move alternates.
            self.board = rules.new_board()
            self.move_number = 0
            self.last_move = None
            self.result = None
            self.next_round_at = None
            self.marks = {PLAYER_1: self.marks[PLAYER_2],
                          PLAYER_2: self.marks[PLAYER_1]}
            self.round += 1
            self._set_state(State.GAME_START, "T21")
            return self._start_round(now)

        if (self.state is State.PLAYER_TURN
                and self.turn_deadline is not None
                and now >= self.turn_deadline):
            # T13 via turn timeout: treated as a disconnect of the
            # active player. The timed-out player is still connected,
            # so they get a copy of the final GAME_OVER explaining why
            # the series ended, and their socket closes after it.
            loser = self.active_player
            loser_conn = self.seats[loser]
            log.info("%s exceeded the %d second turn limit", loser,
                     int(TURN_LIMIT_SECONDS))
            out = self.handle_disconnect(
                loser_conn,
                reason=f"{loser} did not move within "
                       f"{int(TURN_LIMIT_SECONDS)} seconds")
            out.append(Outbound(loser_conn, out[0].message,
                                close_after=True))
            return out

        return []

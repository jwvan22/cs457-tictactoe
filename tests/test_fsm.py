"""Tests for game/fsm.py: one test per transition plus spec 6 edge cases.

A fake clock drives the turn and intermission timers, so no test sleeps
and no sockets are used.
"""

import unittest

from game.fsm import (
    INTERMISSION_SECONDS, PLAYER_1, PLAYER_2, TURN_LIMIT_SECONDS, GameFSM,
    State,
)
from protocol.messages import make_connect, make_disconnect, make_move

C1, C2, C3 = "c1", "c2", "c3"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def types(out) -> list:
    """(conn_id, msg_type) pairs, or (conn_id, None) for close-only."""
    return [(o.conn_id, o.message["msg_type"] if o.message else None)
            for o in out]


def payloads(out, msg_type: str) -> list:
    return [o.message["payload"] for o in out
            if o.message and o.message["msg_type"] == msg_type]


class FSMTestBase(unittest.TestCase):

    def setUp(self) -> None:
        self.clock = FakeClock()
        self.fsm = GameFSM(clock=self.clock)
        self.fsm.start()

    def seat_both(self):
        self.fsm.handle_connect(C1, make_connect("Alice"))
        return self.fsm.handle_connect(C2, make_connect("Bob"))

    def move(self, conn, player, row, col):
        return self.fsm.handle_move(conn, make_move(player, row, col))

    def play(self, moves):
        """moves: list of (conn, player, row, col). Returns last output."""
        out = []
        for m in moves:
            out = self.move(*m)
        return out

    def p1_wins_top_row(self):
        """Round 1: Player_1 (X) takes the top row."""
        return self.play([
            (C1, PLAYER_1, 0, 0), (C2, PLAYER_2, 1, 0),
            (C1, PLAYER_1, 0, 1), (C2, PLAYER_2, 1, 1),
            (C1, PLAYER_1, 0, 2),
        ])


class TransitionTests(FSMTestBase):

    def test_T1_start_enters_lobby(self):
        fsm = GameFSM(clock=self.clock)
        self.assertIs(fsm.state, State.INIT)
        fsm.start()
        self.assertIs(fsm.state, State.WAITING_FOR_PLAYERS)

    def test_T3_first_connect_gets_lobby_wait(self):
        out = self.fsm.handle_connect(C1, make_connect("Alice"))
        self.assertEqual(types(out), [(C1, "LOBBY_WAIT")])
        self.assertEqual(self.fsm.seats, {PLAYER_1: C1})
        self.assertIs(self.fsm.state, State.WAITING_FOR_PLAYERS)

    def test_T4_second_connect_same_conn(self):
        self.fsm.handle_connect(C1, make_connect("Alice"))
        out = self.fsm.handle_connect(C1, make_connect("Alice"))
        self.assertEqual(payloads(out, "ERROR")[0]["code"],
                         "ALREADY_CONNECTED")
        self.assertIs(self.fsm.state, State.WAITING_FOR_PLAYERS)

    def test_T5_move_in_lobby(self):
        self.fsm.handle_connect(C1, make_connect("Alice"))
        out = self.move(C1, PLAYER_1, 0, 0)
        self.assertEqual(payloads(out, "ERROR")[0]["code"], "NOT_IN_GAME")

    def test_T6_lobby_player_leaves(self):
        self.fsm.handle_connect(C1, make_connect("Alice"))
        self.assertEqual(self.fsm.handle_disconnect(C1), [])
        self.assertEqual(self.fsm.seats, {})
        out = self.fsm.handle_connect(C2, make_connect("Bob"))
        self.assertEqual(types(out), [(C2, "LOBBY_WAIT")])

    def test_T7_T8_second_connect_starts_round(self):
        out = self.seat_both()
        self.assertEqual(types(out), [
            (C1, "GAME_START"), (C2, "GAME_START"),
            (C1, "STATE_UPDATE"), (C2, "STATE_UPDATE"),
        ])
        p1, p2 = payloads(out, "GAME_START")
        self.assertEqual((p1["your_id"], p1["your_mark"],
                          p1["opponent_alias"]), (PLAYER_1, "X", "Bob"))
        self.assertEqual((p2["your_id"], p2["your_mark"],
                          p2["opponent_alias"]), (PLAYER_2, "O", "Alice"))
        self.assertEqual(p1["first_turn"], PLAYER_1)
        state = payloads(out, "STATE_UPDATE")[0]
        self.assertEqual(state["move_number"], 0)
        self.assertIsNone(state["last_move"])
        self.assertEqual(state["active_player"], PLAYER_1)
        self.assertIs(self.fsm.state, State.PLAYER_TURN)

    def test_T9_drop_right_after_start_is_forfeit(self):
        self.seat_both()
        out = self.fsm.handle_disconnect(C2)
        over = payloads(out, "GAME_OVER")[0]
        self.assertEqual((over["result"], over["winner"]),
                         ("FORFEIT", PLAYER_1))

    def test_T10_T15_valid_move_switches_turn(self):
        self.seat_both()
        out = self.move(C1, PLAYER_1, 0, 2)
        self.assertEqual(types(out),
                         [(C1, "STATE_UPDATE"), (C2, "STATE_UPDATE")])
        state = payloads(out, "STATE_UPDATE")[0]
        self.assertEqual(state["board"][0][2], "X")
        self.assertEqual(state["active_player"], PLAYER_2)
        self.assertEqual(state["move_number"], 1)
        self.assertEqual(state["last_move"],
                         {"player_id": PLAYER_1, "row": 0, "col": 2})
        self.assertIsNone(self.fsm.pending_move)
        self.assertIs(self.fsm.state, State.PLAYER_TURN)

    def test_T12_third_client_gets_fatal_game_full(self):
        self.seat_both()
        out = self.fsm.handle_connect(C3, make_connect("Carol"))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].conn_id, C3)
        self.assertTrue(out[0].close_after)
        err = out[0].message["payload"]
        self.assertEqual((err["code"], err["fatal"]), ("GAME_FULL", True))
        self.assertIs(self.fsm.state, State.PLAYER_TURN)

    def test_T13_T20_T19_disconnect_mid_game(self):
        self.seat_both()
        self.move(C1, PLAYER_1, 0, 2)
        out = self.fsm.handle_disconnect(C2)
        self.assertEqual(types(out), [(C1, "GAME_OVER")])
        self.assertTrue(out[0].close_after)
        over = out[0].message["payload"]
        self.assertEqual(over["result"], "FORFEIT")
        self.assertEqual(over["winner"], PLAYER_1)
        self.assertFalse(over["series_continues"])
        self.assertEqual(over["scores"][PLAYER_1], 1)
        self.assertEqual(over["final_board"][0][2], "X")
        # T19: context reset, lobby open again.
        self.assertIs(self.fsm.state, State.WAITING_FOR_PLAYERS)
        self.assertEqual(self.fsm.seats, {})
        self.assertEqual(self.fsm.scores[PLAYER_1], 0)
        self.assertEqual(self.fsm.round, 1)

    def test_T14_rejections_keep_turn(self):
        self.seat_both()
        self.move(C1, PLAYER_1, 1, 1)
        cases = [
            (C2, PLAYER_1, 0, 0, "PLAYER_MISMATCH"),
            (C1, PLAYER_1, 0, 0, "OUT_OF_TURN"),
            (C2, PLAYER_2, 3, 0, "INVALID_COORDINATES"),
            (C2, PLAYER_2, 1, 1, "CELL_OCCUPIED"),
        ]
        for conn, pid, row, col, code in cases:
            out = self.move(conn, pid, row, col)
            self.assertEqual(types(out), [(conn, "ERROR")], code)
            self.assertEqual(out[0].message["payload"]["code"], code)
            self.assertEqual(self.fsm.active_player, PLAYER_2)
            self.assertEqual(self.fsm.move_number, 1)
            self.assertIs(self.fsm.state, State.PLAYER_TURN)

    def test_T16_T18_win(self):
        self.seat_both()
        out = self.p1_wins_top_row()
        self.assertEqual(types(out), [
            (C1, "STATE_UPDATE"), (C2, "STATE_UPDATE"),
            (C1, "GAME_OVER"), (C2, "GAME_OVER"),
        ])
        self.assertIsNone(payloads(out, "STATE_UPDATE")[0]["active_player"])
        over = payloads(out, "GAME_OVER")[0]
        self.assertEqual(over["result"], "WIN")
        self.assertEqual(over["winner"], PLAYER_1)
        self.assertEqual(over["winning_line"], [[0, 0], [0, 1], [0, 2]])
        self.assertTrue(over["series_continues"])
        self.assertEqual(over["scores"],
                         {PLAYER_1: 1, PLAYER_2: 0, "draws": 0})
        self.assertFalse(any(o.close_after for o in out))
        self.assertIs(self.fsm.state, State.GAME_OVER)
        self.assertEqual(self.fsm.next_round_at,
                         self.clock.now + INTERMISSION_SECONDS)

    def test_T17_draw(self):
        self.seat_both()
        # X O X / X O O / O X X
        out = self.play([
            (C1, PLAYER_1, 0, 0), (C2, PLAYER_2, 0, 1),
            (C1, PLAYER_1, 0, 2), (C2, PLAYER_2, 1, 1),
            (C1, PLAYER_1, 1, 0), (C2, PLAYER_2, 1, 2),
            (C1, PLAYER_1, 2, 1), (C2, PLAYER_2, 2, 0),
            (C1, PLAYER_1, 2, 2),
        ])
        over = payloads(out, "GAME_OVER")[0]
        self.assertEqual((over["result"], over["winner"]), ("DRAW", None))
        self.assertIsNone(over["winning_line"])
        self.assertEqual(over["scores"]["draws"], 1)

    def test_T21_next_round_swaps_marks(self):
        self.seat_both()
        self.p1_wins_top_row()
        self.assertEqual(self.fsm.handle_tick(self.clock.now + 1), [])
        out = self.fsm.handle_tick(self.clock.now + INTERMISSION_SECONDS)
        p1, p2 = payloads(out, "GAME_START")
        self.assertEqual(p1["round"], 2)
        self.assertEqual((p1["your_mark"], p2["your_mark"]), ("O", "X"))
        self.assertEqual(p1["first_turn"], PLAYER_2)
        self.assertEqual(p1["scores"][PLAYER_1], 1)
        state = payloads(out, "STATE_UPDATE")[0]
        self.assertEqual(state["active_player"], PLAYER_2)
        self.assertEqual(state["move_number"], 0)
        self.assertIs(self.fsm.state, State.PLAYER_TURN)
        # Player_2 now holds X and moves first.
        out = self.move(C2, PLAYER_2, 1, 1)
        self.assertEqual(payloads(out, "STATE_UPDATE")[0]["board"][1][1],
                         "X")

    def test_T22_leave_during_intermission(self):
        self.seat_both()
        self.p1_wins_top_row()
        out = self.fsm.handle_disconnect(C1)
        self.assertEqual(types(out), [(C2, "GAME_OVER")])
        over = out[0].message["payload"]
        self.assertEqual(over["result"], "FORFEIT")
        self.assertEqual(over["winner"], PLAYER_2)
        self.assertFalse(over["series_continues"])
        # Round 1 was already scored; no extra point.
        self.assertEqual(over["scores"],
                         {PLAYER_1: 1, PLAYER_2: 0, "draws": 0})
        self.assertIs(self.fsm.state, State.WAITING_FOR_PLAYERS)

    def test_T23_move_and_connect_during_intermission(self):
        self.seat_both()
        self.p1_wins_top_row()
        out = self.move(C2, PLAYER_2, 2, 2)
        self.assertEqual(payloads(out, "ERROR")[0]["code"], "NOT_IN_GAME")
        out = self.fsm.handle_connect(C3, make_connect("Carol"))
        self.assertEqual(payloads(out, "ERROR")[0]["code"], "GAME_FULL")
        self.assertTrue(out[0].close_after)
        self.assertIs(self.fsm.state, State.GAME_OVER)
        self.assertEqual(self.fsm.scores[PLAYER_1], 1)


class EdgeCaseTests(FSMTestBase):
    """fsm_specification.md section 6 scenarios not covered above."""

    def test_turn_timeout_forfeits_and_closes_loser(self):
        self.seat_both()
        out = self.fsm.handle_tick(self.clock.now + TURN_LIMIT_SECONDS)
        self.assertEqual(types(out), [(C2, "GAME_OVER"), (C1, "GAME_OVER")])
        self.assertTrue(all(o.close_after for o in out))
        over = out[0].message["payload"]
        self.assertEqual((over["result"], over["winner"]),
                         ("FORFEIT", PLAYER_2))
        self.assertIn("120 seconds", over["reason"])
        self.assertIs(self.fsm.state, State.WAITING_FOR_PLAYERS)

    def test_turn_timer_restarts_after_each_move(self):
        self.seat_both()
        self.clock.now += 100
        self.move(C1, PLAYER_1, 0, 0)
        self.assertEqual(self.fsm.handle_tick(self.clock.now + 100), [])
        self.assertEqual(self.fsm.turn_deadline,
                         self.clock.now + TURN_LIMIT_SECONDS)

    def test_rejected_move_does_not_reset_timer(self):
        self.seat_both()
        deadline = self.fsm.turn_deadline
        self.clock.now += 50
        self.move(C1, PLAYER_1, 5, 5)
        self.assertEqual(self.fsm.turn_deadline, deadline)

    def test_both_players_drop(self):
        self.seat_both()
        self.fsm.handle_disconnect(C1)
        self.assertEqual(self.fsm.handle_disconnect(C2), [])
        self.assertIs(self.fsm.state, State.WAITING_FOR_PLAYERS)

    def test_graceful_disconnect_then_eof_ignored(self):
        self.seat_both()
        msg = make_disconnect(PLAYER_2, "user quit")
        self.assertEqual(msg["msg_type"], "DISCONNECT")
        first = self.fsm.handle_disconnect(C2)
        self.assertEqual(len(first), 1)
        self.assertEqual(self.fsm.handle_disconnect(C2), [])

    def test_unseated_connection_disconnect_is_noop(self):
        self.seat_both()
        self.assertEqual(self.fsm.handle_disconnect(C3), [])
        self.assertIs(self.fsm.state, State.PLAYER_TURN)

    def test_ninth_move_win_beats_draw(self):
        self.seat_both()
        # Final board X O X / O X O / O X X, last move (2,2) wins.
        out = self.play([
            (C1, PLAYER_1, 0, 0), (C2, PLAYER_2, 0, 1),
            (C1, PLAYER_1, 0, 2), (C2, PLAYER_2, 1, 0),
            (C1, PLAYER_1, 1, 1), (C2, PLAYER_2, 1, 2),
            (C1, PLAYER_1, 2, 1), (C2, PLAYER_2, 2, 0),
            (C1, PLAYER_1, 2, 2),
        ])
        over = payloads(out, "GAME_OVER")[0]
        self.assertEqual(over["result"], "WIN")
        self.assertEqual(self.fsm.scores["draws"], 0)

    def test_bool_coordinates_rejected(self):
        self.seat_both()
        msg = make_move(PLAYER_1, 0, 0)
        msg["payload"] = {"row": True, "col": 0}
        out = self.fsm.handle_move(C1, msg)
        self.assertEqual(payloads(out, "ERROR")[0]["code"],
                         "INVALID_COORDINATES")

    def test_missing_coordinates_rejected(self):
        self.seat_both()
        msg = make_move(PLAYER_1, 0, 0)
        msg["payload"] = {}
        out = self.fsm.handle_move(C1, msg)
        self.assertEqual(payloads(out, "ERROR")[0]["code"],
                         "INVALID_COORDINATES")

    def test_new_series_after_cleanup(self):
        self.seat_both()
        self.fsm.handle_disconnect(C2)
        self.fsm.handle_connect("c4", make_connect("Dan"))
        out = self.fsm.handle_connect("c5", make_connect("Eve"))
        p1 = payloads(out, "GAME_START")[0]
        self.assertEqual((p1["your_id"], p1["round"]), (PLAYER_1, 1))
        self.assertEqual(p1["scores"],
                         {PLAYER_1: 0, PLAYER_2: 0, "draws": 0})

    def test_scores_accumulate_over_rounds(self):
        self.seat_both()
        self.p1_wins_top_row()
        self.fsm.handle_tick(self.clock.now + INTERMISSION_SECONDS)
        # Round 2: Player_2 holds X and wins the left column.
        out = self.play([
            (C2, PLAYER_2, 0, 0), (C1, PLAYER_1, 0, 1),
            (C2, PLAYER_2, 1, 0), (C1, PLAYER_1, 1, 1),
            (C2, PLAYER_2, 2, 0),
        ])
        over = payloads(out, "GAME_OVER")[0]
        self.assertEqual(over["round"], 2)
        self.assertEqual(over["scores"],
                         {PLAYER_1: 1, PLAYER_2: 1, "draws": 0})


if __name__ == "__main__":
    unittest.main()

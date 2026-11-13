"""Tests for game/board.py."""

import unittest

from game.board import (
    EMPTY, WIN_LINES, is_empty, is_full, new_board, place, winning_line,
)


def board_from(rows: list[str]) -> list[list[str]]:
    """Build a board from three 3-character strings, e.g. 'XO-'."""
    return [list(row) for row in rows]


class BoardTests(unittest.TestCase):

    def test_new_board_is_empty(self):
        board = new_board()
        self.assertEqual(board, [[EMPTY] * 3 for _ in range(3)])
        self.assertFalse(is_full(board))

    def test_rows_are_independent(self):
        board = new_board()
        board[0][0] = "X"
        self.assertEqual(board[1][0], EMPTY)

    def test_place_and_occupied(self):
        board = new_board()
        place(board, 0, 2, "X")
        self.assertFalse(is_empty(board, 0, 2))
        with self.assertRaises(ValueError):
            place(board, 0, 2, "O")

    def test_eight_lines(self):
        self.assertEqual(len(WIN_LINES), 8)

    def test_every_line_wins(self):
        for line in WIN_LINES:
            board = new_board()
            for r, c in line:
                board[r][c] = "O"
            self.assertEqual(winning_line(board, "O"),
                             [[r, c] for r, c in line])
            self.assertIsNone(winning_line(board, "X"))

    def test_blueprint_diagonal_example(self):
        board = board_from(["XO-", "OX-", "--X"])
        self.assertEqual(winning_line(board, "X"),
                         [[0, 0], [1, 1], [2, 2]])

    def test_full_board_draw(self):
        board = board_from(["XOX", "XOO", "OXX"])
        self.assertTrue(is_full(board))
        self.assertIsNone(winning_line(board, "X"))
        self.assertIsNone(winning_line(board, "O"))

    def test_ninth_move_win_is_still_a_win(self):
        board = board_from(["XOX", "OXO", "OX-"])
        place(board, 2, 2, "X")
        self.assertTrue(is_full(board))
        self.assertIsNotNone(winning_line(board, "X"))


if __name__ == "__main__":
    unittest.main()

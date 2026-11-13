"""Tic-Tac-Toe board rules with no network code.

The board is a 3x3 list of rows. Each cell holds "X", "O", or "-" for
empty, matching blueprint section 2. Rows and columns are zero-indexed.
"""

EMPTY = "-"
SIZE = 3

# The 8 winning lines: 3 rows, 3 columns, 2 diagonals.
WIN_LINES = (
    ((0, 0), (0, 1), (0, 2)),
    ((1, 0), (1, 1), (1, 2)),
    ((2, 0), (2, 1), (2, 2)),
    ((0, 0), (1, 0), (2, 0)),
    ((0, 1), (1, 1), (2, 1)),
    ((0, 2), (1, 2), (2, 2)),
    ((0, 0), (1, 1), (2, 2)),
    ((0, 2), (1, 1), (2, 0)),
)


def new_board() -> list[list[str]]:
    """Return an empty 3x3 board."""
    return [[EMPTY] * SIZE for _ in range(SIZE)]


def is_empty(board: list[list[str]], row: int, col: int) -> bool:
    """True if the cell holds no mark."""
    return board[row][col] == EMPTY


def place(board: list[list[str]], row: int, col: int, mark: str) -> None:
    """Put a mark in an empty cell. The caller validates first."""
    if not is_empty(board, row, col):
        raise ValueError(f"Cell ({row}, {col}) is occupied")
    board[row][col] = mark


def winning_line(board: list[list[str]], mark: str) -> list | None:
    """Return the three [row, col] pairs of a completed line, or None."""
    for line in WIN_LINES:
        if all(board[r][c] == mark for r, c in line):
            return [[r, c] for r, c in line]
    return None


def is_full(board: list[list[str]]) -> bool:
    """True if no empty cells remain."""
    return all(cell != EMPTY for row in board for cell in row)

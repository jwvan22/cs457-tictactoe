# CS 457 Final Project

Two-player networked Tic-Tac-Toe over TCP, written in Python 3.

- Game: 3x3 Tic-Tac-Toe, turn based, two players
- Language: Python 3 (standard library only)
- Target server domain: server.vanallen.edu
- Statement of Work: see sow_template.md

## Structure
- `game/` core game rules, no network code
- `server.py` authoritative game server
- `client.py` player client
- `tests/` unit tests for game logic

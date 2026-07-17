"""Python wrapper for the interactive chessboard component (board_component/).

A real drag-and-drop + tap board (Lichess/chess.com feel) rendered by a small
self-contained HTML/JS component. It returns the move the user made:
  {"from": "e2", "to": "e4", "promotion": None, "id": n}   (a move)
  {"tap": "e4", "id": n}                                    (edit paint tap)
`id` increments per interaction so repeat moves still register; the caller
dedupes on it.

In game_mode, the component uses chess.js to handle moves locally — no server
round-trip per move. Moves accumulate in JS and are flushed to Python on demand
(when flush=True is passed). The component returns:
  {"moves": ["e4", "d5", ...], "fen": "...", "id": n}
"""

from __future__ import annotations

import os

import chess
import streamlit.components.v1 as components

_DIR = os.path.join(os.path.dirname(__file__), "board_component")
_component = components.declare_component("chess_board", path=_DIR)


def legal_dests(board: chess.Board) -> dict[str, list[str]]:
    """from-square -> [legal to-squares], in square-name form."""
    dests: dict[str, list[str]] = {}
    for mv in board.legal_moves:
        dests.setdefault(chess.square_name(mv.from_square), []).append(
            chess.square_name(mv.to_square)
        )
    return dests


def last_move_squares(board: chess.Board) -> list[str] | None:
    if not board.move_stack:
        return None
    mv = board.peek()
    return [chess.square_name(mv.from_square), chess.square_name(mv.to_square)]


def show_board(
    fen: str,
    *,
    dests: dict | None = None,
    orientation: str = "white",
    last_move: list[str] | None = None,
    free: bool = False,
    brush: str = "move",
    game_mode: bool = False,
    flush: bool = False,
    undo_count: int = 0,
    key: str,
):
    return _component(
        fen=fen,
        dests=dests or {},
        orientation=orientation,
        last_move=last_move,
        free=free,
        brush=brush,
        game_mode=game_mode,
        flush=flush,
        undo_count=undo_count,
        key=key,
        default=None,
    )

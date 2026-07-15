"""Interactive tap board for Streamlit.

Two entry points:
- edit_board(): free placement for correcting the detected position on the
  confirm screen (palette to add pieces, erase, or relocate by tap-from/tap-to).
- move_board(): chess.com-style tap-a-piece then tap-a-destination to play a
  legal move during the game.

Tap rather than HTML5 drag: drag needs a bespoke JS component that is fragile
on mobile and the free host, whereas tapping is fully native Streamlit and
rock-solid on a phone. The board displays as a crisp SVG (real piece colours,
selected-square + last-move highlights); an 8x8 grid of buttons directly below,
in the same orientation, is the input surface.
"""

from __future__ import annotations

import chess
import chess.svg
import streamlit as st

GLYPHS = {
    "P": "♙", "N": "♘", "B": "♗", "R": "♖", "Q": "♕", "K": "♔",
    "p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚",
}
_EMPTY = "·"

# Streamlit collapses narrow columns onto separate rows on a phone, which would
# stack the 8 files vertically. We wrap each board in a keyed container (which
# Streamlit tags with a `st-key-board_grid_*` class) and scope CSS to it to
# keep every file-row a real 8-wide, tightly-packed row of square buttons.
_BOARD_CSS = """
<style>
div[class*="board_grid"] div[data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    gap: 2px !important;
}
div[class*="board_grid"] div[data-testid="stColumn"] {
    min-width: 0 !important;
    flex: 1 1 0 !important;
}
div[class*="board_grid"] button {
    padding: 0 !important;
    min-height: 42px !important;
    font-size: 1.4rem !important;
    line-height: 1 !important;
}
</style>
"""


def _inject_css():
    st.markdown(_BOARD_CSS, unsafe_allow_html=True)


def _square_iter(flipped: bool):
    """Yield squares in display order: top row first, left to right."""
    ranks = range(8) if flipped else range(7, -1, -1)
    files = range(7, -1, -1) if flipped else range(8)
    for r in ranks:
        yield [chess.square(f, r) for f in files]


def _render_svg(board: chess.Board, selected, flipped: bool, arrow_san: str | None):
    fill = {selected: "#f6f669"} if selected is not None else {}
    arrows = []
    if arrow_san:
        try:
            mv = board.parse_san(arrow_san)
            arrows = [chess.svg.Arrow(mv.from_square, mv.to_square, color="#15781B")]
        except ValueError:
            pass
    lastmove = board.peek() if board.move_stack else None
    svg = chess.svg.board(
        board, fill=fill, arrows=arrows, lastmove=lastmove, flipped=flipped, size=360,
    )
    st.image(svg, width="stretch")


def _make_move(board: chess.Board, frm: int, to: int) -> chess.Move | None:
    """Legal move from->to, auto-queening promotions. None if illegal."""
    piece = board.piece_at(frm)
    promo = None
    if piece and piece.piece_type == chess.PAWN and chess.square_rank(to) in (0, 7):
        promo = chess.QUEEN
    move = chess.Move(frm, to, promotion=promo)
    return move if move in board.legal_moves else None


def move_board(board: chess.Board, flipped: bool, key: str,
               arrow_san: str | None = None) -> chess.Move | None:
    """Render a tap-to-move board. Returns a legal move once the user taps a
    source square (of the side to move) then a destination, else None."""
    _inject_css()
    sel_key = f"{key}_sel"
    selected = st.session_state.get(sel_key)
    _render_svg(board, selected, flipped, arrow_san)

    result = None
    with st.container(key=f"board_grid_{key}"):
        for row_squares in _square_iter(flipped):
            cols = st.columns(8)
            for col, sq in zip(cols, row_squares):
                piece = board.piece_at(sq)
                label = GLYPHS[piece.symbol()] if piece else _EMPTY
                if col.button(label, key=f"{key}_sq_{sq}",
                              type="primary" if selected == sq else "secondary",
                              width="stretch"):
                    if selected is None:
                        if piece and piece.color == board.turn:
                            st.session_state[sel_key] = sq
                            st.rerun()
                    elif sq == selected:
                        st.session_state.pop(sel_key, None)
                        st.rerun()
                    else:
                        move = _make_move(board, selected, sq)
                        st.session_state.pop(sel_key, None)
                        if move is not None:
                            result = move  # applied by caller, which then reruns
                        else:
                            st.rerun()  # illegal: just deselect
    return result


def edit_board(placement_key: str, flipped: bool, key: str) -> None:
    """Render a free-placement editor operating on st.session_state[placement_key]
    (a board-FEN string). Mutations are written straight back to that key."""
    _inject_css()
    board = chess.Board.empty()
    board.set_board_fen(st.session_state[placement_key])
    sel_key = f"{key}_sel"
    brush_key = f"{key}_brush"
    selected = st.session_state.get(sel_key)
    brush = st.session_state.get(brush_key, "move")

    _render_svg(board, selected, flipped, arrow_san=None)

    st.caption("Pick **Move** to relocate a piece, a **piece** to place it, "
               "or **Erase** to clear a square — then tap the board below.")
    tools = ["move", "erase", "P", "N", "B", "R", "Q", "K",
             "p", "n", "b", "r", "q", "k"]
    labels = {"move": "↔", "erase": "⌫"}
    with st.container(key=f"board_grid_{key}_tools"):
        tcols = st.columns(8)
        for i, tool in enumerate(tools):
            label = labels[tool] if tool in labels else GLYPHS[tool]
            if tcols[i % 8].button(label, key=f"{key}_tool_{tool}",
                                   type="primary" if brush == tool else "secondary",
                                   width="stretch"):
                st.session_state[brush_key] = tool
                st.rerun()

    with st.container(key=f"board_grid_{key}"):
        for row_squares in _square_iter(flipped):
            cols = st.columns(8)
            for col, sq in zip(cols, row_squares):
                piece = board.piece_at(sq)
                label = GLYPHS[piece.symbol()] if piece else _EMPTY
                if col.button(label, key=f"{key}_sq_{sq}",
                              type="primary" if selected == sq else "secondary",
                              width="stretch"):
                    if brush == "erase":
                        board.remove_piece_at(sq)
                    elif brush == "move":
                        if selected is None:
                            if piece:
                                st.session_state[sel_key] = sq
                                st.rerun()
                        elif sq == selected:
                            st.session_state.pop(sel_key, None)
                        else:
                            moving = board.piece_at(selected)
                            board.remove_piece_at(selected)
                            if moving:
                                board.set_piece_at(sq, moving)
                            st.session_state.pop(sel_key, None)
                    else:  # a piece brush
                        board.set_piece_at(sq, chess.Piece.from_symbol(brush))
                    st.session_state[placement_key] = board.board_fen()
                    st.rerun()

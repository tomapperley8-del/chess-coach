"""Chess Coach — upload a screenshot, confirm the position, get ELO-aware coaching.

Flow: upload -> vision (CNN) + metadata (Claude) -> confirmation screen ->
optional full-game fetch -> Stockfish -> Claude coaching.
"""

from __future__ import annotations

import os

import chess
import chess.svg
import streamlit as st

import coach
import engine as engine_mod
import fetcher
import vision

st.set_page_config(page_title="Chess Coach", page_icon="♞", layout="centered")

PIECE_CHOICES = {
    "Empty": None,
    "White pawn ♙": "P", "White knight ♘": "N", "White bishop ♗": "B",
    "White rook ♖": "R", "White queen ♕": "Q", "White king ♔": "K",
    "Black pawn ♟": "p", "Black knight ♞": "n", "Black bishop ♝": "b",
    "Black rook ♜": "r", "Black queen ♛": "q", "Black king ♚": "k",
}


def _api_key() -> str | None:
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY", None)
    except Exception:  # no secrets.toml in local dev
        key = None
    return key or st.session_state.get("api_key_input") or None


def _default_castling(board: chess.Board) -> str:
    rights = ""
    if board.piece_at(chess.E1) == chess.Piece.from_symbol("K"):
        if board.piece_at(chess.H1) == chess.Piece.from_symbol("R"):
            rights += "K"
        if board.piece_at(chess.A1) == chess.Piece.from_symbol("R"):
            rights += "Q"
    if board.piece_at(chess.E8) == chess.Piece.from_symbol("k"):
        if board.piece_at(chess.H8) == chess.Piece.from_symbol("r"):
            rights += "k"
        if board.piece_at(chess.A8) == chess.Piece.from_symbol("r"):
            rights += "q"
    return rights or "-"


def _board_from_state() -> chess.Board:
    placement = st.session_state.placement
    turn = "w" if st.session_state.turn_white else "b"
    board = chess.Board.empty()
    board.set_board_fen(placement)
    castling = _default_castling(board)
    board.set_fen(f"{placement} {turn} {castling} - 0 1")
    return board


def _reset():
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def _render_board(board: chess.Board, arrow_san: str | None = None, flipped: bool = False):
    arrows = []
    if arrow_san:
        try:
            move = board.parse_san(arrow_san)
            arrows = [chess.svg.Arrow(move.from_square, move.to_square, color="#15781B")]
        except ValueError:
            pass
    svg = chess.svg.board(board, arrows=arrows, flipped=flipped, size=380)
    st.image(svg, width="stretch")


st.title("♞ Chess Coach")

stage = st.session_state.get("stage", "upload")

# ---------------------------------------------------------------------------
# Stage 1: upload
# ---------------------------------------------------------------------------
if stage == "upload":
    st.caption("Upload a screenshot of your game and get coaching for your level.")
    uploaded = st.file_uploader("Board screenshot", type=["png", "jpg", "jpeg", "webp"])
    flipped = st.toggle("I'm playing Black (board is upside-down)", value=False)

    if not _api_key():
        st.text_input(
            "Anthropic API key (no key found in secrets)",
            type="password", key="api_key_input",
        )

    sample = os.path.join(os.path.dirname(__file__), "test_images", "fake_screenshot.png")
    use_sample = (
        os.path.exists(sample)
        and not uploaded
        and st.button("Try with a sample screenshot")
    )

    if use_sample or (uploaded and st.button("Read the board", type="primary", width="stretch")):
        if use_sample:
            with open(sample, "rb") as f:
                data = f.read()
            media_type = "image/png"
        else:
            data = uploaded.getvalue()
            media_type = uploaded.type or "image/png"
        with st.spinner("Finding the board and reading the pieces..."):
            try:
                image = vision.image_from_bytes(data)
                placement, board_img = vision.screenshot_to_placement(image, flipped=flipped)
            except Exception as exc:  # surface, don't crash: user can retry
                st.error(f"Couldn't read the board: {exc}")
                st.stop()
        st.session_state.placement = placement
        st.session_state.flipped = flipped
        st.session_state.board_img = board_img
        st.session_state.turn_white = not flipped  # crude default: it's your move
        meta = {}
        key = _api_key()
        if key:
            with st.spinner("Reading names and ratings..."):
                try:
                    meta = coach.extract_metadata(key, data, media_type)
                except Exception:
                    meta = {}
        st.session_state.meta = meta
        st.session_state.stage = "confirm"
        st.rerun()

# ---------------------------------------------------------------------------
# Stage 2: confirm the detected position
# ---------------------------------------------------------------------------
elif stage == "confirm":
    st.subheader("Does this match your screenshot?")
    board = _board_from_state()
    _render_board(board, flipped=st.session_state.flipped)

    meta = st.session_state.get("meta", {})
    col1, col2 = st.columns(2)
    with col1:
        my_elo = st.number_input(
            "Your rating", 100, 3200,
            int(meta.get("bottom_rating") or 800), step=50,
        )
    with col2:
        opp_elo = st.number_input(
            "Opponent rating", 100, 3200,
            int(meta.get("top_rating") or 800), step=50,
        )
    st.session_state.turn_white = (
        st.radio("Whose move is it?", ["White", "Black"], horizontal=True,
                 index=0 if st.session_state.turn_white else 1)
        == "White"
    )

    with st.expander("Fix a square"):
        sq_col, piece_col = st.columns(2)
        with sq_col:
            square = st.selectbox("Square", [chess.square_name(s) for s in chess.SQUARES])
        with piece_col:
            piece_label = st.selectbox("Should be", list(PIECE_CHOICES))
        if st.button("Apply fix", width="stretch"):
            b = chess.Board.empty()
            b.set_board_fen(st.session_state.placement)
            sym = PIECE_CHOICES[piece_label]
            sq = chess.parse_square(square)
            b.set_piece_at(sq, chess.Piece.from_symbol(sym) if sym else None)
            st.session_state.placement = b.board_fen()
            st.rerun()
        st.caption("Or edit the FEN directly:")
        fen_edit = st.text_input("FEN placement", st.session_state.placement)
        if fen_edit != st.session_state.placement:
            try:
                b = chess.Board.empty()
                b.set_board_fen(fen_edit)
                st.session_state.placement = fen_edit
                st.rerun()
            except ValueError:
                st.error("That's not a valid FEN placement.")

    if not board.is_valid():
        st.warning("This position isn't legal yet (check kings/pawns). Fix it above.")
    else:
        if st.button("Looks right — coach me", type="primary", width="stretch"):
            st.session_state.my_elo = my_elo
            st.session_state.opp_elo = opp_elo
            st.session_state.stage = "analyse"
            st.rerun()

    if st.button("Start over"):
        _reset()
        st.rerun()

# ---------------------------------------------------------------------------
# Stage 3: engine + coaching
# ---------------------------------------------------------------------------
elif stage == "analyse":
    board = _board_from_state()
    fen = board.fen()
    meta = st.session_state.get("meta", {})
    my_elo = st.session_state.my_elo

    # Try to fetch the full game so we can review it, not just the snapshot.
    game = st.session_state.get("game")
    if game is None and "game_lookup_done" not in st.session_state:
        usernames = [meta.get("bottom_username"), meta.get("top_username")]
        if any(usernames):
            with st.spinner("Looking for this game online..."):
                game = fetcher.find_game(
                    board.board_fen(), usernames, meta.get("site"),
                )
        st.session_state.game = game
        st.session_state.game_lookup_done = True

    review = st.session_state.get("review")
    if game and review is None:
        bar = st.progress(0.0, "Reviewing the whole game with Stockfish...")
        try:
            review = engine_mod.sweep_game(
                game["moves_san"][: game["ply_of_screenshot"]],
                progress=lambda f: bar.progress(f),
            )
        except RuntimeError as exc:
            st.error(str(exc))
            st.stop()
        bar.empty()
        st.session_state.review = review

    if "analysis" not in st.session_state:
        with st.spinner("Analysing the position with Stockfish..."):
            try:
                st.session_state.analysis = engine_mod.analyse_position(fen, elo=my_elo)
            except RuntimeError as exc:
                st.error(str(exc))
                st.stop()
    analysis = st.session_state.analysis

    recommended = analysis.human_move_san or (analysis.best.move_san if analysis.best else None)
    _render_board(board, arrow_san=recommended, flipped=st.session_state.flipped)
    st.caption(f"Engine eval: {analysis.eval_text()}")
    if game:
        st.caption(f"Found the game: {game['white']} vs {game['black']} ({game['site']})")

    if "coaching" not in st.session_state:
        key = _api_key()
        if not key:
            st.error("No Anthropic API key.")
            st.text_input("Anthropic API key", type="password", key="api_key_input")
            if st.button("Use this key", type="primary"):
                st.rerun()
            st.stop()
        with st.spinner("Your coach is thinking..."):
            try:
                st.session_state.coaching = coach.get_coaching(
                    key, analysis, my_elo,
                    opponent_elo=st.session_state.opp_elo,
                    game_review=review,
                )
            except Exception as exc:
                st.error(f"Coaching call failed: {exc}")
                st.stop()

    st.markdown(st.session_state.coaching)

    with st.expander("Raw engine lines"):
        for i, line in enumerate(analysis.lines):
            score = (
                f"mate in {line.mate_in}" if line.mate_in is not None
                else f"{(line.score_cp or 0) / 100:+.2f}"
            )
            st.text(f"{i + 1}. {line.move_san} ({score})  {' '.join(line.pv_san)}")

    if st.button("Analyse another position", type="primary", width="stretch"):
        _reset()
        st.rerun()

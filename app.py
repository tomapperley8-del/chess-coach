"""Chess Coach — upload a screenshot, confirm the position, then track the
game live: type each move as it's played, get ELO-aware coaching, and export
the PGN at any time.

Flow: upload -> vision (CNN) + metadata (Claude) -> confirmation screen ->
live game (Stockfish + Claude coaching, PGN saved after every move).
The online game lookup (chess.com / Lichess) is opt-in; everything else
works with no external service except the coaching call itself, and even
without an Anthropic key the engine analysis and PGN export still work.
"""

from __future__ import annotations

import os

import chess
import chess.svg
import streamlit as st

import auth
import coach
import engine as engine_mod
import fetcher
import gamestore
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


def _board_from_confirm() -> chess.Board:
    placement = st.session_state.placement
    turn = "w" if st.session_state.turn_white else "b"
    board = chess.Board.empty()
    board.set_board_fen(placement)
    castling = _default_castling(board)
    board.set_fen(f"{placement} {turn} {castling} - 0 1")
    return board


def _current_board() -> chess.Board:
    board = gamestore.base_board(st.session_state.start_fen)
    for san in st.session_state.prefix + st.session_state.appended:
        board.push_san(san)
    return board


def _reset():
    for key in list(st.session_state.keys()):
        if key != "user":
            del st.session_state[key]


def _render_board(board: chess.Board, arrow_san: str | None = None, flipped: bool = False):
    arrows = []
    if arrow_san:
        try:
            move = board.parse_san(arrow_san)
            arrows = [chess.svg.Arrow(move.from_square, move.to_square, color="#15781B")]
        except ValueError:
            pass
    lastmove = board.peek() if board.move_stack else None
    svg = chess.svg.board(board, arrows=arrows, lastmove=lastmove, flipped=flipped, size=380)
    st.image(svg, width="stretch")


def _pgn_notes() -> dict[int, str]:
    notes = {}
    for entry in st.session_state.get("review", []):
        loss = entry["loss"]
        if loss >= 250:
            notes[entry["ply"]] = f"Blunder - lost {loss / 100:.1f} pawns."
        elif loss >= 120:
            notes[entry["ply"]] = f"Mistake - lost {loss / 100:.1f} pawns."
    return notes


def _current_pgn() -> str:
    return gamestore.build_pgn(
        st.session_state.start_fen,
        st.session_state.prefix,
        st.session_state.appended,
        st.session_state.headers,
        notes=_pgn_notes(),
    )


def _save():
    try:
        gamestore.save_game(st.session_state.user, st.session_state.game_id, _current_pgn())
    except OSError:
        pass  # read-only filesystem is fine; download still works


def _start_game(start_fen, prefix, headers, review):
    st.session_state.start_fen = start_fen
    st.session_state.prefix = prefix
    st.session_state.appended = []
    st.session_state.headers = headers
    st.session_state.review = review
    st.session_state.evals = (
        {e["ply"]: e["eval_after"] for e in review} | {0: review[0]["eval_before"]}
        if review else {}
    )
    st.session_state.reviewed = {e["ply"] for e in review}
    st.session_state.analyses = {}
    st.session_state.coachings = {}
    st.session_state.game_id = gamestore.new_game_id(headers.get("white"), headers.get("black"))
    st.session_state.stage = "game"
    _save()


def _resume_saved(path: str) -> bool:
    """Load a saved PGN into a fresh live-game session. Returns False if the
    file couldn't be read."""
    loaded = gamestore.load_game(path)
    if loaded is None:
        return False
    st.session_state.start_fen = loaded["start_fen"]
    st.session_state.prefix = loaded["prefix"]
    st.session_state.appended = []
    st.session_state.headers = loaded["headers"]
    st.session_state.review = []
    st.session_state.evals = {}
    st.session_state.reviewed = set()
    st.session_state.analyses = {}
    st.session_state.coachings = {}
    st.session_state.game_id = loaded["game_id"]
    h = loaded["headers"]
    student_white = h.get("student_side", "white") == "white"
    st.session_state.student_white = student_white
    my = h.get("whiteelo") if student_white else h.get("blackelo")
    opp = h.get("blackelo") if student_white else h.get("whiteelo")
    st.session_state.my_elo = int(my) if my and str(my).isdigit() else 800
    st.session_state.opp_elo = int(opp) if opp and str(opp).isdigit() else 800
    st.session_state.flipped = not student_white
    st.session_state.stage = "game"
    return True


username = auth.require_login()

title_col, logout_col = st.columns([4, 1])
with title_col:
    st.title("♞ Chess Coach")
    st.caption(f"Signed in as **{username}**")
with logout_col:
    auth.logout_button()

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
            "Anthropic API key (optional — needed for coaching text)",
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

    saved = gamestore.list_saved(username)
    if saved:
        st.divider()
        st.caption("Jump straight back into a game:")
        # Quick-select: one tap resumes the most recent games.
        for path in saved[:6]:
            name = os.path.splitext(os.path.basename(path))[0]
            if st.button(f"▶  {name}", key=f"quick_{path}", width="stretch"):
                if _resume_saved(path):
                    st.rerun()
                else:
                    st.error("Couldn't read that saved game.")

        with st.expander("All saved games / rename"):
            choice = st.selectbox(
                "Saved games", saved,
                format_func=lambda p: os.path.splitext(os.path.basename(p))[0],
            )
            if st.button("Resume this game", key="resume_full", width="stretch"):
                if _resume_saved(choice):
                    st.rerun()
                else:
                    st.error("Couldn't read that saved game.")
            current_name = os.path.splitext(os.path.basename(choice))[0]
            new_name = st.text_input("Rename to", value=current_name, key="rename_saved_input")
            if st.button("Rename", key="rename_saved_btn"):
                new_name = new_name.strip()
                if new_name and new_name != current_name:
                    try:
                        gamestore.rename_game(username, current_name, new_name)
                        st.rerun()
                    except FileExistsError as exc:
                        st.error(str(exc))

# ---------------------------------------------------------------------------
# Stage 2: confirm the detected position
# ---------------------------------------------------------------------------
elif stage == "confirm":
    st.subheader("Does this match your screenshot?")
    board = _board_from_confirm()
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
    student_white = (
        st.radio("Which side are you?", ["White", "Black"], horizontal=True,
                 index=1 if st.session_state.flipped else 0)
        == "White"
    )
    lookup = st.checkbox(
        "Look this game up online (chess.com / Lichess) to review earlier moves",
        value=False,
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
        if st.button("Looks right — start coaching", type="primary", width="stretch"):
            st.session_state.my_elo = my_elo
            st.session_state.opp_elo = opp_elo
            st.session_state.student_white = student_white
            st.session_state.flipped = not student_white

            headers = {
                "white": meta.get("bottom_username") if meta.get("bottom_is_white") else meta.get("top_username"),
                "black": meta.get("top_username") if meta.get("bottom_is_white") else meta.get("bottom_username"),
                "site": meta.get("site") or "chess-coach app",
                "student_side": "white" if student_white else "black",
                "whiteelo": my_elo if student_white else opp_elo,
                "blackelo": opp_elo if student_white else my_elo,
            }
            if not headers["white"] and not headers["black"]:
                headers["white"] = "Me" if student_white else "Opponent"
                headers["black"] = "Opponent" if student_white else "Me"

            start_fen, prefix, review = board.fen(), [], []
            if lookup:
                usernames = [meta.get("bottom_username"), meta.get("top_username")]
                game = None
                if any(usernames):
                    with st.spinner("Looking for this game online..."):
                        game = fetcher.find_game(board.board_fen(), usernames, meta.get("site"))
                if game:
                    prefix = game["moves_san"][: game["ply_of_screenshot"]]
                    start_fen = None
                    headers["white"] = game["white"] or headers["white"]
                    headers["black"] = game["black"] or headers["black"]
                    st.toast(f"Found it: {game['white']} vs {game['black']}")
                    if prefix:
                        bar = st.progress(0.0, "Reviewing the earlier moves with Stockfish...")
                        try:
                            review = engine_mod.sweep_game(
                                prefix,
                                depth=12 if len(prefix) <= 60 else 8,
                                progress=lambda f: bar.progress(f),
                            )
                        except RuntimeError as exc:
                            st.error(str(exc))
                            st.stop()
                        bar.empty()
                else:
                    st.warning(
                        "Couldn't find the game online — carrying on from the "
                        "screenshot position only."
                    )

            _start_game(start_fen, prefix, headers, review)
            st.rerun()

    if st.button("Start over"):
        _reset()
        st.rerun()

# ---------------------------------------------------------------------------
# Stage 3: live game — engine, coaching, move entry, PGN export
# ---------------------------------------------------------------------------
elif stage == "game":
    board = _current_board()
    fen = board.fen()
    ply = len(st.session_state.prefix) + len(st.session_state.appended)
    student_white = st.session_state.student_white
    my_turn = board.turn == student_white
    game_over = board.is_game_over()
    h = st.session_state.headers

    fast_mode = st.session_state.get("fast_mode", False)

    # --- persistent status line, shown above the board at all times ---
    status_line = st.empty()

    def _run_analysis():
        with st.spinner("Analysing with Stockfish..."):
            try:
                a = engine_mod.analyse_position(fen, elo=st.session_state.my_elo)
            except RuntimeError as exc:
                st.error(str(exc))
                st.stop()
        st.session_state.analyses[fen] = a
        st.session_state.evals[ply] = engine_mod.white_cp(a)
        # If this position follows a live-entered move, log it for the review.
        if (
            st.session_state.appended
            and ply not in st.session_state.reviewed
            and (ply - 1) in st.session_state.evals
        ):
            before = st.session_state.evals[ply - 1]
            after = st.session_state.evals[ply]
            b_prev = board.copy()
            b_prev.pop()
            mover = "white" if b_prev.turn else "black"
            st.session_state.review.append({
                "ply": ply,
                "move_number": b_prev.fullmove_number,
                "mover": mover,
                "san": st.session_state.appended[-1],
                "eval_before": before,
                "eval_after": after,
                "loss": -(after - before) if mover == "white" else (after - before),
                "fen_after": fen,
            })
            st.session_state.reviewed.add(ply)
            _save()  # so blunder notes land in the saved PGN
        return a

    # In fast-entry mode the engine call is skipped so moves can be punched in
    # quickly; analysis (and coaching) then run only when explicitly asked.
    analysis = st.session_state.analyses.get(fen)
    if analysis is None and not game_over and not fast_mode:
        analysis = _run_analysis()

    turn_label = "Your move" if my_turn else "Waiting for opponent's move"
    if game_over:
        status_line.success(f"Game over: {board.result()}")
    else:
        base = f"{h.get('white')} vs {h.get('black')} — move {board.fullmove_number} · {turn_label}"
        status_line.caption(
            base + (f" · Engine eval: {analysis.eval_text()}" if analysis else "")
        )

    # --- automatic complexity flag: warn when a human is likely to go wrong ---
    if analysis and not game_over:
        level, message = engine_mod.assess_complexity(analysis)
        if level == "critical":
            st.warning("⚠️ " + message)
        elif level == "sharp":
            st.info("♟ " + message)

    recommended = None
    if analysis and my_turn and not game_over:
        recommended = analysis.human_move_san or (
            analysis.best.move_san if analysis.best else None
        )
    _render_board(board, arrow_san=recommended, flipped=not student_white)

    if analysis is None and not game_over and fast_mode:
        if st.button("Analyse this position", width="stretch"):
            _run_analysis()
            st.rerun()

    # --- coaching (needs an Anthropic key; everything else works without) ---
    if not game_over and analysis:
        auto_coach = st.checkbox("Coach me automatically on my move", value=True)
        key = _api_key()
        if my_turn and key and (auto_coach or st.button("Coach this position")):
            coaching = st.session_state.coachings.get(fen)
            if coaching is None:
                with st.spinner("Your coach is thinking..."):
                    try:
                        coaching = coach.get_coaching(
                            key, analysis, st.session_state.my_elo,
                            opponent_elo=st.session_state.opp_elo,
                            game_review=st.session_state.review or None,
                        )
                        st.session_state.coachings[fen] = coaching
                    except Exception as exc:
                        st.error(f"Coaching call failed: {exc}")
            if coaching:
                st.markdown(coaching)
        elif my_turn and not key:
            st.info("No Anthropic key — engine analysis and PGN export still work.")
            st.text_input("Anthropic API key", type="password", key="api_key_input")

    # --- move entry ---
    if not game_over:
        st.checkbox(
            "⚡ Fast entry — skip auto-analysis (tap Analyse when you want it)",
            key="fast_mode",
        )
        with st.form("move_form", clear_on_submit=True):
            move_text = st.text_input(
                "Next move(s)",
                placeholder="Type moves and press Enter — one or many: Nf3 d5 e4",
            )
            submitted = st.form_submit_button("Play move(s)", type="primary", width="stretch")
        if submitted and move_text.strip():
            tokens = move_text.replace(",", " ").split()
            b2 = board.copy()
            new_sans = []
            error = None
            for token in tokens:
                try:
                    move = b2.parse_san(token)
                except ValueError:
                    try:
                        move = b2.parse_uci(token.lower())
                    except ValueError:
                        error = f"'{token}' isn't a legal move here."
                        break
                new_sans.append(b2.san(move))
                b2.push(move)
            if error:
                st.error(error)
            else:
                st.session_state.appended.extend(new_sans)
                _save()
                st.rerun()

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Undo last move", width="stretch",
                     disabled=not st.session_state.appended):
            st.session_state.appended.pop()
            _save()
            st.rerun()
    with col_b:
        st.download_button(
            "Download PGN", _current_pgn(),
            file_name=f"{st.session_state.game_id}.pgn",
            mime="application/x-chess-pgn", width="stretch",
        )

    with st.expander("Rename this game"):
        new_id = st.text_input(
            "Name", value=st.session_state.game_id, key="rename_active_input",
        )
        if st.button("Rename", key="rename_active_btn"):
            new_id = new_id.strip()
            if new_id and new_id != st.session_state.game_id:
                try:
                    st.session_state.game_id = gamestore.rename_game(
                        username, st.session_state.game_id, new_id,
                    )
                    st.rerun()
                except FileExistsError as exc:
                    st.error(str(exc))

    with st.expander("Game so far (PGN)"):
        st.code(_current_pgn(), language=None)

    if analysis:
        with st.expander("Raw engine lines"):
            for i, line in enumerate(analysis.lines):
                score = (
                    f"mate in {line.mate_in}" if line.mate_in is not None
                    else f"{(line.score_cp or 0) / 100:+.2f}"
                )
                st.text(f"{i + 1}. {line.move_san} ({score})  {' '.join(line.pv_san)}")

    if not game_over:
        with st.expander("All move rankings — top 20 & worst 10"):
            if st.button("Compute full rankings", key="rank_btn"):
                with st.spinner("Scoring every legal move..."):
                    try:
                        st.session_state[f"ranks::{fen}"] = engine_mod.rank_all_moves(fen)
                    except RuntimeError as exc:
                        st.error(str(exc))
            ranks = st.session_state.get(f"ranks::{fen}")
            if ranks:
                def _fmt_rank(rm):
                    if rm.mate_in is not None:
                        return f"mate in {rm.mate_in}"
                    return f"{(rm.score_cp or 0) / 100:+.2f}"
                st.caption(f"Top {min(20, len(ranks))} moves")
                for i, rm in enumerate(ranks[:20], 1):
                    st.text(f"{i:>2}. {rm.san:<7} {_fmt_rank(rm)}")
                if len(ranks) > 20:
                    st.caption("Worst 10 moves")
                    worst = ranks[-10:]
                    start = len(ranks) - len(worst) + 1
                    for j, rm in enumerate(worst, start):
                        st.text(f"{j:>2}. {rm.san:<7} {_fmt_rank(rm)}")

    if st.button("New game"):
        _reset()
        st.rerun()

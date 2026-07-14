"""Build, save and reload games as PGN.

The PGN is the single source of truth: it's what gets downloaded, what gets
saved to disk after every move, and what a resumed session is rebuilt from.
Games that start from a screenshot mid-game carry the position in the
standard SetUp/FEN headers, so any chess program can open the file.
"""

from __future__ import annotations

import datetime as _dt
import io
import os
import re

import chess
import chess.pgn

SAVE_DIR = os.path.join(os.path.dirname(__file__), "saved_games")


def base_board(start_fen: str | None) -> chess.Board:
    return chess.Board(start_fen) if start_fen else chess.Board()


def build_pgn(
    start_fen: str | None,
    prefix: list[str],
    appended: list[str],
    headers: dict,
    notes: dict[int, str] | None = None,
) -> str:
    """Assemble a PGN string. `prefix` are moves recovered from an online
    lookup (played before the screenshot); `appended` are moves entered live.
    `notes` maps 1-based ply -> comment text."""
    game = chess.pgn.Game()
    game.headers["Event"] = headers.get("event", "Live coaching session")
    game.headers["Site"] = headers.get("site", "chess-coach app")
    game.headers["Date"] = headers.get("date", _dt.date.today().strftime("%Y.%m.%d"))
    game.headers["White"] = headers.get("white") or "White"
    game.headers["Black"] = headers.get("black") or "Black"
    game.headers["Result"] = headers.get("result", "*")
    for tag in ("WhiteElo", "BlackElo"):
        if headers.get(tag.lower()):
            game.headers[tag] = str(headers[tag.lower()])
    if headers.get("student_side"):
        game.headers["StudentSide"] = headers["student_side"]
    if start_fen:
        game.headers["SetUp"] = "1"
        game.headers["FEN"] = start_fen
        game.setup(chess.Board(start_fen))

    node = game
    board = base_board(start_fen)
    notes = notes or {}
    for ply, san in enumerate(prefix + appended, start=1):
        node = node.add_variation(board.parse_san(san))
        if ply in notes:
            node.comment = notes[ply]
        board.push(node.move)

    return str(game)


def save_game(game_id: str, pgn_text: str) -> str:
    os.makedirs(SAVE_DIR, exist_ok=True)
    safe = re.sub(r"[^\w-]", "_", game_id)
    path = os.path.join(SAVE_DIR, f"{safe}.pgn")
    with open(path, "w", encoding="utf-8") as f:
        f.write(pgn_text)
    return path


def list_saved() -> list[str]:
    """Saved game files, newest first."""
    if not os.path.isdir(SAVE_DIR):
        return []
    files = [os.path.join(SAVE_DIR, f) for f in os.listdir(SAVE_DIR) if f.endswith(".pgn")]
    return sorted(files, key=os.path.getmtime, reverse=True)


def load_game(path: str) -> dict | None:
    """Rebuild session state from a saved PGN. All moves become the prefix;
    play continues by appending from the final position."""
    with open(path, encoding="utf-8") as f:
        pgn_text = f.read()
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        return None
    h = game.headers
    start_fen = h.get("FEN") if h.get("SetUp") == "1" else None
    board = base_board(start_fen)
    prefix = []
    for move in game.mainline_moves():
        prefix.append(board.san(move))
        board.push(move)
    return {
        "game_id": os.path.splitext(os.path.basename(path))[0],
        "start_fen": start_fen,
        "prefix": prefix,
        "headers": {
            "event": h.get("Event"),
            "site": h.get("Site"),
            "date": h.get("Date"),
            "white": h.get("White"),
            "black": h.get("Black"),
            "result": h.get("Result", "*"),
            "whiteelo": h.get("WhiteElo"),
            "blackelo": h.get("BlackElo"),
            "student_side": h.get("StudentSide", "white"),
        },
    }


def new_game_id(white: str | None, black: str | None) -> str:
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    if white or black:
        return f"{stamp}_{white or 'White'}_vs_{black or 'Black'}"
    return f"{stamp}_game"

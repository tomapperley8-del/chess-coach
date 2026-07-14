"""Stockfish analysis: engine-best lines plus a human-realistic move.

Finds the Stockfish binary across environments:
- Streamlit Community Cloud installs it via packages.txt -> /usr/games/stockfish
- Local dev can drop a binary in ./stockfish/ or set STOCKFISH_PATH
"""

from __future__ import annotations

import glob
import os
import shutil
from dataclasses import dataclass, field

import chess
import chess.engine

_CANDIDATE_PATHS = [
    os.environ.get("STOCKFISH_PATH", ""),
    "/usr/games/stockfish",
    "/usr/bin/stockfish",
]


def find_stockfish() -> str | None:
    for path in _CANDIDATE_PATHS:
        if path and os.path.isfile(path):
            return path
    local = glob.glob(
        os.path.join(os.path.dirname(__file__), "stockfish", "**", "*stockfish*"),
        recursive=True,
    )
    for path in local:
        if os.path.isfile(path) and (os.access(path, os.X_OK) or path.endswith(".exe")):
            return path
    return shutil.which("stockfish")


@dataclass
class EngineLine:
    move_san: str
    pv_san: list[str]
    score_cp: int | None  # centipawns from side-to-move's perspective
    mate_in: int | None


@dataclass
class Analysis:
    fen: str
    turn: str  # "white" / "black"
    lines: list[EngineLine] = field(default_factory=list)
    human_move_san: str | None = None
    human_elo: int | None = None

    @property
    def best(self) -> EngineLine | None:
        return self.lines[0] if self.lines else None

    def eval_text(self) -> str:
        best = self.best
        if best is None:
            return "no legal moves"
        if best.mate_in is not None:
            side = "side to move" if best.mate_in > 0 else "opponent"
            return f"mate in {abs(best.mate_in)} for the {side}"
        pawns = (best.score_cp or 0) / 100
        sign = "+" if pawns >= 0 else ""
        return f"{sign}{pawns:.2f} pawns for the side to move"


def _line_from_info(board: chess.Board, info: chess.engine.InfoDict) -> EngineLine | None:
    pv = info.get("pv")
    score = info.get("score")
    if not pv or score is None:
        return None
    tmp = board.copy()
    pv_san = []
    for mv in pv[:6]:
        pv_san.append(tmp.san(mv))
        tmp.push(mv)
    pov = score.pov(board.turn)
    return EngineLine(
        move_san=pv_san[0],
        pv_san=pv_san,
        score_cp=pov.score(),
        mate_in=pov.mate(),
    )


def analyse_position(fen: str, elo: int | None = None, depth: int = 16, multipv: int = 10) -> Analysis:
    """Full-strength MultiPV analysis plus an ELO-limited 'human' move."""
    path = find_stockfish()
    if path is None:
        raise RuntimeError(
            "Stockfish binary not found. Set STOCKFISH_PATH or place a binary in ./stockfish/."
        )
    board = chess.Board(fen)
    result = Analysis(fen=fen, turn="white" if board.turn else "black")

    with chess.engine.SimpleEngine.popen_uci(path) as engine:
        infos = engine.analyse(board, chess.engine.Limit(depth=depth), multipv=multipv)
        if isinstance(infos, dict):
            infos = [infos]
        for info in infos:
            line = _line_from_info(board, info)
            if line:
                result.lines.append(line)

        if elo:
            # Stockfish's UCI_Elo floor is 1320; approximate below that with
            # Skill Level (0-20), which trades strength for plausible errors.
            engine.configure({"UCI_LimitStrength": False})  # reset
            if elo >= 1320:
                engine.configure({"UCI_LimitStrength": True, "UCI_Elo": min(elo, 3190)})
            else:
                skill = max(0, int(elo / 1320 * 8))
                engine.configure({"Skill Level": skill})
            played = engine.play(board, chess.engine.Limit(time=1.0))
            if played.move:
                result.human_move_san = board.san(played.move)
                result.human_elo = elo
    return result


def _line_cp(line: EngineLine) -> int:
    """A single comparable centipawn value (mate mapped to a large number)."""
    if line.mate_in is not None:
        return 10_000 if line.mate_in > 0 else -10_000
    return line.score_cp or 0


def assess_complexity(analysis: Analysis) -> tuple[str, str]:
    """Judge, from the multipv spread, how easy this position is to get wrong.

    Returns (level, message) where level is 'calm', 'sharp' or 'critical'.
    The idea: if only one move holds the evaluation and everything else drops
    off, a human is very likely to blunder - flag it. Uses only the lines we
    already computed, so it costs nothing extra."""
    lines = analysis.lines
    if len(lines) < 2:
        return ("calm", "")
    cps = [_line_cp(ln) for ln in lines]
    best = cps[0]
    gap_second = best - cps[1]
    idx = min(3, len(cps) - 1)
    gap_topfew = best - cps[idx]
    if gap_second >= 120:
        return (
            "critical",
            "Tricky position — there's essentially one good move here, so it's "
            "easy to go wrong. Take your time.",
        )
    if gap_topfew >= 150:
        return (
            "sharp",
            "Sharp position — only a couple of moves keep your advantage. "
            "Calculate carefully before committing.",
        )
    return ("calm", "")


@dataclass
class RankedMove:
    san: str
    score_cp: int | None  # from the side-to-move's perspective
    mate_in: int | None


def rank_all_moves(fen: str, depth: int = 12) -> list[RankedMove]:
    """Score every legal move, best to worst. Used for the on-demand
    'top 20 / worst 10' view - deliberately not run on every move, since a
    full-width multipv search is heavier than the lean live analysis."""
    path = find_stockfish()
    if path is None:
        raise RuntimeError("Stockfish binary not found.")
    board = chess.Board(fen)
    n = board.legal_moves.count()
    if n == 0:
        return []
    ranked = []
    with chess.engine.SimpleEngine.popen_uci(path) as engine:
        infos = engine.analyse(board, chess.engine.Limit(depth=depth), multipv=n)
        if isinstance(infos, dict):
            infos = [infos]
        for info in infos:
            pv = info.get("pv")
            score = info.get("score")
            if not pv or score is None:
                continue
            pov = score.pov(board.turn)
            ranked.append(RankedMove(board.san(pv[0]), pov.score(), pov.mate()))
    return ranked


def white_cp(analysis: Analysis) -> int:
    """The analysis eval converted to white's perspective, in centipawns."""
    best = analysis.best
    if best is None:
        return 0
    if best.mate_in is not None:
        stm = 10_000 if best.mate_in > 0 else -10_000
    else:
        stm = best.score_cp or 0
    return stm if analysis.turn == "white" else -stm


def sweep_game(
    moves_san: list[str], depth: int = 12, progress=None, start_fen: str | None = None
) -> list[dict]:
    """Evaluate every position of a game. Returns one dict per ply with the
    eval (white's perspective, centipawns) before and after the move, and the
    swing. Used for blunder detection in full-game review."""
    path = find_stockfish()
    if path is None:
        raise RuntimeError("Stockfish binary not found.")
    board = chess.Board(start_fen) if start_fen else chess.Board()
    results = []
    with chess.engine.SimpleEngine.popen_uci(path) as engine:
        def white_cp() -> int:
            info = engine.analyse(board, chess.engine.Limit(depth=depth))
            return info["score"].white().score(mate_score=10_000)

        prev_eval = white_cp()
        for i, san in enumerate(moves_san):
            move = board.parse_san(san)
            mover = "white" if board.turn else "black"
            board.push(move)
            cur_eval = white_cp()
            swing = cur_eval - prev_eval
            results.append({
                "ply": i + 1,
                "move_number": (i // 2) + 1,
                "mover": mover,
                "san": san,
                "eval_before": prev_eval,
                "eval_after": cur_eval,
                # Loss from the mover's perspective (positive = mistake).
                "loss": -swing if mover == "white" else swing,
                "fen_after": board.fen(),
            })
            prev_eval = cur_eval
            if progress:
                progress((i + 1) / len(moves_san))
    return results

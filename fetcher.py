"""Game fetcher: use the usernames from the screenshot to pull the actual game
PGN from the chess.com / Lichess public APIs (free, no auth), so the coach can
review the whole game rather than one snapshot.

The fetched game is only trusted if the screenshotted position actually occurs
in its move list - that check makes analysing the wrong game essentially
impossible."""

from __future__ import annotations

import io

import chess
import chess.pgn
import requests

_HEADERS = {"User-Agent": "chess-coach-app (personal coaching tool)"}


def _get(url: str, **kwargs) -> requests.Response:
    """GET with one retry - lichess occasionally drops the first connection."""
    try:
        return requests.get(url, **kwargs)
    except requests.ConnectionError:
        return requests.get(url, **kwargs)


def _positions_in_game(game: chess.pgn.Game) -> tuple[list[str], list[str]]:
    """Return (placement strings, SAN moves) for every position in the game."""
    board = game.board()
    placements = [board.fen().split()[0]]
    sans = []
    for move in game.mainline_moves():
        sans.append(board.san(move))
        board.push(move)
        placements.append(board.fen().split()[0])
    return placements, sans


def _match(pgn_text: str, placement: str, opponent: str | None) -> dict | None:
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        return None
    headers = game.headers
    if opponent:
        players = {headers.get("White", "").lower(), headers.get("Black", "").lower()}
        if opponent.lower() not in players:
            return None
    placements, sans = _positions_in_game(game)
    if placement not in placements:
        return None
    return {
        "pgn": pgn_text,
        "moves_san": sans,
        "ply_of_screenshot": placements.index(placement),
        "white": headers.get("White"),
        "black": headers.get("Black"),
        "result": headers.get("Result"),
    }


def fetch_chesscom(username: str, placement: str, opponent: str | None = None) -> dict | None:
    """Search the player's most recent chess.com archives for a game containing
    the screenshotted position."""
    archives = _get(
        f"https://api.chess.com/pub/player/{username.lower()}/games/archives",
        headers=_HEADERS, timeout=10,
    )
    if archives.status_code != 200:
        return None
    for archive_url in reversed(archives.json().get("archives", [])[-2:]):
        month = _get(archive_url, headers=_HEADERS, timeout=15)
        if month.status_code != 200:
            continue
        for game in reversed(month.json().get("games", [])):
            pgn = game.get("pgn")
            if not pgn:
                continue
            hit = _match(pgn, placement, opponent)
            if hit:
                hit["site"] = "chess.com"
                hit["url"] = game.get("url")
                return hit
    return None


def fetch_lichess(username: str, placement: str, opponent: str | None = None) -> dict | None:
    """Search the player's recent Lichess games for the screenshotted position."""
    resp = _get(
        f"https://lichess.org/api/games/user/{username}",
        params={"max": 30},
        headers={**_HEADERS, "Accept": "application/x-chess-pgn"},
        timeout=20,
    )
    if resp.status_code != 200:
        return None
    for pgn in resp.text.split("\n\n\n"):
        pgn = pgn.strip()
        if not pgn:
            continue
        hit = _match(pgn, placement, opponent)
        if hit:
            hit["site"] = "lichess"
            return hit
    return None


def find_game(placement: str, usernames: list[str], site_hint: str | None = None) -> dict | None:
    """Try to locate the game on either site given the extracted usernames."""
    usernames = [u for u in usernames if u]
    fetchers = [fetch_chesscom, fetch_lichess]
    if site_hint == "lichess":
        fetchers.reverse()
    for fetch in fetchers:
        for i, user in enumerate(usernames):
            opponent = usernames[1 - i] if len(usernames) == 2 else None
            try:
                hit = fetch(user, placement, opponent)
            except requests.RequestException:
                continue
            if hit:
                return hit
    return None


def parse_pasted_pgn(pgn_text: str, placement: str | None = None) -> dict | None:
    """Fallback: user pastes a PGN directly."""
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        return None
    placements, sans = _positions_in_game(game)
    ply = placements.index(placement) if placement in placements else len(placements) - 1
    return {
        "pgn": pgn_text,
        "moves_san": sans,
        "ply_of_screenshot": ply,
        "white": game.headers.get("White"),
        "black": game.headers.get("Black"),
        "result": game.headers.get("Result"),
        "site": "pasted",
    }

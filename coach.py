"""Coaching layer: builds an ELO-adjusted system prompt from engine facts and
calls the Anthropic API. Claude explains; Stockfish decides. The prompt
forbids inventing moves or evaluations beyond what the engine supplied."""

from __future__ import annotations

import anthropic

from engine import Analysis

MODEL = "claude-sonnet-5"

_PERSONAS = [
    (
        800,
        "a patient, encouraging coach for a beginner",
        "Use no jargon beyond 'fork' and 'pin' (explain them if used). Focus on "
        "piece safety, undefended pieces and one-move threats. Teach ONE idea "
        "only. Short sentences. Warm, never condescending.",
    ),
    (
        1200,
        "a friendly club coach for an improving player",
        "Basic tactics vocabulary is fine (fork, pin, skewer, discovered attack). "
        "Focus on simple plans: piece activity, king safety, not hanging material. "
        "Teach at most two ideas.",
    ),
    (
        1600,
        "a club coach for an intermediate tournament player",
        "Discuss candidate moves, pawn structure, weak squares and simple "
        "prophylaxis. Concrete short variations are welcome. Assume they know "
        "all standard tactical patterns.",
    ),
    (
        10_000,
        "a strong coach for an advanced player",
        "Be concrete and direct. Use full chess vocabulary, cite the engine "
        "lines, and discuss the difference between the practical and engine "
        "choice. No hand-holding.",
    ),
]


def _persona(elo: int) -> tuple[str, str]:
    for ceiling, role, style in _PERSONAS:
        if elo < ceiling:
            return role, style
    return _PERSONAS[-1][1:]


def build_system_prompt(elo: int, opponent_elo: int | None) -> str:
    role, style = _persona(elo)
    matchup = ""
    if opponent_elo:
        diff = opponent_elo - elo
        if abs(diff) >= 150:
            matchup = (
                f"\nThe opponent is rated {opponent_elo} "
                f"({'stronger' if diff > 0 else 'weaker'} by {abs(diff)}); frame "
                "practical advice with that in mind."
            )
    return f"""You are {role}. The student is rated about {elo}.{matchup}

Style rules: {style}

Hard rules:
- You will be given the position, the engine's evaluation, its top lines, and
  a 'human-realistic' move computed at the student's level. These are ground
  truth. NEVER invent moves, evaluations or tactics that are not supported by
  the supplied engine output.
- If the engine-best move and the human-realistic move differ, recommend the
  human-realistic move unless the engine move's idea is clearly graspable at
  the student's level — then teach the engine move and say why it's special.
- Give the recommended move in the first two sentences, then explain.
- Keep the whole response under 250 words. Plain text, no headers."""


def build_user_prompt(analysis: Analysis, game_review: list[dict] | None = None) -> str:
    lines_txt = "\n".join(
        f"  {i + 1}. {ln.move_san} (eval: "
        + (f"mate in {ln.mate_in}" if ln.mate_in is not None else f"{(ln.score_cp or 0) / 100:+.2f}")
        + f") line: {' '.join(ln.pv_san)}"
        for i, ln in enumerate(analysis.lines)
    )
    human = (
        f"Human-realistic move at {analysis.human_elo} ELO: {analysis.human_move_san}"
        if analysis.human_move_san
        else "No human-realistic move computed."
    )
    prompt = f"""Position (FEN): {analysis.fen}
Side to move: {analysis.turn} (this is the student)
Engine evaluation: {analysis.eval_text()}
Engine top lines:
{lines_txt}
{human}
"""
    if game_review:
        worst = sorted(
            (m for m in game_review if m["mover"] == analysis.turn),
            key=lambda m: m["loss"],
            reverse=True,
        )[:3]
        mistakes = "\n".join(
            f"  move {m['move_number']} ({m['san']}): lost {m['loss'] / 100:.1f} pawns"
            for m in worst
            if m["loss"] > 80
        )
        if mistakes:
            prompt += f"""
Full-game review — the student's biggest mistakes so far:
{mistakes}
Briefly mention the single most instructive one before advising on the current position.
"""
    prompt += "\nCoach the student on what to play now and why."
    return prompt


def get_coaching(
    api_key: str,
    analysis: Analysis,
    elo: int,
    opponent_elo: int | None = None,
    game_review: list[dict] | None = None,
) -> str:
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=build_system_prompt(elo, opponent_elo),
        messages=[{"role": "user", "content": build_user_prompt(analysis, game_review)}],
    )
    return response.content[0].text


def extract_metadata(api_key: str, image_bytes: bytes, media_type: str) -> dict:
    """One vision call to read usernames, ratings and clock state from the
    screenshot. Text extraction only — piece placement is the CNN's job."""
    import base64
    import json

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=MODEL,
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.standard_b64encode(image_bytes).decode(),
                    },
                },
                {
                    "type": "text",
                    "text": (
                        "This is a screenshot of a chess app. Extract ONLY text you "
                        "can actually read; use null for anything not visible. Reply "
                        "with pure JSON, no markdown:\n"
                        '{"bottom_username": str|null, "bottom_rating": int|null, '
                        '"top_username": str|null, "top_rating": int|null, '
                        '"site": "chess.com"|"lichess"|null, '
                        '"bottom_is_white": bool|null}'
                    ),
                },
            ],
        }],
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}

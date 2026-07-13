# Chess Coach

Mobile-friendly Streamlit app: upload a screenshot of a chess game (chess.com /
lichess app), confirm the detected position, and get coaching pitched at your
rating — powered by Stockfish for the chess and Claude for the explanation.

## How it works

1. **Vision** — OpenCV finds the board in the screenshot; a CNN
   (`board-to-fen`) classifies the 64 squares into a FEN. One Claude vision
   call reads usernames and ratings (text only — never piece placement).
2. **Confirmation** — the reconstructed board is shown for approval, with a
   tap-to-fix editor and side-to-move toggle. Analysis only runs on a
   confirmed, legal position.
3. **Game fetch** — using the usernames, the app looks the game up on the
   chess.com / Lichess public APIs and verifies the screenshotted position
   occurs in the move list. If found, Stockfish reviews the whole game.
4. **Coaching** — Stockfish supplies the engine-best lines plus a
   "human-realistic" move (ELO-limited). Claude explains, in a persona matched
   to your rating band.

## Deploy (Streamlit Community Cloud, free)

1. Push this repo to GitHub (public).
2. On share.streamlit.io create an app pointing at `app.py`.
3. In the app's **Secrets**, add: `ANTHROPIC_API_KEY = "sk-ant-..."`.
4. `packages.txt` installs Stockfish automatically.

## Local development

```
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
# drop a stockfish .exe into ./stockfish/ (or set STOCKFISH_PATH)
.venv\Scripts\streamlit run app.py
```

"""Vision pipeline: locate the chess board in a screenshot and convert it to FEN.

Two-stage approach:
1. Board detection (OpenCV): find the large square board region in a phone
   screenshot. Contour detection first, with a "checkerboard scan" fallback
   that slides a square window down the image and scores 8x8 alternation.
2. Piece classification (board-to-fen CNN): per-square classification of the
   cropped board into a FEN placement string.
"""

from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image


# ---------------------------------------------------------------------------
# Board detection
# ---------------------------------------------------------------------------

def _checker_score(gray_square: np.ndarray) -> float:
    """Score how much a grayscale square crop looks like an 8x8 checkerboard.

    Compares mean brightness of the two square colour groups using the
    central region of each cell (piece glyphs mostly sit centre, so we sample
    a ring near cell edges instead).
    """
    size = 256
    img = cv2.resize(gray_square, (size, size), interpolation=cv2.INTER_AREA)
    cell = size // 8
    light_vals, dark_vals = [], []
    for r in range(8):
        for c in range(8):
            y, x = r * cell, c * cell
            # Sample the cell's corner patches, which pieces rarely cover.
            patch = np.concatenate([
                img[y : y + cell // 4, x : x + cell // 4].ravel(),
                img[y : y + cell // 4, x + 3 * cell // 4 : x + cell].ravel(),
                img[y + 3 * cell // 4 : y + cell, x : x + cell // 4].ravel(),
                img[y + 3 * cell // 4 : y + cell, x + 3 * cell // 4 : x + cell].ravel(),
            ])
            (light_vals if (r + c) % 2 == 0 else dark_vals).append(patch.mean())
    light = np.array(light_vals)
    dark = np.array(dark_vals)
    contrast = abs(light.mean() - dark.mean())
    # Penalise groups that aren't internally uniform (random photos score high
    # contrast by luck but have big within-group variance).
    noise = light.std() + dark.std()
    return float(contrast - 0.5 * noise)


def _detect_by_contours(gray: np.ndarray) -> tuple[int, int, int] | None:
    """Find a large square contour. Returns (x, y, side) or None."""
    h, w = gray.shape
    edges = cv2.Canny(gray, 40, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_area = 0
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        area = cw * ch
        if area < 0.15 * w * h:
            continue
        aspect = cw / ch
        if not 0.92 <= aspect <= 1.08:
            continue
        if area > best_area:
            best_area = area
            side = min(cw, ch)
            best = (x, y, side)
    return best


def _detect_by_scan(gray: np.ndarray) -> tuple[int, int, int]:
    """Fallback: assume the board spans (nearly) the full image width and
    slide a square window vertically, keeping the best checkerboard score."""
    h, w = gray.shape
    side = min(w, h)
    best_score = -1e9
    best_xy = (0, 0)
    # Try a few widths (full width, 95%, 90%) x vertical positions.
    for frac in (1.0, 0.95, 0.9):
        s = int(side * frac)
        x = (w - s) // 2
        step = max(8, (h - s) // 40 or 1)
        for y in range(0, h - s + 1, step):
            score = _checker_score(gray[y : y + s, x : x + s])
            if score > best_score:
                best_score = score
                best_xy = (x, y)
                best_side = s
    return (best_xy[0], best_xy[1], best_side)


def find_board(image: Image.Image) -> Image.Image:
    """Locate the chess board in a screenshot and return the cropped board."""
    rgb = np.array(image.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    candidates = []
    contour_hit = _detect_by_contours(gray)
    if contour_hit:
        candidates.append(contour_hit)
    candidates.append(_detect_by_scan(gray))

    # Keep whichever candidate actually looks most like a checkerboard.
    def score(c):
        x, y, s = c
        return _checker_score(gray[y : y + s, x : x + s])

    x, y, s = max(candidates, key=score)
    return image.convert("RGB").crop((x, y, x + s, y + s))


# ---------------------------------------------------------------------------
# Piece classification
# ---------------------------------------------------------------------------

_NET = None


def _load_net():
    """Load the board-to-fen CNN once, from its weights file.

    We bypass board_to_fen.predict: its full-model file has no .h5 extension
    so Keras 3 refuses to load it, and it reloads the model on every call.
    The architecture is defined in code; the .h5 weights load fine.
    """
    global _NET
    if _NET is None:
        import os

        import board_to_fen
        from board_to_fen.KerasNeuralNetwork import KerasNeuralNetwork

        weights = os.path.join(
            os.path.dirname(board_to_fen.__file__),
            "saved_models", "november_model_weights.h5",
        )
        net = KerasNeuralNetwork()
        net.model.load_weights(weights)
        _NET = net
    return _NET


def board_image_to_placement(board_img: Image.Image, flipped: bool = False) -> str:
    """Run the board-to-fen CNN on a cropped board image.

    Returns the FEN placement field only (e.g. "rnbqkbnr/pppppppp/8/...").
    `flipped` means the screenshot is from Black's perspective.
    """
    from board_to_fen.utils import Decoder_FEN, Tiler

    net = _load_net()
    tiles = Tiler().get_tiles(img=board_img)
    batch = np.stack([np.array(t) for t in tiles]).reshape(-1, 50, 50, 3)
    predictions = net.model.predict(batch, verbose=0)
    labels = [net.CATEGORIES[i] for i in predictions.argmax(axis=1)]
    fen = Decoder_FEN().fen_decode(squares=labels, black_view=flipped)
    if "invalid" in fen or " " in fen:
        raise ValueError("The classifier couldn't find a sensible position in the image.")
    return fen.split()[0]


def screenshot_to_placement(image: Image.Image, flipped: bool = False) -> tuple[str, Image.Image]:
    """Full pipeline: screenshot -> (FEN placement, cropped board image)."""
    board_img = find_board(image)
    placement = board_image_to_placement(board_img, flipped=flipped)
    return placement, board_img


def image_from_bytes(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))

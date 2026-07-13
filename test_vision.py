"""Manual test for the vision pipeline.

Builds a fake phone screenshot (board pasted into a tall dark canvas with
text above/below, like the chess.com app) and checks the pipeline recovers
the expected FEN both from the bare board and from the screenshot.
"""

import sys

from PIL import Image, ImageDraw

import vision

EXPECTED = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R"


def make_fake_screenshot(board: Image.Image) -> Image.Image:
    """Paste the board into a 390x844 'iPhone screenshot' with app chrome."""
    board = board.convert("RGB").resize((390, 390))
    canvas = Image.new("RGB", (390, 844), (38, 37, 34))  # chess.com dark grey
    draw = ImageDraw.Draw(canvas)
    draw.text((16, 150), "Magnus_Fan_1100 (1104)", fill=(230, 230, 230))
    draw.rectangle([330, 140, 382, 170], fill=(70, 70, 70))  # fake clock
    canvas.paste(board, (0, 200))
    draw.text((16, 620), "tomapperley (1088)", fill=(230, 230, 230))
    draw.rectangle([330, 640, 382, 670], fill=(120, 170, 90))
    return canvas


def main():
    board = Image.open("test_images/lichess_italian.gif").convert("RGB")

    fen1 = vision.board_image_to_placement(board)
    ok1 = fen1 == EXPECTED
    print(f"bare board:    {'PASS' if ok1 else 'FAIL'}  {fen1}")

    shot = make_fake_screenshot(board)
    shot.save("test_images/fake_screenshot.png")
    fen2, crop = vision.screenshot_to_placement(shot)
    crop.save("test_images/detected_crop.png")
    ok2 = fen2 == EXPECTED
    print(f"screenshot:    {'PASS' if ok2 else 'FAIL'}  {fen2}")

    sys.exit(0 if ok1 and ok2 else 1)


if __name__ == "__main__":
    main()

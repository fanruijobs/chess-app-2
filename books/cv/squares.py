"""Split a warped board image into its 64 square patches."""

import numpy as np


def square_color(square_index: int) -> str:
    """'light' or 'dark' for a rank8-first square index (0 = a8)."""
    file = square_index % 8
    rank = square_index // 8
    return "light" if (file + 7 - rank) % 2 == 1 else "dark"


def split_board(board_img: np.ndarray, inset: float = 0.10,
                vx: list[int] | None = None, hy: list[int] | None = None,
                baseline: bool = False) -> list[list[np.ndarray]]:
    """Return an 8x8 grid of grayscale patches, rank 8 first (a8..h8, ... a1..h1).

    With explicit grid lines (vx/hy, 9 entries each) the squares are split
    along the detected lines; otherwise the board is divided equally.
    `inset` shrinks each square inward (percent of the square side) to drop
    border artifacts. With `baseline` (books whose pieces sit ON the bottom
    grid line), the bottom edge extends slightly past the line so pieces are
    not clipped differently per square.
    """
    size = board_img.shape[0]
    if vx is None or hy is None:
        vx = [size * c // 8 for c in range(9)]
        hy = [size * r // 8 for r in range(9)]
    patches = []
    for row in range(8):  # row 0 = rank 8
        rank = []
        cell = hy[row + 1] - hy[row]
        for col in range(8):
            top = hy[row] + int(inset * cell)
            if baseline:
                bottom = min(size, hy[row + 1] + int(0.08 * cell))
            else:
                bottom = hy[row + 1] - int(inset * cell)
            left = vx[col] + int(inset * (vx[col + 1] - vx[col]))
            right = vx[col + 1] - int(inset * (vx[col + 1] - vx[col]))
            rank.append(board_img[top:bottom, left:right])
        patches.append(rank)
    return patches


def patch_to_numpy(patches: list[list[np.ndarray]]) -> np.ndarray:
    """Flatten the 8x8 patch grid to an (64, n, n) ndarray."""
    return np.stack([p for rank in patches for p in rank])

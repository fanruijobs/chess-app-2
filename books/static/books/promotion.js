/**
 * Promotion picker shared by the interactive boards.
 *
 * Chessground has no built-in promotion chooser: it moves the pawn on the
 * board before firing `movable.events.after`. The picker is drawn over the
 * promotion square as a column of board-square-sized choices, so every icon
 * is the same size as a piece on the board.
 *
 * askPromotion(color, boardEl, destSquare, orientation) resolves with
 * "q" | "r" | "b" | "n", or `null` when dismissed (outside click / Escape).
 * The caller must restore the board position on `null`.
 */

const CHOICES = [
  ["q", "queen", "Queen", "Q"],
  ["r", "rook", "Rook", "R"],
  ["b", "bishop", "Bishop", "B"],
  ["n", "knight", "Knight", "N"],
];

let active = false;

/** True when a pawn move in `chess` from `orig` to `dest` needs a promotion. */
export function needsPromotion(chess, orig, dest) {
  const piece = chess.get(orig);
  if (!piece || piece.type !== "p") return false;
  return dest[1] === (piece.color === "w" ? "8" : "1");
}

/**
 * Show the picker over the promotion square.
 *
 * `boardEl` is the chessground `.cg-wrap` element, `destSquare` the square the
 * pawn is moving to, and `orientation` the board's current orientation
 * ("white" | "black").
 */
export function askPromotion(color, boardEl, destSquare, orientation = "white") {
  if (active) return Promise.resolve(null);
  active = true;

  return new Promise((resolve) => {
    const pieceColor = color === "black" ? "black" : "white";

    const picker = document.createElement("div");
    picker.className = "cg-wrap promotion-picker";
    picker.setAttribute("role", "dialog");
    picker.setAttribute("aria-label", "Choose promotion piece");

    const buttons = CHOICES.map(([piece, role, label, key]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "promotion-choice";
      button.dataset.piece = piece;
      button.title = `${label} (${key})`;
      button.setAttribute("aria-label", `Promote to ${label}`);
      button.setAttribute("aria-keyshortcuts", key);
      const glyph = document.createElement("piece");
      // chessground's stylesheet keys its piece images on the role name
      glyph.className = `${role} ${pieceColor}`;
      button.appendChild(glyph);
      button.addEventListener("click", () => finish(piece));
      picker.appendChild(button);
      return button;
    });

    boardEl.appendChild(picker);
    place();
    buttons[0].focus();
    window.addEventListener("resize", place);
    document.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("keydown", onKey);

    /** Anchor the column on the promotion square; stack away from the edge. */
    function place() {
      const rect = boardEl.getBoundingClientRect();
      const square = rect.width / 8;
      const file = destSquare.charCodeAt(0) - 97;
      const rank = Number(destSquare[1]) - 1;
      const col = orientation === "black" ? 7 - file : file;
      const row = orientation === "black" ? rank : 7 - rank;
      let top = row * square;
      if (top + CHOICES.length * square > rect.height) {
        top = row * square - (CHOICES.length - 1) * square;
      }
      top = Math.max(0, Math.min(top, rect.height - CHOICES.length * square));
      picker.style.setProperty("--promo-square", `${square}px`);
      picker.style.left = `${col * square}px`;
      picker.style.top = `${top}px`;
    }

    function finish(piece) {
      window.removeEventListener("resize", place);
      document.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("keydown", onKey);
      picker.remove();
      active = false;
      resolve(piece);
    }

    function onPointerDown(event) {
      if (picker.contains(event.target)) return;
      // dismiss before the board sees the press, so it does not start a drag
      event.preventDefault();
      event.stopPropagation();
      finish(null);
    }

    function onKey(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        finish(null);
        return;
      }
      const choice = CHOICES.find(([, , , key]) =>
        key.toLowerCase() === event.key.toLowerCase()
      );
      if (choice) {
        event.preventDefault();
        finish(choice[0]);
      }
    }
  });
}

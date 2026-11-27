import { createElement, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { getTrainingItem, markTrainingItem, trainingItemAction } from "../api/client";
import type { TrainingItemResponse, TrainingItemStatus } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { StockfishAnalysis, type EngineLineInfo } from "../engine.js";
import { Chessground } from "../vendor/chessground.min.js";
import { Chess } from "../vendor/chess.js";
import { askPromotion, needsPromotion } from "../vendor/promotion.js";

import "../vendor/chessground.base.css";
import "../vendor/chessground.brown.css";
import "../vendor/chessground.cburnett.css";
import "../vendor/palette.css";
import "../vendor/trainer.css";
import "../session.css";

const PIECES = ["K", "Q", "R", "B", "N", "P", "k", "q", "r", "b", "n", "p"];
const ROLE = {
  K: "king",
  Q: "queen",
  R: "rook",
  B: "bishop",
  N: "knight",
  P: "pawn",
} as const;
const BRUSHES = ["green", "red", "blue"];
const CATEGORIES = new Set(["pending", "rework", "passed"]);

interface DisplayLine extends EngineLineInfo {
  san: string[];
  shown: boolean;
}

function safeChess(fen: string) {
  try {
    return new Chess(fen);
  } catch (error) {
    console.error("[training] invalid FEN (moves disabled):", error, fen);
    return null;
  }
}

function parseFenCells(fen: string): (string | null)[] | null {
  const ranks = fen.trim().split(/\s+/)[0].split("/");
  if (ranks.length !== 8) return null;
  const cells = new Array<string | null>(64).fill(null);
  for (let r = 0; r < 8; r++) {
    let col = 0;
    for (const ch of ranks[r]) {
      if (/\d/.test(ch)) {
        col += Number(ch);
      } else if (PIECES.includes(ch)) {
        cells[r * 8 + col] = ch;
        col += 1;
      } else {
        return null;
      }
    }
    if (col !== 8) return null;
  }
  return cells;
}

function cellsToFen(cells: (string | null)[], turn: string): string {
  const ranks: string[] = [];
  for (let r = 0; r < 8; r++) {
    let out = "";
    let run = 0;
    for (let c = 0; c < 8; c++) {
      const piece = cells[r * 8 + c];
      if (piece) {
        if (run) out += run;
        run = 0;
        out += piece;
      } else {
        run += 1;
      }
    }
    if (run) out += run;
    ranks.push(out);
  }
  return `${ranks.join("/")} ${turn} - - 0 1`;
}

function squareIndex(square: string): number {
  const file = square.charCodeAt(0) - 97;
  const rank = Number(square[1]);
  return (8 - rank) * 8 + file;
}

function evalBadge(line: DisplayLine): string {
  if (line.eval_mate !== null) {
    const sign = line.eval_mate > 0 ? "+" : "";
    return `M${sign}${line.eval_mate}`;
  }
  if (line.eval_cp !== null) {
    return `${line.eval_cp >= 0 ? "+" : ""}${(line.eval_cp / 100).toFixed(2)}`;
  }
  return "";
}

export function TrainingSessionPage() {
  const { id, itemId } = useParams<{ id: string; itemId: string }>();
  const setId = Number(id);
  const trainingItemId = Number(itemId);
  const [searchParams] = useSearchParams();
  const rawCategory = searchParams.get("status") ?? "pending";
  const category: TrainingItemStatus = CATEGORIES.has(rawCategory)
    ? (rawCategory as TrainingItemStatus)
    : "pending";
  const categoryQuery = category === "pending" ? "" : `?status=${category}`;
  const navigate = useNavigate();
  const { user } = useAuth();

  const [data, setData] = useState<TrainingItemResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [currentFen, setCurrentFen] = useState("");
  const [analysisOn, setAnalysisOn] = useState(false);
  const [lines, setLines] = useState<DisplayLine[]>([]);
  const [editing, setEditing] = useState(false);
  const [armed, setArmed] = useState<string | null>(null);
  const [fenInput, setFenInput] = useState("");
  const [busy, setBusy] = useState(false);

  const boardRef = useRef<HTMLDivElement>(null);
  const cgRef = useRef<any>(null);
  const chessRef = useRef<any>(null);
  const savedFenRef = useRef("");
  const currentFenRef = useRef("");
  const editingRef = useRef(false);
  const armedRef = useRef<string | null>(null);
  const editCellsRef = useRef<(string | null)[] | null>(null);
  const analysisOnRef = useRef(false);
  const linesRef = useRef<DisplayLine[]>([]);
  const engineRef = useRef<StockfishAnalysis | null>(null);

  function setCurrent(fen: string) {
    currentFenRef.current = fen;
    setCurrentFen(fen);
  }

  function setEditingState(value: boolean) {
    editingRef.current = value;
    setEditing(value);
  }

  function setArmedState(value: string | null) {
    armedRef.current = value;
    setArmed(value);
  }

  function setLineState(value: DisplayLine[]) {
    linesRef.current = value;
    setLines(value);
  }

  function computeDests() {
    const dests = new Map<string, string[]>();
    const chess = chessRef.current;
    if (!chess || editingRef.current) return dests;
    for (const move of chess.moves({ verbose: true })) {
      const entry = dests.get(move.from);
      if (entry) entry.push(move.to);
      else dests.set(move.from, [move.to]);
    }
    return dests;
  }

  function refreshDests() {
    cgRef.current?.set({
      movable: { free: false, color: "both", dests: computeDests() },
    });
  }

  function syncEditBoard() {
    const cells = editCellsRef.current;
    if (!cells) return;
    const turn = currentFenRef.current.trim().split(/\s+/)[1] || "w";
    const fen = cellsToFen(cells, turn === "b" ? "b" : "w");
    cgRef.current?.set({ fen });
    setFenInput(fen);
  }

  function applyPosition(fen: string) {
    chessRef.current = safeChess(fen);
    setCurrent(fen);
    cgRef.current?.set({ fen });
    refreshDests();
    if (analysisOnRef.current) scheduleAnalysis();
  }

  function scheduleAnalysis() {
    if (!analysisOnRef.current) return;
    ensureEngine().analyzeNow(currentFenRef.current);
  }

  async function onMove(orig: string, dest: string, meta: any) {
    if (editingRef.current) {
      const cells = editCellsRef.current;
      if (!cells) return;
      const from = squareIndex(orig);
      const to = squareIndex(dest);
      if (from >= 0 && from < 64 && to >= 0 && to < 64) {
        cells[to] = cells[from];
        cells[from] = null;
        syncEditBoard();
      }
      return;
    }
    const chess = chessRef.current;
    if (!chess) return;
    let promotion = meta?.promotion;
    if (!promotion && needsPromotion(chess, orig, dest)) {
      const color = chess.get(orig)?.color === "b" ? "black" : "white";
      promotion = await askPromotion(
        color,
        boardRef.current as HTMLElement,
        dest,
        cgRef.current?.state?.orientation ?? "white",
      );
      if (!promotion) {
        cgRef.current?.set({ fen: chess.fen() });
        refreshDests();
        return;
      }
    }
    let applied = null;
    try {
      applied = chess.move({
        from: orig,
        to: dest,
        promotion: promotion || undefined,
      });
    } catch (firstError) {
      try {
        applied = chess.move({ from: orig, to: dest, promotion: "q" });
      } catch (secondError) {
        console.error(
          `[training] move ${orig}-${dest} rejected:`,
          firstError,
          secondError,
        );
      }
    }
    setCurrent(chess.fen());
    if (applied?.promotion) cgRef.current?.set({ fen: currentFenRef.current });
    refreshDests();
    if (analysisOnRef.current) scheduleAnalysis();
  }

  function handleSelect(square: string) {
    if (!editingRef.current || armedRef.current === null) return;
    const cells = editCellsRef.current;
    if (!cells) return;
    const index = squareIndex(square);
    if (index < 0 || index > 63) return;
    cells[index] = armedRef.current === "erase" ? null : armedRef.current;
    setArmedState(null);
    syncEditBoard();
  }

  function startEdit() {
    setError("");
    setMessage("");
    const cells =
      parseFenCells(currentFenRef.current) ||
      new Array<string | null>(64).fill(null);
    editCellsRef.current = cells;
    setEditingState(true);
    setArmedState(null);
    setFenInput(currentFenRef.current);
    cgRef.current?.set({
      movable: { free: true, color: "both" },
      selectable: { enabled: true },
    });
    syncEditBoard();
  }

  function endEdit() {
    setEditingState(false);
    setArmedState(null);
    cgRef.current?.set({ selectable: { enabled: true } });
    refreshDests();
  }

  async function handleItemAction(
    action: "rotate" | "toggle_turn" | "set_fen",
    fen?: string,
  ) {
    if (!data) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await trainingItemAction(
        data.set.id,
        data.item.id,
        action,
        fen,
      );
      savedFenRef.current = response.fen;
      applyPosition(response.fen);
      setMessage("Position updated.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Position update failed.");
    } finally {
      setBusy(false);
    }
  }

  async function saveEdit() {
    const cells = editCellsRef.current;
    if (!cells) return;
    const turn = currentFenRef.current.trim().split(/\s+/)[1] || "w";
    await handleItemAction("set_fen", cellsToFen(cells, turn));
    endEdit();
  }

  async function handleMark(result: "passed" | "rework") {
    if (!data) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await markTrainingItem(
        data.set.id,
        data.item.id,
        result,
        category,
      );
      if (response.next_item_id) {
        navigate(
          `/sets/${data.set.id}/items/${response.next_item_id}/${categoryQuery}`,
        );
      } else {
        navigate(`/sets/${data.set.id}${categoryQuery}`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Mark failed.");
    } finally {
      setBusy(false);
    }
  }

  function sanFor(fen: string, pv: string[]): string[] {
    const board = safeChess(fen);
    if (!board) return [];
    const san: string[] = [];
    for (const uci of pv) {
      const move = board.move({
        from: uci.slice(0, 2),
        to: uci.slice(2, 4),
        promotion: uci.length > 4 ? uci[4] : undefined,
      });
      if (!move) break;
      san.push(move.san);
    }
    return san;
  }

  function ensureEngine(): StockfishAnalysis {
    if (engineRef.current) return engineRef.current;
    engineRef.current = new StockfishAnalysis({
      workerUrl: "https://cdn.jsdelivr.net/npm/stockfish.js@10.0.2/stockfish.js",
      fallbackWorkerUrl: "/static/books/vendor/stockfish.js",
      lineCount: 3,
      onLines: (newLines, fen) => {
        const next = newLines.map((info) => ({
          ...info,
          san: sanFor(fen, info.pv),
          shown: true,
        }));
        setLineState(next);
        drawLines(next);
      },
      onError: (text) => setError(text),
    });
    return engineRef.current;
  }

  function drawLines(nextLines: DisplayLine[]) {
    if (!analysisOnRef.current) {
      cgRef.current?.setAutoShapes([]);
      return;
    }
    const shapes: any[] = [];
    nextLines.forEach((line, index) => {
      if (!line.shown || !line.pv.length) return;
      const move = line.pv[0];
      shapes.push({
        orig: move.slice(0, 2),
        dest: move.slice(2, 4),
        brush: BRUSHES[index % BRUSHES.length],
      });
    });
    cgRef.current?.setAutoShapes(shapes);
  }

  function changeAnalysis(on: boolean) {
    setAnalysisOn(on);
    analysisOnRef.current = on;
    if (on) {
      setLineState([]);
      ensureEngine().analyzeNow(currentFenRef.current);
    } else {
      setLineState([]);
      cgRef.current?.setAutoShapes([]);
    }
  }

  function runAnalysis() {
    setError("");
    if (!analysisOnRef.current) {
      changeAnalysis(true);
    } else {
      ensureEngine().analyzeNow(currentFenRef.current);
    }
  }

  function toggleLine(index: number, shown: boolean) {
    const next = linesRef.current.map((line, lineIndex) =>
      lineIndex === index ? { ...line, shown } : line,
    );
    setLineState(next);
    drawLines(next);
  }

  useEffect(() => {
    if (!Number.isFinite(setId) || !Number.isFinite(trainingItemId)) {
      setError("Invalid training item.");
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    setMessage("");
    getTrainingItem(setId, trainingItemId, category)
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load item.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [setId, trainingItemId, category]);

  useEffect(() => {
    if (!data || !boardRef.current) return;
    const fen = data.item.fen;
    chessRef.current = safeChess(fen);
    savedFenRef.current = fen;
    setCurrent(fen);
    setLineState([]);
    const board = Chessground(boardRef.current, {
      fen,
      coordinates: true,
      movable: {
        free: false,
        color: "both",
        dests: computeDests(),
        events: { after: onMove },
      },
      draggable: { enabled: true },
      selectable: { enabled: true },
      premovable: { enabled: false },
      events: { select: handleSelect },
    });
    cgRef.current = board;
    if (analysisOnRef.current) scheduleAnalysis();
    return () => {
      engineRef.current?.dispose();
      engineRef.current = null;
      board.destroy?.();
      cgRef.current = null;
    };
    // The board is re-created only when the item changes; all callbacks read refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.item.id]);

  if (loading && !data) return <p className="loading">Loading position…</p>;
  if (error && !data) return <p className="error">{error}</p>;
  if (!data) return null;

  const turn = currentFen.trim().split(/\s+/)[1] === "b" ? "b" : "w";
  const chessComUrl = `https://www.chess.com/analysis?fen=${encodeURIComponent(
    currentFen.trim(),
  )}`;

  return (
    <section>
      <p className="breadcrumb">
        <Link to="/">Training sets</Link> /{" "}
        <Link to={`/sets/${data.set.id}${categoryQuery}`}>
          {data.set.name}
        </Link>{" "}
        / {data.item.label || data.item.id}
      </p>
      <div className="page-heading">
        <div>
          <h1>{data.item.label || `#${data.item.id}`}</h1>
          <p className="stats-line">
            <span className="turn-indicator">
              <span className={`dot ${turn === "b" ? "black" : "white"}`} />
              {turn === "b" ? "Black to move" : "White to move"}
            </span>{" "}
            · status:{" "}
            <span className={`badge badge-${data.item.status}`}>
              {data.item.status}
            </span>{" "}
            · attempts {data.item.attempts}
            {data.item.san_best ? ` · best move: ${data.item.san_best}` : ""}
          </p>
        </div>
        <Link className="button" to={`/sets/${data.set.id}${categoryQuery}`}>
          Back to set
        </Link>
      </div>

      <div className="trainer-layout">
        <div>
          <div ref={boardRef} className="training-board cg-wrap" />
          <p className="row-form">
            <button
              type="button"
              className="button"
              onClick={() => cgRef.current?.toggleOrientation()}
            >
              ⇅ Flip board
            </button>
            {user?.is_superuser && (
              <>
                <button
                  type="button"
                  className="button"
                  disabled={busy}
                  title="rotate the stored position 180° and save"
                  onClick={() => handleItemAction("rotate")}
                >
                  ⟲ Fix orientation
                </button>
                <button
                  type="button"
                  className="button"
                  disabled={busy}
                  title="switch which side is to move and save"
                  onClick={() => handleItemAction("toggle_turn")}
                >
                  ⚫⇄⚪ Switch side to move
                </button>
                <button
                  type="button"
                  className="button"
                  onClick={() => (editing ? endEdit() : startEdit())}
                >
                  ✏️ Edit position
                </button>
              </>
            )}
          </p>

          {editing && (
            <div className="editor-box">
              <p className="hint">
                Pick a piece (or the ✕ eraser), then click squares on the board
                to place or remove it. Saving also fixes the source puzzle.
              </p>
              <p className="row-form palette" id="palette">
                {PIECES.map((piece) => {
                  const color =
                    piece === piece.toUpperCase() ? "white" : "black";
                  const role = ROLE[piece.toUpperCase() as keyof typeof ROLE];
                  return (
                    <button
                      key={piece}
                      type="button"
                      className={armed === piece ? "armed" : ""}
                      title={`${color} ${role}`}
                      onClick={() =>
                        setArmedState(armedRef.current === piece ? null : piece)
                      }
                    >
                      {createElement("piece", { className: `${role} ${color}` })}
                    </button>
                  );
                })}
                <button
                  type="button"
                  className={armed === "erase" ? "armed erase" : "erase"}
                  title="erase pieces"
                  onClick={() =>
                    setArmedState(armedRef.current === "erase" ? null : "erase")
                  }
                >
                  ✕
                </button>
              </p>
              <p className="row-form">
                <input
                  value={fenInput}
                  size={72}
                  spellCheck={false}
                  onChange={(event) => setFenInput(event.target.value)}
                />
                <button
                  type="button"
                  className="button"
                  onClick={() => {
                    const cells = parseFenCells(fenInput);
                    if (!cells) {
                      setError("Invalid FEN — not applied.");
                      return;
                    }
                    editCellsRef.current = cells;
                    setCurrent(fenInput.trim());
                    syncEditBoard();
                  }}
                >
                  Apply FEN
                </button>
              </p>
              <p className="row-form">
                <button
                  type="button"
                  className="button"
                  disabled={busy}
                  onClick={saveEdit}
                >
                  💾 Save position
                </button>
                <button
                  type="button"
                  className="button"
                  onClick={() => {
                    endEdit();
                    applyPosition(savedFenRef.current);
                  }}
                >
                  Cancel
                </button>
              </p>
            </div>
          )}

          <p className="row-form">
            <button
              type="button"
              className={`button analysis-switch ${analysisOn ? "on" : ""}`}
              onClick={() => changeAnalysis(!analysisOn)}
            >
              {analysisOn ? "Hide Analysis" : "Analysis"}
            </button>
            <button type="button" className="button" onClick={runAnalysis}>
              Re-analyze
            </button>
            <button
              type="button"
              className="button"
              onClick={() => applyPosition(savedFenRef.current)}
            >
              Reset board
            </button>
            <a
              className="button"
              href={chessComUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open in Chess.com
            </a>
          </p>
        </div>

        <div>
          {analysisOn && (
            <div className="lines">
              {lines.length === 0 && <p className="hint">Analyzing…</p>}
              {lines.map((line, index) => (
                <p className="hint" key={index}>
                  <input
                    type="checkbox"
                    checked={line.shown}
                    onChange={(event) => toggleLine(index, event.target.checked)}
                  />{" "}
                  {index + 1}. {line.san.join(" ")} {evalBadge(line)}
                </p>
              ))}
            </div>
          )}
          {message && <p className="note">{message}</p>}
          {error && <p className="error">{error}</p>}
          <p className="row-form">
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() => handleMark("passed")}
            >
              ✓ Pass
            </button>
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() => handleMark("rework")}
            >
              ↻ Re-work
            </button>
          </p>
        </div>
      </div>
    </section>
  );
}

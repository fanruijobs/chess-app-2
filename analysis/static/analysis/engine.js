/* Stockfish (stockfish.js, loaded from a CDN with a local fallback) in a
   web worker + UCI plumbing, shared by the analysis board and the training
   session.

   Searches are bounded by 1 second OR depth 25 (whichever comes first) and
   return up to `lineCount` MultiPV lines. `analyze(fen)` is debounced so
   rapid position changes collapse into one search; `analyzeNow(fen)` skips
   the debounce. `onLines(lines, fen)` fires with the finalized lines and
   the fen they belong to.
*/

const INFO_SCORE_RE = /multipv (\d+) score (cp|mate) (-?\d+)/;

export class StockfishAnalysis {
  constructor({
    workerUrl,
    fallbackWorkerUrl,
    lineCount = 3,
    onLines = null,
    onError = null,
    debounceMs = 600,
  }) {
    this.lineCount = lineCount;
    this.onLines = onLines;
    this.onError = onError;
    this.debounceMs = debounceMs;
    this.lines = null; // last finalized lines, null before the first bestmove
    this._fen = null;
    this._ready = false; // 'uciok' received
    this._searching = false;
    this._ignoreNextBestmove = false; // next bestmove belongs to a `stop`
    this._pending = false; // analyze was requested before the engine was ready
    this._queue = []; // commands issued before uciok
    this._partial = []; // multipv index -> parsed info of the current search
    this._timer = null;
    // Browsers forbid cross-origin workers, so the (CDN-hosted) engine
    // script is loaded through a tiny same-origin glue worker via
    // importScripts — the primary source first, the local vendored copy
    // as the offline fallback.
    const sources = [
      workerUrl,
      fallbackWorkerUrl ? new URL(fallbackWorkerUrl, location.href).href : null,
    ].filter(Boolean);
    console.log("[engine] engine sources:", sources);
    const glue =
      `try { importScripts(${JSON.stringify(sources[0])}); }\n` +
      `catch (err) { ${
        sources[1]
          ? `importScripts(${JSON.stringify(sources[1])});`
          : "throw err;"
      } }`;
    this._worker = new Worker(
      URL.createObjectURL(new Blob([glue], {type: "application/javascript"}))
    );
    this._worker.addEventListener("message", (event) =>
      this._onMessage(String(event.data ?? ""))
    );
    this._worker.addEventListener("error", (event) => {
      console.error("[engine] worker error:", event.message || event);
      this._fail("Stockfish failed to load in the browser.");
    });
    this._worker.postMessage("uci");
  }

  _fail(message) {
    if (this.onError) this.onError(message);
  }

  _onMessage(line) {
    if (line === "uciok") {
      this._ready = true;
      console.log(`[engine] uciok — MultiPV set to ${this.lineCount}`);
      this._worker.postMessage("setoption name MultiPV value " + this.lineCount);
      for (const command of this._queue) this._worker.postMessage(command);
      this._queue.length = 0;
      if (this._pending) {
        this._pending = false;
        this._analyzeNow();
      }
    } else if (line.startsWith("info ")) {
      const score = line.match(INFO_SCORE_RE);
      if (!score) return;
      const index = Number(score[1]) - 1;
      if (index >= this.lineCount) return;
      const pvMatch = line.match(/ pv (.+)$/);
      this._partial[index] = {
        pv: pvMatch ? pvMatch[1].trim().split(/\s+/) : [],
        eval_cp: score[2] === "cp" ? Number(score[3]) : null,
        eval_mate: score[2] === "mate" ? Number(score[3]) : null,
      };
    } else if (line.startsWith("bestmove")) {
      if (this._ignoreNextBestmove) {
        this._ignoreNextBestmove = false; // bestmove of an aborted search
        return;
      }
      this._searching = false;
      this.lines = this._partial
        .filter((info) => info && info.pv.length)
        .slice(0, this.lineCount);
      console.log(`[engine] bestmove — ${this.lines.length} line(s)`);
      if (this.onLines) this.onLines(this.lines, this._fen);
    } else if (line.startsWith("info string")) {
      // engine diagnostics, e.g. "error ..." for invalid positions
      console.warn("[engine]", line);
    }
  }

  _send(command) {
    if (this._ready) {
      this._worker.postMessage(command);
    } else {
      this._queue.push(command);
    }
  }

  analyze(fen) {
    // debounced: rapid position changes collapse into one search
    this._fen = fen;
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this._analyzeNow(), this.debounceMs);
  }

  analyzeNow(fen) {
    clearTimeout(this._timer);
    if (fen !== undefined) this._fen = fen;
    this._analyzeNow();
  }

  _analyzeNow() {
    if (!this._ready) {
      this._pending = true;
      return;
    }
    if (this._searching) {
      // abort the in-flight search; its bestmove is ignored
      this._ignoreNextBestmove = true;
      this._worker.postMessage("stop");
    }
    this._searching = true;
    this._partial = new Array(this.lineCount).fill(null);
    this._send("position fen " + this._fen);
    this._send("go depth 25 movetime 1000");
  }

  dispose() {
    clearTimeout(this._timer);
    this._worker.postMessage("quit");
    this._worker.terminate();
  }
}

"""Stockfish analysis: full per-move eval curve + turning-point detection."""

import io
import logging

import chess
import chess.engine
import chess.pgn
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from analysis.classifiers import RuleContext, classify, find_punishing_reply
from analysis.formatting import format_eval
from analysis.models import AnalysisRun, TurningPoint
from analysis.turning_points import EvalPoint, detect_turning_points
from chessdata.models import Game, MoveEval

logger = logging.getLogger("analysis.engine")


class EngineMissingError(Exception):
    """Stockfish binary not found / failed to start."""


class EngineManager:
    """Context manager over one shared Stockfish subprocess for a whole batch."""

    def __init__(self, path=None, threads=4, hash_mb=128):
        self.path = path or settings.STOCKFISH_PATH
        self.threads = threads
        self.hash_mb = hash_mb
        self.engine = None

    def __enter__(self):
        try:
            self.engine = chess.engine.SimpleEngine.popen_uci(self.path, timeout=10)
        except (OSError, chess.engine.EngineError) as exc:
            raise EngineMissingError(
                f"Stockfish not found at {self.path}. Install with: brew install stockfish"
            ) from exc
        self.engine.configure({"Threads": self.threads, "Hash": self.hash_mb})
        return self

    def __exit__(self, *exc_info):
        if self.engine is not None:
            self.engine.quit()

    def __getattr__(self, name):
        return getattr(self.engine, name)

    @property
    def engine_id(self) -> dict:
        return self.engine.id


def _player_color(player_side: str) -> chess.Color:
    return chess.WHITE if player_side == "w" else chess.BLACK


def _move_number(ply: int, side: str, san: str) -> str:
    """'23... Nf6' or '24. Qh5'."""
    number = (ply + 1) // 2
    suffix = "." if side == "w" else "..."
    return f"{number}{suffix} {san}"


def compute_evals(game: Game, engine, depth: int):
    """Replay the game and evaluate every position (player's perspective).

    Returns (evals, moves, boards): the eval curve, the played moves, and the
    board after each ply (used for turning-point classification).
    """
    node = chess.pgn.read_game(io.StringIO(game.pgn))
    if node is None:
        raise ValueError(f"unparseable PGN for game {game.uuid}")
    board = node.board()
    player_color = _player_color(game.player_side)

    evals = []
    moves = []
    boards = []
    for ply, move in enumerate(node.mainline_moves(), start=1):
        san = board.san(move)
        moves.append(move)
        board.push(move)
        boards.append(board.copy())
        info = engine.analyse(board, chess.engine.Limit(depth=depth, time=30.0))
        score = info["score"].pov(player_color)
        # NOTE: use the Score methods — .cp exists only on Cp instances and
        # .mate is a method (Mate stores .moves), so attribute access is
        # type-dependent. score()/mate() work on both.
        if score.is_mate():
            cp, mate = None, score.mate()
        else:
            cp, mate = score.score(), None
        pv = info.get("pv", [])
        pv_san = board.san(pv[0]) if pv else ""
        logger.debug("ply %d %s -> %s", ply, san, format_eval(cp, mate))
        evals.append(
            EvalPoint(
                ply=ply,
                san=san,
                fen=board.fen(),
                side_to_move="w" if board.turn == chess.BLACK else "b",
                cp=cp,
                mate=mate,
                pv_san=pv_san,
            )
        )
    return evals, moves, boards


def _classify_record(game: Game, record, moves, boards, engine) -> tuple[str, str]:
    """Build the rule context for one turning point and classify it."""
    board_before = boards[record.ply - 2]  # decision position (player to move)
    board_after = boards[record.ply - 1]  # after the blunder
    played = moves[record.ply - 1]
    try:
        best = board_before.parse_san(record.san_best) if record.san_best else None
    except ValueError:
        best = None
    ctx = RuleContext(
        board_before=board_before,
        board_after=board_after,
        played=played,
        best=best,
        san_played=record.san_played,
        san_best=record.san_best,
        before_n=record.before.norm,
        after_n=record.after.norm,
        before_mate=record.before.mate,
        after_mate=record.after.mate,
        player_color=_player_color(game.player_side),
    )
    ctx.reply = find_punishing_reply(ctx, engine)
    return classify(ctx)


def analyze_game(game: Game, engine, *, depth: int, threshold_cp: int, force: bool = False):
    """Analyze one game and store the eval curve + turning points.

    `threshold_cp` is only recorded on the run (job metadata) — turning-point
    selection itself uses the fixed rule in detect_turning_points.

    Wipes and rewrites old evals/turning points in one transaction, so a
    crash mid-game never leaves a half-written curve. Returns the AnalysisRun
    (DONE or FAILED), or None if the game was skipped (already analyzed and
    not force).
    """
    if not force and game.last_run and game.last_run.status == AnalysisRun.RunStatus.DONE:
        return None

    logger.info("Analyzing game %s", game.uuid)
    engine_id = engine.engine_id
    run = AnalysisRun.objects.create(
        game=game,
        engine_name=engine_id.get("name", "engine"),
        engine_version=engine_id.get("author", ""),
        depth=depth,
        threshold_cp=threshold_cp,
        status=AnalysisRun.RunStatus.RUNNING,
    )

    try:
        evals, moves, boards = compute_evals(game, engine, depth)
        records = detect_turning_points(evals, game.player_side)
        classified = [
            _classify_record(game, record, moves, boards, engine) for record in records
        ]
        with transaction.atomic():
            game.evals.all().delete()
            game.turning_points.all().delete()
            MoveEval.objects.bulk_create(
                MoveEval(
                    game=game,
                    ply=e.ply,
                    san=e.san,
                    fen=e.fen,
                    side_to_move=e.side_to_move,
                    eval_cp=e.cp,
                    eval_mate=e.mate,
                )
                for e in evals
            )
            TurningPoint.objects.bulk_create(
                TurningPoint(
                    game=game,
                    run=run,
                    ply=r.ply,
                    move_number=_move_number(r.ply, game.player_side, r.san_played),
                    san_played=r.san_played,
                    san_best=r.san_best,
                    fen_before=r.fen_before,
                    fen_after=r.fen_after,
                    eval_before_cp=r.before.cp,
                    eval_before_mate=r.before.mate,
                    eval_after_cp=r.after.cp,
                    eval_after_mate=r.after.mate,
                    drop_cp=r.drop_cp,
                    classification=cls,
                    rule_text=text,
                )
                for r, (cls, text) in zip(records, classified)
            )
            run.total_plies = len(evals)
            run.status = AnalysisRun.RunStatus.DONE
            run.finished_at = timezone.now()
            run.save(update_fields=["total_plies", "status", "finished_at"])
            game.last_run = run
            game.analyzed_at = timezone.now()
            game.save(update_fields=["last_run", "analyzed_at"])
        logger.info(
            "Analyzed game %s: %d plies, %d turning points",
            game.uuid, len(evals), len(records),
        )
        return run
    except Exception as exc:  # noqa: BLE001 - record any failure on the run
        logger.exception("Analysis of %s failed", game.uuid)
        run.status = AnalysisRun.RunStatus.FAILED
        run.error = str(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error", "finished_at"])
        return run

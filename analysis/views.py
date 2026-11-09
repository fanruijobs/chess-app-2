import io
import json
import logging
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models
from django.db.models import Count
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render

from analysis.boards import board_svg
from analysis.formatting import format_eval
from analysis.llm import LlmConfigError, enrich_tp
from analysis.models import AnalysisJob, TurningPoint, WorkerHeartbeat
from analysis.pdf import render_game_pdf
from analysis.profiles import TIME_CLASSES, estimated_seconds_per_game, format_duration
from analysis.queue import enqueue_games, worker_alive
from chessdata.models import Game, Player
from chessdata.services.chesscom import ChessComError, sync_player

logger = logging.getLogger("analysis.queue")


# --- analysis board --------------------------------------------------------


@login_required
def analysis_board(request):
    """The interactive analysis board: Stockfish WASM in the browser.

    Two modes: analysis from the start position (live, updating after every
    move) and analysis of a user-set-up custom position. No server state —
    the page is pure frontend (Chessground + chess.js + stockfish.wasm).
    """
    return render(request, "analysis/board.html")


# --- players ---------------------------------------------------------------


@login_required
def players_list(request):
    players = Player.objects.annotate(
        game_count=Count("games"),
        turning_point_count=Count("games__turning_points"),
        queued_count=Count(
            "games__jobs",
            filter=models.Q(games__jobs__status__in=["queued", "running"]),
        ),
    )
    return render(
        request,
        "analysis/players_list.html",
        {"players": players, "worker": _worker_context()},
    )


def _worker_context() -> dict:
    heartbeat = WorkerHeartbeat.objects.filter(pk=1).first()
    return {
        "alive": worker_alive(),
        "heartbeat": heartbeat,
    }


@login_required
def players_fetch(request):
    """POST: fetch recent chess.com games for a username (synchronous)."""
    if request.method != "POST":
        return redirect("analysis:players_list")
    username = (request.POST.get("username") or "").strip()
    if not username:
        messages.error(request, "Enter a chess.com username.")
        return redirect("analysis:players_list")
    months = int(request.POST.get("months", 3))
    limit = request.POST.get("limit") or None
    try:
        report = sync_player(username, months=months, limit=limit and int(limit))
    except ChessComError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{username}: {report}")
    return redirect("analysis:players_list")


def _parse_date_param(value: str):
    """Parse a YYYY-MM-DD form value; return None when empty/invalid."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


@login_required
def players_detail(request, username):
    player = get_object_or_404(Player, username=username)
    # NOTE: annotate() with an aggregate clears Meta's default ordering, so
    # order explicitly — the date grouping in the template relies on it.
    games = player.games.select_related("last_run").annotate(
        tp_count=Count("turning_points")
    ).order_by("-end_time")

    result_filter = request.GET.get("result", "all")
    if result_filter in (Game.Result.WIN, Game.Result.LOSS, Game.Result.DRAW):
        games = games.filter(result=result_filter)
    time_filter = request.GET.get("time", "")
    if time_filter in TIME_CLASSES:
        games = games.filter(time_class=time_filter)
    from_date = _parse_date_param(request.GET.get("from", ""))
    if from_date:
        games = games.filter(end_time__date__gte=from_date)
    to_date = _parse_date_param(request.GET.get("to", ""))
    if to_date:
        games = games.filter(end_time__date__lte=to_date)
    analyzed_filter = request.GET.get("analyzed", "")
    if analyzed_filter == "1":
        games = games.filter(last_run__isnull=False)
    elif analyzed_filter == "0":
        games = games.filter(last_run__isnull=True)

    stats = player.games.aggregate(
        total=Count("uuid"),
        analyzed=Count("last_run", distinct=True),
        losses=Count("uuid", filter=models.Q(result=Game.Result.LOSS)),
    )
    type_stats = list(
        player.games.values("time_class")
        .annotate(
            total=Count("uuid"),
            losses=Count("uuid", filter=models.Q(result=Game.Result.LOSS)),
        )
        .order_by("-total")
    )
    job_counts = {
        status: AnalysisJob.objects.filter(game__player=player, status=status).count()
        for status, _label in AnalysisJob.JobStatus.choices
    }

    if request.method == "POST" and request.POST.get("action") == "queue":
        return _enqueue_batch(request, player)
    if request.method == "POST" and request.POST.get("action") == "export":
        return _export_pdf(request, player)

    context = {
        "player": player,
        "games": games,
        "stats": stats,
        "type_stats": type_stats,
        "job_counts": job_counts,
        "time_classes": TIME_CLASSES,
        "result_filter": result_filter,
        "time_filter": time_filter,
        "from_date": request.GET.get("from", ""),
        "to_date": request.GET.get("to", ""),
        "analyzed_filter": analyzed_filter,
        "worker": _worker_context(),
    }
    return render(request, "analysis/players_detail.html", context)


def _export_pdf(request, player):
    """POST: render the turning-point PDF for the selected date range."""
    games = player.games.order_by("end_time")
    if request.POST.get("lost_only") == "on":
        games = games.filter(result=Game.Result.LOSS)
    time_class = request.POST.get("time_class") or ""
    if time_class in TIME_CLASSES:
        games = games.filter(time_class=time_class)
    from_date = _parse_date_param(request.POST.get("from", ""))
    if from_date:
        games = games.filter(end_time__date__gte=from_date)
    to_date = _parse_date_param(request.POST.get("to", ""))
    if to_date:
        games = games.filter(end_time__date__lte=to_date)
    games = list(games)

    if not games:
        messages.error(request, "No games in this date range.")
        return redirect("analysis:players_detail", username=player.username)

    answers = request.POST.get("answers", "inline")
    if answers not in ("inline", "end", "none"):
        answers = "inline"
    pdf_bytes = render_game_pdf(games, answers=answers)
    suffix = "" if answers == "inline" else f"_{answers}"
    response = FileResponse(io.BytesIO(pdf_bytes), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="{player.username}_turning_points{suffix}.pdf"'
    )
    return response


def _enqueue_batch(request, player):
    """POST: enqueue games for the worker (per-time-class profiles)."""
    lost_only = request.POST.get("lost_only") == "on"
    time_class = request.POST.get("time_class") or ""
    games = player.games.order_by("-end_time")
    if lost_only:
        games = games.filter(result=Game.Result.LOSS)
    if time_class in TIME_CLASSES:
        games = games.filter(time_class=time_class)
    games = list(games)

    created, skipped = enqueue_games(games)
    eta = sum(estimated_seconds_per_game(g) for g in games[:created])
    messages.success(
        request,
        f"Queued {created} games for analysis (estimated {format_duration(eta)}"
        f"{', ' + str(skipped) + ' already queued' if skipped else ''}).",
    )
    if not worker_alive():
        messages.warning(
            request,
            "The worker is not running — start it with: "
            ".venv/bin/python manage.py analyze_worker",
        )
    return redirect("analysis:players_detail", username=player.username)


# --- games -----------------------------------------------------------------


@login_required
def game_detail(request, uuid):
    game = get_object_or_404(
        Game.objects.select_related("player", "last_run"), uuid=uuid
    )
    if request.method == "POST" and request.POST.get("action") == "queue":
        created, skipped = enqueue_games([game])
        if created:
            messages.success(request, "Queued for re-analysis.")
        else:
            messages.info(request, "This game is already queued or running.")
        return redirect("analysis:game_detail", uuid=uuid)
    evals = list(game.evals.all())
    total_plies = evals[-1].ply if evals else 0

    try:
        current_ply = int(request.GET.get("move", total_plies))
    except ValueError:
        current_ply = total_plies
    current_ply = max(0, min(current_ply, total_plies))

    turning_points = list(game.turning_points.all())
    tp_plies = {tp.ply for tp in turning_points}

    # eval curve for the chart (mates clamped to +-2000 for readability)
    chart_data = []
    for e in evals:
        if e.eval_mate is not None:
            y = 2000 if e.eval_mate > 0 else -2000
        else:
            y = e.eval_cp or 0
        chart_data.append(
            {
                "ply": e.ply,
                "san": e.san,
                "y": y,
                "evalText": format_eval(e.eval_cp, e.eval_mate),
                "isTurningPoint": e.ply in tp_plies,
            }
        )

    # clickable movetext grouped by move number
    movelist = []
    for e in evals:
        number = (e.ply + 1) // 2
        is_white = e.ply % 2 == 1
        if is_white:
            movelist.append({"number": number, "white": e, "black": None})
        else:
            movelist[-1]["black"] = e

    context = {
        "game": game,
        "current_ply": current_ply,
        "total_plies": total_plies,
        "board_svg": board_svg(game, current_ply) if current_ply else "",
        "movelist": movelist,
        "turning_points": turning_points,
        "tp_plies": tp_plies,
        "chart_data_json": json.dumps(chart_data),
        "threshold_cp": (
            game.last_run.threshold_cp
            if game.last_run
            else settings.DEFAULT_TURNING_POINT_THRESHOLD_CP
        ),
    }
    return render(request, "analysis/game_detail.html", context)


@login_required
def game_pdf(request, uuid):
    """Render the turning-point report of one game as a PDF download."""
    game = get_object_or_404(Game, uuid=uuid)
    pdf_bytes = render_game_pdf([game])
    response = FileResponse(
        io.BytesIO(pdf_bytes), content_type="application/pdf"
    )
    response["Content-Disposition"] = f'attachment; filename="{game.uuid}.pdf"'
    return response


@login_required
def tp_llm(request, uuid, tp_id):
    """POST: (re)generate the LLM explanation for one turning point."""
    tp = get_object_or_404(
        TurningPoint.objects.select_related("game"), game__uuid=uuid, pk=tp_id
    )
    if request.method == "POST":
        try:
            enriched = enrich_tp(tp, force=True)
        except LlmConfigError as exc:
            messages.error(request, str(exc))
        else:
            if enriched.llm_status == TurningPoint.LlmStatus.DONE:
                messages.success(request, f"Explanation generated ({enriched.llm_model}).")
            else:
                messages.error(request, f"Generation failed: {enriched.llm_error[:200]}")
    return redirect("analysis:game_detail", uuid=uuid)

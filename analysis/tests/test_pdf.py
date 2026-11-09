import io

import pytest
from django.utils import timezone
from pypdf import PdfReader

from analysis.models import AnalysisRun, TurningPoint
from analysis.pdf import build_html, render_game_pdf
from chessdata.models import Game, Player

pytestmark = pytest.mark.django_db

PGN = (
    '[Event "Live Chess"]\n[White "TestUser"]\n[Black "opponent"]\n[Result "0-1"]\n\n'
    "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. Nxe5 Nxe5 0-1"
)

A4_POINTS = (595.2, 841.9)


def make_game(uuid, *, tp_count=3, time_class="blitz", end_time=None):
    player = Player.objects.get_or_create(username="testuser")[0]
    game = Game.objects.create(
        uuid=uuid, player=player,
        white_username="TestUser", black_username="opponent",
        player_side="w", player_result_raw="resigned",
        result=Game.Result.LOSS, pgn=PGN,
        end_time=end_time or timezone.now(), url="https://example.com",
        time_class=time_class,
    )
    run = AnalysisRun.objects.create(
        game=game, engine_name="Stockfish", engine_version="18",
        depth=18, threshold_cp=150, status=AnalysisRun.RunStatus.DONE,
    )
    for i in range(tp_count):
        ply = 7 + i * 4
        TurningPoint.objects.create(
            game=game, run=run, ply=ply, move_number=f"{(ply + 1) // 2}. Nxe5",
            san_played="Nxe5", san_best="d3",
            fen_before="fen-before", fen_after="fen-after",
            eval_before_cp=5, eval_after_cp=-250, drop_cp=255,
            classification=TurningPoint.Classification.HUNG_PIECE,
            rule_text="You played Nxe5 and Nxe5 simply captures the knight.",
            llm_text="You hung the knight.\n\n**Lesson:** keep knights defended.",
        )
    return game


@pytest.fixture
def game_with_tps():
    return make_game("pdftest1")


def _page_count(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def _page_size(pdf: bytes):
    page = PdfReader(io.BytesIO(pdf)).pages[0]
    return page.mediabox.width, page.mediabox.height


def test_build_html_structure(game_with_tps):
    html = build_html([game_with_tps])
    assert html.count('class="tp"') == 3
    assert html.count("<svg") == 3  # one board diagram per turning point
    assert "TestUser vs opponent" in html
    assert "Hung a piece" in html
    assert "You hung the knight." in html


def test_build_html_empty_game():
    game = make_game("pdftest2", tp_count=0)
    html = build_html([game])
    assert "No turning points" in html


def test_render_game_pdf(game_with_tps):
    pdf = render_game_pdf([game_with_tps])
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF")


def test_grid_fits_six_per_a4_page():
    game = make_game("pdftest3", tp_count=6)
    pdf = render_game_pdf([game])
    assert _page_count(pdf) == 1  # 6 turning points fit on one A4 page
    width, height = _page_size(pdf)
    assert width == pytest.approx(A4_POINTS[0], abs=1)
    assert height == pytest.approx(A4_POINTS[1], abs=1)


def test_grid_flows_to_second_page():
    game = make_game("pdftest4", tp_count=7)
    pdf = render_game_pdf([game])
    assert _page_count(pdf) == 2  # the 7th position overflows to page 2


def test_masked_grid_is_six_per_page_plus_answer_key():
    game = make_game("pdftest5", tp_count=6)
    pdf = render_game_pdf([game], answers="end")
    assert _page_count(pdf) == 2  # 1 page of positions + 1 page of answers


def test_end_mode_hides_reasons_and_adds_answer_key(game_with_tps):
    html = build_html([game_with_tps], answers="end")
    # positions are shown, but no best move / eval / classification in the grid
    assert html.count('class="tp"') == 3
    assert 'class="answers"' in html
    assert "keep knights defended" in html  # appears in the answer key
    # the grid cell itself must not reveal the answer: check the grid section only
    # (note: the board SVG contains square names like "d3" in class attributes,
    # so check for the rendered answer markup, not the bare string)
    grid = html.split('class="grid"')[1].split('class="answers"')[0]
    assert '<span class="best">' not in grid  # best move hidden
    assert 'class="badge"' not in grid  # classification hidden
    assert 'class="evals"' not in grid  # eval numbers hidden


def test_none_mode_has_no_answers_at_all(game_with_tps):
    html = build_html([game_with_tps], answers="none")
    assert 'class="answers"' not in html
    assert '<span class="best">' not in html
    assert "Hung a piece" not in html
    assert 'class="evals"' not in html


def test_end_mode_answer_key_after_all_games():
    from datetime import datetime, timezone as dt_timezone

    game_a = make_game(
        "pdftest6", tp_count=2,
        end_time=datetime(2026, 8, 1, tzinfo=dt_timezone.utc),
    )
    game_b = make_game(
        "pdftest7", tp_count=2,
        end_time=datetime(2026, 8, 2, tzinfo=dt_timezone.utc),
    )
    html = build_html([game_a, game_b], answers="end")
    # both game sections come before the single answer key section
    assert html.index("2026-08-01") < html.index('class="answers"')
    assert html.index("2026-08-02") < html.index('class="answers"')
    # one key, two per-game subheads
    assert html.count('class="answers"') == 1
    assert html.count("<h3>") == 2


def test_large_boards_in_end_and_none_modes(game_with_tps):
    for mode in ("end", "none"):
        html = build_html([game_with_tps], answers=mode)
        assert html.count('board large') == 3
        assert "width: 65mm" in html
    html = build_html([game_with_tps], answers="inline")
    assert "board large" not in html


def test_masked_kwarg_is_backward_compatible_alias(game_with_tps):
    assert build_html([game_with_tps], masked=True) == build_html(
        [game_with_tps], answers="end"
    )


def test_unmasked_shows_everything(game_with_tps):
    html = build_html([game_with_tps], answers="inline")
    assert 'class="answers"' not in html
    assert "d3" in html
    assert "Hung a piece" in html

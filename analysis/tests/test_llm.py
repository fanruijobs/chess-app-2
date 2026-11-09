import json

import openai
import pytest

from analysis.llm import LlmConfigError, build_user_prompt, enrich_tp, get_client
from analysis.models import AnalysisRun, TurningPoint
from chessdata.models import Game, Player

pytestmark = pytest.mark.django_db


@pytest.fixture
def tp():
    player = Player.objects.create(username="testuser")
    game = Game.objects.create(
        uuid="llmtest1",
        player=player,
        white_username="TestUser",
        black_username="opponent",
        player_side="w",
        player_result_raw="resigned",
        result=Game.Result.LOSS,
        pgn="1. e4 e5 1-0",
        end_time="2026-08-01T12:00:00Z",
        url="https://example.com",
    )
    run = AnalysisRun.objects.create(
        game=game, engine_name="Stockfish", engine_version="18",
        depth=18, threshold_cp=150, status=AnalysisRun.RunStatus.DONE,
    )
    return TurningPoint.objects.create(
        game=game, run=run, ply=3, move_number="2... e5",
        san_played="e5", san_best="Nf6",
        fen_before="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
        fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2",
        eval_before_cp=20, eval_after_cp=-250, drop_cp=270,
        classification=TurningPoint.Classification.UNSPECIFIED_BLUNDER,
        rule_text="Your evaluation dropped by 270 centipawns after e5.",
    )


class FakeResponse:
    def __init__(self, content, model="deepseek-chat"):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]
        self.model = model


class FakeClient:
    def __init__(self, content=None, error=None):
        self.content = content or json.dumps(
            {
                "summary": "You left your e5 pawn undefended.",
                "lesson": "Every pawn move leaves squares behind it.",
                "fix": "Defend e5 with Nf6 before advancing elsewhere.",
            }
        )
        self.error = error
        self.calls = 0

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return FakeResponse(self.content)


def test_enrich_tp_success(tp):
    enriched = enrich_tp(tp, client=FakeClient())
    assert enriched.llm_status == TurningPoint.LlmStatus.DONE
    assert "undefended" in enriched.llm_text
    assert "**Lesson:**" in enriched.llm_text
    assert enriched.llm_model == "deepseek-chat"
    assert enriched.llm_created_at is not None
    assert enriched.llm_error == ""


def test_enrich_tp_idempotent(tp):
    client = FakeClient()
    enrich_tp(tp, client=client)
    assert client.calls == 1
    # second call without force is a no-op
    assert enrich_tp(tp, client=client) is None
    assert client.calls == 1


def test_enrich_tp_failure_marks_failed(tp, monkeypatch):
    monkeypatch.setattr("analysis.llm.time.sleep", lambda s: None)

    class FakeClientError(FakeClient):
        def create(self, **kwargs):
            self.calls += 1
            raise openai.APIConnectionError(
                message="connection failed", request=None
            )

    client = FakeClientError()
    enriched = enrich_tp(tp, client=client)
    assert enriched.llm_status == TurningPoint.LlmStatus.FAILED
    assert "connection failed" in enriched.llm_error
    assert client.calls == 3  # retried MAX_ATTEMPTS times


def test_get_client_requires_key(settings):
    settings.DEEPSEEK_API_KEY = ""
    with pytest.raises(LlmConfigError):
        get_client()


def test_build_user_prompt(tp):
    prompt = build_user_prompt(tp)
    assert "e5" in prompt
    assert "2... e5" in prompt
    assert "Unspecified blunder" in prompt
    assert tp.fen_after in prompt

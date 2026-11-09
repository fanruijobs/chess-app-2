"""DeepSeek LLM client: natural-language explanations for turning points.

DeepSeek exposes an OpenAI-compatible API (base_url https://api.deepseek.com).
Requires DEEPSEEK_API_KEY in the environment (see .env.example).
"""

import json
import logging
import time

import openai
from django.conf import settings
from django.utils import timezone

from analysis.formatting import format_eval
from analysis.models import TurningPoint

logger = logging.getLogger("analysis.llm")

MODEL = "deepseek-chat"
MAX_ATTEMPTS = 3

SYSTEM_PROMPT = (
    "You are a chess coach explaining one concrete blunder to an amateur player. "
    "Be specific, concrete and encouraging; explain every chess term you use. "
    'Reply with JSON only: {"summary": "<at most 60 words, what happened and why it '
    'was a mistake>", "lesson": "<one sentence, the general idea to learn>", '
    '"fix": "<one sentence, the concrete move or plan that would have been better>"}.'
)


class LlmConfigError(Exception):
    """DEEPSEEK_API_KEY is not set."""


def get_client() -> openai.OpenAI:
    key = settings.DEEPSEEK_API_KEY
    if not key:
        raise LlmConfigError(
            "DEEPSEEK_API_KEY is not set — add it to your environment "
            "(see .env.example) to enable LLM explanations."
        )
    return openai.OpenAI(
        api_key=key, base_url="https://api.deepseek.com", timeout=60.0, max_retries=3
    )


def build_user_prompt(tp: TurningPoint) -> str:
    game = tp.game
    opponent = game.black_username if game.player_side == "w" else game.white_username
    lines = [
        f"Player: {game.player.username} (playing {'White' if game.player_side == 'w' else 'Black'})",
        f"Opponent: {opponent}",
        f"Position after the blunder (FEN): {tp.fen_after}",
        f"Move played: {tp.move_number}",
        f"Engine's best move instead: {tp.san_best or 'unknown'}",
        "Evaluation before the move: "
        + format_eval(tp.eval_before_cp, tp.eval_before_mate),
        "Evaluation after the move: "
        + format_eval(tp.eval_after_cp, tp.eval_after_mate),
        f"Classification: {tp.get_classification_display()}",
        f"Rule-based note: {tp.rule_text}",
    ]
    return "\n".join(lines)


def render_answer(data: dict) -> str:
    """Render the JSON answer into markdown-ish text."""
    parts = []
    if data.get("summary"):
        parts.append(data["summary"])
    if data.get("lesson"):
        parts.append(f"**Lesson:** {data['lesson']}")
    if data.get("fix"):
        parts.append(f"**Fix:** {data['fix']}")
    return "\n\n".join(parts)


def enrich_tp(tp: TurningPoint, *, force: bool = False, client=None):
    """Generate/store the LLM explanation for one turning point.

    Idempotent: skips DONE ones unless force. On final failure sets
    llm_status=FAILED and returns the tp. Returns None if skipped.
    """
    if not force and tp.llm_status == TurningPoint.LlmStatus.DONE:
        return None
    client = client or get_client()

    try:
        response = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = client.chat.completions.create(
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": build_user_prompt(tp)},
                    ],
                    temperature=0.3,
                    max_tokens=400,
                    response_format={"type": "json_object"},
                )
                break
            except (openai.RateLimitError, openai.APIConnectionError) as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                logger.warning("LLM attempt %d failed for tp %s: %s", attempt + 1, tp.pk, exc)
                time.sleep(2**attempt)

        content = response.choices[0].message.content
        data = json.loads(content)
        tp.llm_text = render_answer(data)
        tp.llm_model = getattr(response, "model", "") or MODEL
        tp.llm_status = TurningPoint.LlmStatus.DONE
        tp.llm_error = ""
        tp.llm_created_at = timezone.now()
        tp.save(
            update_fields=["llm_text", "llm_model", "llm_status", "llm_error", "llm_created_at"]
        )
        return tp
    except LlmConfigError:
        raise  # misconfiguration must fail fast, not mark turning points failed
    except Exception as exc:  # noqa: BLE001 - record any failure on the tp
        logger.warning("LLM enrichment failed for tp %s: %s", tp.pk, exc)
        tp.llm_status = TurningPoint.LlmStatus.FAILED
        tp.llm_error = str(exc)
        tp.save(update_fields=["llm_status", "llm_error"])
        return tp

"""Printable PDF export of turning points (WeasyPrint HTML -> PDF).

Layout: 2 columns x 3 rows of turning points per A4 page. Three answer modes:
- "inline" (default): reason + best move + explanation under each position,
  boards at the compact size so 6 cells fit a page.
- "end": positions only (with a "what should you have played?" hint) and ONE
  answer key at the very end of the document, after all games; boards render
  ~1/3 larger since the cells carry no text.
- "none": positions only, no answers anywhere; large boards.
"""

from django.template.loader import render_to_string

from analysis.boards import board_svg
from analysis.formatting import format_eval

ANSWERS_MODES = ("inline", "end", "none")

CELL_BOARD_SIZE = 185  # px (~49mm); compact size for inline answers
CELL_BOARD_SIZE_LARGE = 246  # px (~65mm, ~1/3 larger) for end/none modes

# Cell text is compact so 6 cells fit an A4 page; the full text lives in the
# web UI (and in the end-mode answer key).
RULE_MAX_CHARS = 160
SUMMARY_MAX_CHARS = 200


def _llm_summary(text: str) -> str:
    """First paragraph of the LLM explanation, truncated for the cell."""
    summary = text.split("\n\n")[0] if text else ""
    if len(summary) > SUMMARY_MAX_CHARS:
        return summary[: SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    return summary


def _rule_text(text: str) -> str:
    if len(text) > RULE_MAX_CHARS:
        return text[: RULE_MAX_CHARS - 1].rstrip() + "…"
    return text


def _cell_size(answers: str) -> int:
    return CELL_BOARD_SIZE_LARGE if answers != "inline" else CELL_BOARD_SIZE


def build_sections(games, *, answers: str = "inline") -> list[dict]:
    """One dict per game with its turning points pre-rendered for the template."""
    if answers not in ANSWERS_MODES:
        raise ValueError(f"unknown answers mode {answers!r}")
    sections = []
    for game in games:
        cells = []
        key_entries = []
        for tp in game.turning_points.all():
            item = {
                "tp": tp,
                "board_svg": board_svg(game, tp.ply, size=_cell_size(answers)),
                "eval_before_text": format_eval(tp.eval_before_cp, tp.eval_before_mate),
                "eval_after_text": format_eval(tp.eval_after_cp, tp.eval_after_mate),
            }
            if answers == "inline":
                item.update(
                    rule_text=_rule_text(tp.rule_text),
                    llm_summary=_llm_summary(tp.llm_text),
                )
            cells.append(item)
            if answers == "end":
                key_entries.append(item)
        sections.append(
            {
                "game": game,
                "opponent": (
                    game.black_username
                    if game.player_side == "w"
                    else game.white_username
                ),
                "cells": cells,
                "answers": key_entries,
            }
        )
    return sections


def build_html(games, *, answers: str = "inline", masked=None) -> str:
    if masked is not None:  # backward-compatible alias
        answers = "end" if masked else "inline"
    return render_to_string(
        "analysis/pdf/game_pdf.html",
        {"sections": build_sections(games, answers=answers), "answers_mode": answers},
    )


def render_game_pdf(games, *, answers: str = "inline", masked=None) -> bytes:
    """Render the turning-point report for the given games as PDF bytes."""
    # Lazy import: WeasyPrint needs GTK/Pango at import time, so keep the web
    # app runnable on machines that only use non-PDF features.
    from weasyprint import HTML

    return HTML(string=build_html(games, answers=answers, masked=masked)).write_pdf()

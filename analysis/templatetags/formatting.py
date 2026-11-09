from django import template

from analysis.formatting import format_eval as _format_eval

register = template.Library()


@register.simple_tag
def format_eval(cp, mate):
    """Render an (eval_cp, eval_mate) pair, e.g. '+1.2' or 'mated in 2'."""
    return _format_eval(cp, mate)

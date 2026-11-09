"""Human-readable formatting of evaluations."""


def format_eval(cp: int | None, mate: int | None) -> str:
    """'+1.2', '-2.3', '0.0', 'mate in 3' or 'mated in 2'."""
    if mate is not None:
        return f"mate in {mate}" if mate > 0 else f"mated in {abs(mate)}"
    if cp is None:
        return "0.0"
    sign = "+" if cp > 0 else ("-" if cp < 0 else "")
    return f"{sign}{abs(cp) / 100:.1f}"

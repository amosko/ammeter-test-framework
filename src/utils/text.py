"""Small formatting helpers shared by the reports, the CLI and the sampler."""


def plural(count: int, noun: str) -> str:
    """English plural for generated output; "1 runs" reads like a bug in the tool."""
    return noun if count == 1 else f"{noun}s"

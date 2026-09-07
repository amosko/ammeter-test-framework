"""Logging setup shared by the command line tools."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional


def configure_logging(log_dir: Optional[Path] = None, level: int = logging.INFO) -> Optional[Path]:
    """Log to stderr and, if log_dir is given, to a timestamped file. Returns the log file path."""
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    handlers: list[logging.Handler] = [console]

    log_file = None
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{datetime.now():%Y%m%d_%H%M%S}_ammeter_tests.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8", delay=True)  # created on first message
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        handlers.append(file_handler)

    logging.basicConfig(level=level, handlers=handlers, force=True)
    return log_file

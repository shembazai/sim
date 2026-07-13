"""Logging configuration for SIM.

Two sinks:
1. Console, via RichHandler — human-readable, used interactively and over
   SSH (pure ANSI, no GUI dependency, confirmed compatible with the
   Kubuntu-laptop-over-SSH management workflow).
2. journald, via the optional `systemd-python` binding — best-effort. Rocky
   Linux 10 target hosts run systemd, so this is expected to be present in
   production, but the import is guarded so `sim --dry-run` or unit tests on
   non-systemd dev machines don't fail on import.
"""

from __future__ import annotations

import logging
from pathlib import Path

from rich.logging import RichHandler

LOG_FORMAT = "%(message)s"
DEFAULT_LOG_DIR = Path("/opt/k1/logs")


def _try_add_journald_handler(logger: logging.Logger) -> bool:
    try:
        from systemd.journal import JournalHandler  # type: ignore[import-not-found]
    except ImportError:
        return False
    handler = JournalHandler(SYSLOG_IDENTIFIER="sim")
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    logger.addHandler(handler)
    return True


def configure_logging(level: str = "INFO", log_dir: Path = DEFAULT_LOG_DIR) -> logging.Logger:
    """Configure and return the 'sim' root logger. Idempotent: safe to call
    more than once (e.g. once at CLI startup, once in tests) without
    duplicating handlers."""
    logger = logging.getLogger("sim")
    logger.setLevel(level)

    if not any(isinstance(h, RichHandler) for h in logger.handlers):
        console_handler = RichHandler(
            show_time=True, show_path=False, rich_tracebacks=True, markup=True
        )
        console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(console_handler)

    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_dir / "sim.log", encoding="utf-8")
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            )
            logger.addHandler(file_handler)
        except OSError:
            # e.g. /opt/k1/logs not writable yet during Phase 0, before
            # init_k1_environment has run — console logging still works.
            logger.warning(
                "Could not open %s for writing; continuing with console-only logging.",
                log_dir / "sim.log",
            )

    journald_present = any(
        h.__class__.__name__ == "JournalHandler" for h in logger.handlers
    )
    if not journald_present:
        _try_add_journald_handler(logger)

    return logger

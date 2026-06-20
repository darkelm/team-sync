"""Lightweight logging setup — a pilot audit trail without hosting.

`configure_logging()` routes the standard logging module — our own loggers, the
Figma module's logger, slack_bolt, etc. — to a rotating file plus the console,
with timestamps and levels. `get_logger()` returns a namespaced logger.

The heavyweight value (structured JSON, remote aggregation, per-level routing in
prod) waits for hosting; this just gives a durable, level-tagged record of what
the bot did and errored during a local pilot. Call configure_logging() once at a
process entry point; it's a no-op if already configured.
"""
from __future__ import annotations
import logging
import os
from logging.handlers import RotatingFileHandler

LOG_FILE = os.environ.get("SYNCBOT_LOG_FILE", "data/syncbot.log")
_configured = False


def configure_logging(level: int = logging.INFO, logfile: str = LOG_FILE) -> None:
    global _configured
    if _configured:
        return
    os.makedirs(os.path.dirname(logfile) or ".", exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)
    file_handler = RotatingFileHandler(logfile, maxBytes=5_000_000, backupCount=3)
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(file_handler)
    root.addHandler(console)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

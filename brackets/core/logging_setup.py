from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import ROOT, env_bool

LOG_NAME = "T0G_Tournament_Bot"
LOG_FMT = "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"

def setup_logging() -> logging.Logger:
    """Configure console + rotating file logging.

    Cleanup Roadmap v1: centralize logging config (no behavior changes).
    - Uses ROOT/logs/bot.log
    - Console logging via basicConfig
    - Rotating file handler attached to root logger
    - Discord logger kept at INFO unless DEBUG=true
    """
    debug = env_bool("DEBUG", False)
    level = logging.DEBUG if debug else logging.INFO

    log_dir: Path = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file: Path = log_dir / "bot.log"

    # basicConfig only has effect once per process (unless force=True),
    # so calling it here is safe and avoids duplicate console handlers.
    logging.basicConfig(level=level, format=LOG_FMT)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Avoid adding duplicate file handlers if setup_logging() is called again.
    for h in list(root_logger.handlers):
        if isinstance(h, RotatingFileHandler):
            # Keep the existing one (assume configured)
            break
    else:
        fh = RotatingFileHandler(
            log_file,
            maxBytes=5_000_000,  # 5 MB
            backupCount=5,
            encoding="utf-8",
        )
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter(LOG_FMT))
        root_logger.addHandler(fh)

    # Discord's internal logger can be noisy
    discord_logger = logging.getLogger("discord")
    discord_logger.setLevel(level if debug else logging.INFO)

    return logging.getLogger(LOG_NAME)

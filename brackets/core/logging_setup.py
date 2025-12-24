from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import ROOT, env_bool


def setup_logging(logger_name: str = "T0G_Tournament_Bot") -> logging.Logger:
    """Configure console + rotating file logging.

    - Safe to call multiple times (won't duplicate handlers)
    - Uses DEBUG=true in .env to increase verbosity
    """
    log_level = logging.DEBUG if env_bool("DEBUG", False) else logging.INFO

    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "bot.log"

    # Root logger
    root = logging.getLogger()
    root.setLevel(log_level)

    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")

    # Avoid duplicate handlers if reloaded
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setLevel(log_level)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        fh = RotatingFileHandler(
            log_file,
            maxBytes=5_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        fh.setLevel(log_level)
        fh.setFormatter(fmt)
        root.addHandler(fh)

    # Discord.py can be noisy; keep it at INFO unless DEBUG
    discord_logger = logging.getLogger("discord")
    discord_logger.setLevel(logging.DEBUG if log_level == logging.DEBUG else logging.INFO)

    return logging.getLogger(logger_name)

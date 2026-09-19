"""Executable entry point."""

from __future__ import annotations

import logging

from .config import ConfigurationError, load_settings
from .discord_bot import create_bot


def main() -> None:
    try:
        settings = load_settings()
    except ConfigurationError as error:
        raise SystemExit(f"Configuration error: {error}") from error

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("typesafe_sdk").setLevel(logging.INFO)
    create_bot(settings).run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()

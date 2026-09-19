from __future__ import annotations

import pytest

from messageeval_discord.config import ConfigurationError, load_settings
from messageeval_discord.rating import RATING_LABELS


def valid_environment() -> dict[str, str]:
    environment = {
        "DISCORD_TOKEN": "discord-secret",
        "TYPESAFE_API_KEY": "typesafe-secret",
    }
    environment.update({f"EMOJI_{label.upper()}": f"rating_{label}" for label in RATING_LABELS})
    return environment


def test_load_settings_uses_expected_defaults() -> None:
    settings = load_settings(valid_environment())

    assert settings.typesafe_model == "jev-latest"
    assert settings.log_level == "INFO"
    assert settings.emoji_names["book_move"] == "rating_book_move"


def test_load_settings_rejects_missing_value() -> None:
    environment = valid_environment()
    del environment["EMOJI_BLUNDER"]

    with pytest.raises(ConfigurationError, match="EMOJI_BLUNDER"):
        load_settings(environment)


def test_load_settings_rejects_duplicate_emoji_names() -> None:
    environment = valid_environment()
    environment["EMOJI_GREAT"] = environment["EMOJI_BRILLIANT"]

    with pytest.raises(ConfigurationError, match="must be distinct"):
        load_settings(environment)


def test_load_settings_rejects_unknown_log_level() -> None:
    environment = valid_environment()
    environment["LOG_LEVEL"] = "verbose"

    with pytest.raises(ConfigurationError, match="Invalid LOG_LEVEL"):
        load_settings(environment)

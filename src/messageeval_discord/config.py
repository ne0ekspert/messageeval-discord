"""Environment-backed application configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv

from .rating import RATING_LABELS, RatingLabel


class ConfigurationError(ValueError):
    """Raised when required environment configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    typesafe_api_key: str
    typesafe_model: str
    emoji_names: Mapping[RatingLabel, str]
    log_level: str


def _required(environment: Mapping[str, str], key: str) -> str:
    value = environment.get(key, "").strip()
    if not value:
        raise ConfigurationError(f"Missing required environment variable: {key}")
    return value


def load_settings(environment: Mapping[str, str] | None = None) -> Settings:
    """Load settings from ``.env`` and the process environment.

    Passing an explicit mapping skips dotenv loading and is intended for tests.
    """

    if environment is None:
        load_dotenv()
        environment = os.environ

    emoji_names: dict[RatingLabel, str] = {
        label: _required(environment, f"EMOJI_{label.upper()}") for label in RATING_LABELS
    }
    duplicates = sorted(
        name for name in set(emoji_names.values()) if list(emoji_names.values()).count(name) > 1
    )
    if duplicates:
        joined = ", ".join(duplicates)
        raise ConfigurationError(f"Custom emoji names must be distinct; duplicated: {joined}")

    log_level = environment.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        raise ConfigurationError(f"Invalid LOG_LEVEL: {log_level}")

    return Settings(
        discord_token=_required(environment, "DISCORD_TOKEN"),
        typesafe_api_key=_required(environment, "TYPESAFE_API_KEY"),
        typesafe_model=environment.get("TYPESAFE_MODEL", "jev-latest").strip() or "jev-latest",
        emoji_names=emoji_names,
        log_level=log_level,
    )

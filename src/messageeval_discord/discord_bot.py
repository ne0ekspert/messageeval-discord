"""Discord command wiring and message-analysis workflow."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping

import discord
from discord import app_commands
from discord.ext import commands
from typesafe_sdk import AsyncTypeSafeClient

from .config import Settings
from .rating import (
    RATING_LABELS,
    ConversationMessage,
    InvalidRatingResponse,
    MessageRater,
    RatingLabel,
    build_state,
)

LOGGER = logging.getLogger(__name__)
HISTORY_SIZE = 20
HISTORY_SCAN_LIMIT = 100


class EmojiConfigurationError(RuntimeError):
    """Raised when a guild cannot resolve the configured classification emojis."""


class InFlightMessages:
    """Process-local guard against duplicate concurrent evaluations."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._message_ids: set[int] = set()

    async def try_claim(self, message_id: int) -> bool:
        async with self._guard:
            if message_id in self._message_ids:
                return False
            self._message_ids.add(message_id)
            return True

    async def release(self, message_id: int) -> None:
        async with self._guard:
            self._message_ids.discard(message_id)


def resolve_custom_emojis(
    guild: discord.Guild, emoji_names: Mapping[RatingLabel, str]
) -> dict[RatingLabel, discord.Emoji]:
    """Resolve every configured name to one unique, usable guild emoji."""

    resolved: dict[RatingLabel, discord.Emoji] = {}
    errors: list[str] = []

    for label in RATING_LABELS:
        name = emoji_names[label]
        matches = [emoji for emoji in guild.emojis if emoji.name == name]
        if not matches:
            errors.append(f"{label}: missing `{name}`")
            continue
        if len(matches) > 1:
            errors.append(f"{label}: `{name}` is not unique")
            continue
        emoji = matches[0]
        if not emoji.is_usable():
            errors.append(f"{label}: `{name}` is not usable by this bot")
            continue
        resolved[label] = emoji

    if len({emoji.id for emoji in resolved.values()}) != len(resolved):
        errors.append("multiple labels resolve to the same emoji")

    if errors:
        raise EmojiConfigurationError("; ".join(errors))
    return resolved


def has_bot_analysis_reaction(
    message: discord.Message, emojis: Mapping[RatingLabel, discord.Emoji]
) -> bool:
    configured_ids = {emoji.id for emoji in emojis.values()}
    return any(
        reaction.me and getattr(reaction.emoji, "id", None) in configured_ids
        for reaction in message.reactions
    )


def _is_history_message(message: discord.Message) -> bool:
    return (
        not message.author.bot
        and message.type in {discord.MessageType.default, discord.MessageType.reply}
        and bool(message.content.strip())
    )


async def collect_history(message: discord.Message) -> list[ConversationMessage]:
    """Collect up to 20 qualifying messages before the target, oldest first."""

    selected: list[ConversationMessage] = []
    async for candidate in message.channel.history(
        limit=HISTORY_SCAN_LIMIT,
        before=message,
        oldest_first=False,
    ):
        if not _is_history_message(candidate):
            continue
        selected.append(
            ConversationMessage(author_id=candidate.author.id, content=candidate.content)
        )
        if len(selected) == HISTORY_SIZE:
            break

    selected.reverse()
    return selected


class AnalysisCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        settings: Settings,
        rater: MessageRater,
    ) -> None:
        self.bot = bot
        self.settings = settings
        self.rater = rater
        self.in_flight = InFlightMessages()

    @app_commands.guild_only()
    async def analyze_message(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not await self.in_flight.try_claim(message.id):
            await interaction.followup.send(
                "This message is already being analyzed.", ephemeral=True
            )
            return

        try:
            await self._analyze_claimed(interaction, message)
        finally:
            await self.in_flight.release(message.id)

    async def _analyze_claimed(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        guild = interaction.guild
        if guild is None or message.guild is None:
            await interaction.followup.send(
                "Message analysis is only available in servers.", ephemeral=True
            )
            return

        try:
            emojis = resolve_custom_emojis(guild, self.settings.emoji_names)
        except EmojiConfigurationError as error:
            LOGGER.warning("Emoji configuration failed for guild %s: %s", guild.id, error)
            await interaction.followup.send(
                f"The server's analysis emojis are not configured correctly: {error}",
                ephemeral=True,
            )
            return

        if has_bot_analysis_reaction(message, emojis):
            await interaction.followup.send(
                "This message has already been analyzed.", ephemeral=True
            )
            return

        target_content = message.content.strip()
        if not target_content:
            await interaction.followup.send("This message has no text to analyze.", ephemeral=True)
            return

        try:
            history = await collect_history(message)
        except (discord.Forbidden, discord.HTTPException) as error:
            LOGGER.warning(
                "Could not read history for guild=%s channel=%s message=%s: %s",
                guild.id,
                message.channel.id,
                message.id,
                type(error).__name__,
            )
            await interaction.followup.send(
                "I could not read this channel's message history.", ephemeral=True
            )
            return

        state = build_state(
            history,
            ConversationMessage(author_id=message.author.id, content=target_content),
        )

        try:
            result = await self.rater.rate(state)
        except InvalidRatingResponse as error:
            LOGGER.error("TypeSafe returned an invalid rating: %s", error)
            await interaction.followup.send(
                "The analysis service returned an invalid result. Please try again later.",
                ephemeral=True,
            )
            return
        except Exception as error:  # SDK errors share no stable public base across releases.
            request_id = getattr(error, "request_id", None)
            LOGGER.error(
                "TypeSafe request failed for message=%s error=%s request_id=%s",
                message.id,
                type(error).__name__,
                request_id,
            )
            await interaction.followup.send(
                "The analysis service is unavailable. Please try again later.", ephemeral=True
            )
            return

        try:
            await message.add_reaction(emojis[result.winner])
        except (discord.Forbidden, discord.HTTPException) as error:
            LOGGER.warning(
                "Could not add rating reaction to message=%s: %s",
                message.id,
                type(error).__name__,
            )
            await interaction.followup.send(
                "The message was evaluated, but I could not add its reaction. "
                "Check my Add Reactions permission.",
                ephemeral=True,
            )
            return

        LOGGER.info(
            "Rated guild=%s channel=%s message=%s label=%s model=%s request_id=%s",
            guild.id,
            message.channel.id,
            message.id,
            result.winner,
            result.model,
            result.request_id,
        )
        await interaction.followup.send(result.format_top_three(), ephemeral=True)


class MessageEvaluationBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(
            command_prefix=commands.when_mentioned,
            help_command=None,
            intents=intents,
        )
        self.settings = settings
        self.typesafe_client = AsyncTypeSafeClient(
            api_key=settings.typesafe_api_key,
            model=settings.typesafe_model,
        )

    async def setup_hook(self) -> None:
        analysis_cog = AnalysisCog(self, self.settings, MessageRater(self.typesafe_client))
        await self.add_cog(analysis_cog)
        self.tree.add_command(
            app_commands.ContextMenu(
                name="Analyze message",
                callback=analysis_cog.analyze_message,
            )
        )
        synced = await self.tree.sync()
        LOGGER.info("Synced %d global application command(s)", len(synced))

    async def close(self) -> None:
        await self.typesafe_client.aclose()
        await super().close()


def create_bot(settings: Settings) -> MessageEvaluationBot:
    return MessageEvaluationBot(settings)

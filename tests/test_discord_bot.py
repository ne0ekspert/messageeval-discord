from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

import discord
import pytest
from discord import app_commands

from messageeval_discord.config import Settings
from messageeval_discord.discord_bot import (
    AnalysisCog,
    EmojiConfigurationError,
    InFlightMessages,
    collect_history,
    has_bot_analysis_reaction,
    resolve_custom_emojis,
)
from messageeval_discord.rating import RATING_LABELS, RatingChoice, RatingResult


@dataclass
class FakeEmoji:
    name: str
    id: int
    usable: bool = True

    def is_usable(self) -> bool:
        return self.usable


@dataclass
class FakeAuthor:
    id: int
    bot: bool = False


@dataclass
class FakeHistoryMessage:
    id: int
    content: str
    author: FakeAuthor
    type: discord.MessageType = discord.MessageType.default


class FakeChannel:
    def __init__(self, messages=None, *, channel_id: int = 70) -> None:
        self.id = channel_id
        self.messages = list(messages or [])
        self.calls = 0

    def history(self, *, limit, before, oldest_first):
        assert limit == 100
        assert before is not None
        assert oldest_first is False
        self.calls += 1

        async def iterate():
            for message in self.messages[:limit]:
                yield message

        return iterate()


@dataclass
class FakeReaction:
    emoji: FakeEmoji
    me: bool


@dataclass
class FakeMessage:
    id: int = 500
    content: str = "Target text"
    author: FakeAuthor = field(default_factory=lambda: FakeAuthor(5))
    type: discord.MessageType = discord.MessageType.default
    guild: object | None = None
    channel: FakeChannel = field(default_factory=FakeChannel)
    reactions: list[FakeReaction] = field(default_factory=list)
    added_reactions: list[FakeEmoji] = field(default_factory=list)

    async def add_reaction(self, emoji: FakeEmoji) -> None:
        self.added_reactions.append(emoji)


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []

    async def send(self, content: str, *, ephemeral: bool) -> None:
        self.messages.append((content, ephemeral))


def make_emojis() -> dict[str, FakeEmoji]:
    return {
        label: FakeEmoji(name=f"rating_{label}", id=index + 100)
        for index, label in enumerate(RATING_LABELS)
    }


def make_settings() -> Settings:
    return Settings(
        discord_token="discord-secret",
        typesafe_api_key="typesafe-secret",
        typesafe_model="jev-latest",
        emoji_names={label: f"rating_{label}" for label in RATING_LABELS},
        log_level="INFO",
    )


def test_resolve_custom_emojis_requires_all_names_and_unique_matches() -> None:
    emojis = make_emojis()
    guild = SimpleNamespace(emojis=list(emojis.values()))

    assert resolve_custom_emojis(guild, make_settings().emoji_names) == emojis

    guild.emojis.pop()
    with pytest.raises(EmojiConfigurationError, match="missing"):
        resolve_custom_emojis(guild, make_settings().emoji_names)


def test_resolve_custom_emojis_rejects_unusable_emoji() -> None:
    emojis = make_emojis()
    emojis["blunder"].usable = False
    guild = SimpleNamespace(emojis=list(emojis.values()))

    with pytest.raises(EmojiConfigurationError, match="not usable"):
        resolve_custom_emojis(guild, make_settings().emoji_names)


def test_only_bot_owned_configured_reaction_counts_as_analyzed() -> None:
    emojis = make_emojis()
    message = FakeMessage(reactions=[FakeReaction(emojis["great"], me=False)])
    assert not has_bot_analysis_reaction(message, emojis)

    message.reactions.append(FakeReaction(FakeEmoji("unrelated", 999), me=True))
    assert not has_bot_analysis_reaction(message, emojis)

    message.reactions.append(FakeReaction(emojis["best"], me=True))
    assert has_bot_analysis_reaction(message, emojis)


async def test_collect_history_gets_nearest_twenty_eligible_messages_in_order() -> None:
    candidates = [
        FakeHistoryMessage(id=index, content=f"message {index}", author=FakeAuthor(index))
        for index in range(30, 0, -1)
    ]
    candidates.insert(1, FakeHistoryMessage(90, "bot", FakeAuthor(90, bot=True)))
    candidates.insert(2, FakeHistoryMessage(91, "   ", FakeAuthor(91)))
    candidates.insert(
        3,
        FakeHistoryMessage(
            92,
            "joined",
            FakeAuthor(92),
            type=discord.MessageType.new_member,
        ),
    )
    message = FakeMessage(channel=FakeChannel(candidates))

    history = await collect_history(message)

    assert len(history) == 20
    assert [item.content for item in history] == [f"message {index}" for index in range(11, 31)]


async def test_in_flight_messages_claims_atomically_and_releases() -> None:
    guard = InFlightMessages()

    first, second = await asyncio.gather(guard.try_claim(10), guard.try_claim(10))

    assert sorted([first, second]) == [False, True]
    await guard.release(10)
    assert await guard.try_claim(10)


class FakeRater:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def rate(self, state):
        self.calls.append(state)
        choices = tuple(
            RatingChoice(label, probability)
            for label, probability in [
                ("great", 0.51),
                ("best", 0.29),
                ("book_move", 0.20),
                ("brilliant", 0.0),
                ("excellent", 0.0),
                ("good", 0.0),
                ("inaccuracy", 0.0),
                ("mistake", 0.0),
                ("blunder", 0.0),
                ("miss", 0.0),
                ("forced", 0.0),
            ]
        )
        return RatingResult("great", choices, "jev-1.13.0", "request-1")


def make_interaction(guild) -> SimpleNamespace:
    return SimpleNamespace(guild=guild, followup=FakeFollowup())


def test_context_menu_is_a_guild_only_message_command() -> None:
    cog = AnalysisCog(SimpleNamespace(), make_settings(), FakeRater())

    menu = app_commands.ContextMenu(name="Analyze message", callback=cog.analyze_message)

    assert menu.type is discord.AppCommandType.message
    assert menu.guild_only is True
    assert menu.allowed_contexts is not None
    assert menu.allowed_contexts.guild is True


async def test_existing_bot_reaction_skips_history_and_api() -> None:
    emojis = make_emojis()
    guild = SimpleNamespace(id=1, emojis=list(emojis.values()))
    channel = FakeChannel()
    message = FakeMessage(
        guild=guild,
        channel=channel,
        reactions=[FakeReaction(emojis["best"], me=True)],
    )
    interaction = make_interaction(guild)
    rater = FakeRater()
    cog = AnalysisCog(SimpleNamespace(), make_settings(), rater)

    await cog._analyze_claimed(interaction, message)

    assert channel.calls == 0
    assert rater.calls == []
    assert interaction.followup.messages == [("This message has already been analyzed.", True)]


async def test_success_adds_winner_reaction_and_sends_only_three_lines() -> None:
    emojis = make_emojis()
    guild = SimpleNamespace(id=1, emojis=list(emojis.values()))
    channel = FakeChannel([FakeHistoryMessage(id=1, content="Context", author=FakeAuthor(7))])
    message = FakeMessage(guild=guild, channel=channel)
    interaction = make_interaction(guild)
    rater = FakeRater()
    cog = AnalysisCog(SimpleNamespace(), make_settings(), rater)

    await cog._analyze_claimed(interaction, message)

    assert message.added_reactions == [emojis["great"]]
    assert interaction.followup.messages == [("<great> 51%\n<best> 29%\n<book move> 20%", True)]
    assert rater.calls == [
        {
            "conversation_history": [{"speaker": "Speaker 1", "text": "Context"}],
            "target_message": {"speaker": "Speaker 2", "text": "Target text"},
        }
    ]


async def test_empty_target_does_not_fetch_history_or_call_api() -> None:
    emojis = make_emojis()
    guild = SimpleNamespace(id=1, emojis=list(emojis.values()))
    channel = FakeChannel()
    message = FakeMessage(content="  ", guild=guild, channel=channel)
    interaction = make_interaction(guild)
    rater = FakeRater()
    cog = AnalysisCog(SimpleNamespace(), make_settings(), rater)

    await cog._analyze_claimed(interaction, message)

    assert channel.calls == 0
    assert rater.calls == []
    assert message.added_reactions == []

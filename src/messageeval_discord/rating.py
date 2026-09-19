"""TypeSafe request construction and rating result handling."""

from __future__ import annotations

import math
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from typesafe_sdk import Choice

RatingLabel = Literal[
    "brilliant",
    "great",
    "best",
    "excellent",
    "good",
    "book_move",
    "inaccuracy",
    "mistake",
    "blunder",
    "miss",
    "forced",
]

RATING_LABELS: tuple[RatingLabel, ...] = (
    "brilliant",
    "great",
    "best",
    "excellent",
    "good",
    "book_move",
    "inaccuracy",
    "mistake",
    "blunder",
    "miss",
    "forced",
)

RATING_INSTRUCTION = (
    "Rate this message using chess-analysis terminology. Choose exactly one criterion."
)

RATING_CRITERIA: dict[RatingLabel, str] = {
    "brilliant": (
        "An exceptionally insightful, creative, or counterintuitive message that produces a "
        "major positive outcome, especially when it involves a meaningful sacrifice, tradeoff, "
        "or risk."
    ),
    "great": (
        "A highly effective or insightful message that significantly improves the situation, "
        "resolves a difficult problem, or finds an uncommon strong response."
    ),
    "best": (
        "The strongest or most appropriate message available in the context, without necessarily "
        "being unusually creative or difficult to find."
    ),
    "excellent": (
        "A very strong message that is close to the best possible response and has no meaningful "
        "downside."
    ),
    "good": (
        "A solid and appropriate message that contributes positively but is not notably "
        "exceptional."
    ),
    "book_move": (
        "A standard, conventional, expected, or well-established response that closely follows "
        "common practice or an obvious known pattern."
    ),
    "inaccuracy": (
        "A slightly flawed or suboptimal message that misses a better option but does not cause a "
        "major problem."
    ),
    "mistake": (
        "A meaningfully flawed message that worsens the situation, introduces confusion, or "
        "misses an important opportunity."
    ),
    "blunder": (
        "A severe error that substantially damages the situation, causes an obvious failure, or "
        "creates a major avoidable problem."
    ),
    "miss": (
        "A message that overlooks a clear and significant opportunity, solution, correction, or "
        "advantage that was available in context."
    ),
    "forced": (
        "A message that is effectively the only reasonable or viable response given the context."
    ),
}

DISPLAY_LABELS: dict[RatingLabel, str] = {
    **{label: label for label in RATING_LABELS},
    "book_move": "book move",
}


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    author_id: Hashable
    content: str


@dataclass(frozen=True, slots=True)
class RatingChoice:
    label: RatingLabel
    probability: float

    @property
    def percentage(self) -> int:
        """Return conventional half-up rounding for a non-negative probability."""

        return int(self.probability * 100 + 0.5)


@dataclass(frozen=True, slots=True)
class RatingResult:
    winner: RatingLabel
    choices: tuple[RatingChoice, ...]
    model: str
    request_id: str | None

    @property
    def top_three(self) -> tuple[RatingChoice, ...]:
        return self.choices[:3]

    def format_top_three(self) -> str:
        return "\n".join(
            f"<{DISPLAY_LABELS[item.label]}> {item.percentage}%" for item in self.top_three
        )


class InvalidRatingResponse(RuntimeError):
    """Raised when TypeSafe returns an unusable rating response."""


class ChoiceAnswerLike(Protocol):
    choice: str
    probabilities: Mapping[str, float]


class SystemOneResponseLike(Protocol):
    model: str
    request_id: str
    choices: Mapping[str, ChoiceAnswerLike]


class AsyncTypeSafeClientLike(Protocol):
    async def system_one(self, *, state: object, questions: Mapping[str, object]) -> object: ...


def build_state(
    history: Sequence[ConversationMessage], target: ConversationMessage
) -> dict[str, object]:
    """Build a pseudonymous, structured TypeSafe state."""

    speakers: dict[Hashable, str] = {}

    def speaker_for(author_id: Hashable) -> str:
        if author_id not in speakers:
            speakers[author_id] = f"Speaker {len(speakers) + 1}"
        return speakers[author_id]

    conversation_history = [
        {"speaker": speaker_for(message.author_id), "text": message.content} for message in history
    ]
    target_message = {
        "speaker": speaker_for(target.author_id),
        "text": target.content,
    }
    return {
        "conversation_history": conversation_history,
        "target_message": target_message,
    }


def parse_rating_response(response: SystemOneResponseLike) -> RatingResult:
    try:
        answer = response.choices["rate"]
    except (AttributeError, KeyError) as error:
        raise InvalidRatingResponse("TypeSafe response did not contain the rate choice") from error

    labels = set(RATING_LABELS)
    probability_keys = set(answer.probabilities)
    if probability_keys != labels:
        missing = sorted(labels - probability_keys)
        unexpected = sorted(probability_keys - labels)
        raise InvalidRatingResponse(
            f"Unexpected probability labels (missing={missing}, unexpected={unexpected})"
        )
    if answer.choice not in labels:
        raise InvalidRatingResponse(f"Unexpected winning label: {answer.choice}")

    probabilities: dict[RatingLabel, float] = {}
    for label in RATING_LABELS:
        value = float(answer.probabilities[label])
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise InvalidRatingResponse(f"Invalid probability for {label}: {value}")
        probabilities[label] = value

    maximum = max(probabilities.values())
    if not math.isclose(probabilities[cast(RatingLabel, answer.choice)], maximum, abs_tol=1e-9):
        raise InvalidRatingResponse("Winning label does not have the highest probability")

    winner = cast(RatingLabel, answer.choice)
    label_order = {label: index for index, label in enumerate(RATING_LABELS)}
    ordered_labels = sorted(
        RATING_LABELS,
        key=lambda label: (
            -probabilities[label],
            0 if label == winner else 1,
            label_order[label],
        ),
    )
    choices = tuple(RatingChoice(label, probabilities[label]) for label in ordered_labels)
    request_id = getattr(response, "request_id", None)
    return RatingResult(
        winner=winner,
        choices=choices,
        model=response.model,
        request_id=request_id or None,
    )


class MessageRater:
    def __init__(self, client: AsyncTypeSafeClientLike) -> None:
        self._client = client

    async def rate(self, state: object) -> RatingResult:
        response = await self._client.system_one(
            state=state,
            questions={
                "rate": Choice(
                    instructions=RATING_INSTRUCTION,
                    criteria=RATING_CRITERIA,
                )
            },
        )
        return parse_rating_response(cast(SystemOneResponseLike, response))

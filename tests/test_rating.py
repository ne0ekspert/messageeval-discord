from __future__ import annotations

from types import SimpleNamespace

import pytest

from messageeval_discord.rating import (
    RATING_CRITERIA,
    RATING_INSTRUCTION,
    RATING_LABELS,
    ConversationMessage,
    InvalidRatingResponse,
    MessageRater,
    build_state,
    parse_rating_response,
)


def response_with(
    *, winner: str = "great", probabilities: dict[str, float] | None = None
) -> SimpleNamespace:
    if probabilities is None:
        probabilities = {label: 0.01 for label in RATING_LABELS}
        probabilities.update({"great": 0.405, "best": 0.304, "book_move": 0.201})
    return SimpleNamespace(
        choices={"rate": SimpleNamespace(choice=winner, probabilities=probabilities)},
        model="jev-1.13.0",
        request_id="request-123",
    )


def test_build_state_preserves_order_and_pseudonymizes_authors() -> None:
    state = build_state(
        [
            ConversationMessage(author_id=20, content="First"),
            ConversationMessage(author_id=10, content="Second"),
            ConversationMessage(author_id=20, content="Third"),
        ],
        ConversationMessage(author_id=10, content="Target"),
    )

    assert state == {
        "conversation_history": [
            {"speaker": "Speaker 1", "text": "First"},
            {"speaker": "Speaker 2", "text": "Second"},
            {"speaker": "Speaker 1", "text": "Third"},
        ],
        "target_message": {"speaker": "Speaker 2", "text": "Target"},
    }


def test_parse_rating_sorts_formats_and_humanizes_book_move() -> None:
    result = parse_rating_response(response_with())

    assert result.winner == "great"
    assert [choice.label for choice in result.top_three] == ["great", "best", "book_move"]
    assert result.format_top_three() == "<great> 41%\n<best> 30%\n<book move> 20%"
    assert result.model == "jev-1.13.0"
    assert result.request_id == "request-123"


def test_parse_rating_puts_declared_winner_first_when_probabilities_tie() -> None:
    probabilities = {label: 0.01 for label in RATING_LABELS}
    probabilities["great"] = 0.45
    probabilities["best"] = 0.45

    result = parse_rating_response(response_with(winner="best", probabilities=probabilities))

    assert [choice.label for choice in result.top_three[:2]] == ["best", "great"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda values: values.pop("forced"), "Unexpected probability labels"),
        (lambda values: values.__setitem__("great", float("nan")), "Invalid probability"),
        (lambda values: values.__setitem__("great", 1.1), "Invalid probability"),
    ],
)
def test_parse_rating_rejects_malformed_probabilities(mutation, message: str) -> None:
    probabilities = {label: 1 / len(RATING_LABELS) for label in RATING_LABELS}
    mutation(probabilities)

    with pytest.raises(InvalidRatingResponse, match=message):
        parse_rating_response(response_with(winner="brilliant", probabilities=probabilities))


def test_parse_rating_rejects_winner_that_is_not_highest_probability() -> None:
    probabilities = {label: 0.01 for label in RATING_LABELS}
    probabilities["great"] = 0.8
    probabilities["best"] = 0.1

    with pytest.raises(InvalidRatingResponse, match="highest probability"):
        parse_rating_response(response_with(winner="best", probabilities=probabilities))


class FakeTypeSafeClient:
    def __init__(self) -> None:
        self.state = None
        self.questions = None

    async def system_one(self, *, state, questions):
        self.state = state
        self.questions = questions
        return response_with()


async def test_message_rater_sends_exact_instruction_and_criteria() -> None:
    client = FakeTypeSafeClient()
    rater = MessageRater(client)
    state = {"target_message": {"speaker": "Speaker 1", "text": "Hello"}}

    await rater.rate(state)

    assert client.state == state
    question = client.questions["rate"]
    assert question.instructions == RATING_INSTRUCTION
    assert question.criteria == RATING_CRITERIA

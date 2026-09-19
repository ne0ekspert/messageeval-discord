# MessageEval Discord

MessageEval Discord rates a message as if it were a move in a chess analysis. From a message's
context menu, the bot reads the recent conversation, asks [TypeSafe AI](https://typesafe.ai/) to
choose a classification, reacts with the matching server emoji, and privately shows the requester
the three most likely choices.

```text
<great> 51%
<best> 29%
<book move> 20%
```

## How it works

1. A member right-clicks a message and selects **Apps → Analyze message**.
2. The bot collects up to 20 preceding human-authored text messages from the same channel or
   thread. It scans at most 100 messages to find them.
3. Authors are replaced with stable labels such as `Speaker 1`; Discord names and IDs are not sent
   to TypeSafe.
4. TypeSafe assigns probabilities to eleven chess-style ratings.
5. The bot adds the winning custom emoji to the original message and returns the top three choices
   in an ephemeral response visible only to the requester.

The available ratings are `brilliant`, `great`, `best`, `excellent`, `good`, `book move`,
`inaccuracy`, `mistake`, `blunder`, `miss`, and `forced`.

## Requirements

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- A Discord application with a bot token
- A [TypeSafe AI API key](https://console.typesafe.ai/settings/keys)
- Eleven custom emojis in every Discord server where the bot will operate

## Quick start

Clone the project, install its locked dependencies, and create the local configuration file:

```bash
uv sync
cp .env.example .env
```

Fill in `.env`, complete the Discord setup below, and start the bot:

```bash
uv run messageeval-discord
```

The bot registers **Analyze message** globally. Discord may take time to propagate a newly created
or changed global command.

## Discord application setup

1. Create an application and bot in the
   [Discord Developer Portal](https://discord.com/developers/applications).
2. On the bot page, enable the privileged **Message Content Intent**. The command's selected
   message is available through the interaction, but this intent is also needed to read preceding
   conversation history.
3. Use the OAuth2 URL Generator to install the application with both the `bot` and
   `applications.commands` scopes.
4. Grant the bot these permissions in every channel where analysis should work:
   - **View Channel**
   - **Read Message History**
   - **Add Reactions**
5. Create all eleven configured custom emojis in every server that uses the bot.

The command is guild-only and does not appear in direct messages.

## Configuration

The application reads `.env` from the working directory. `.env` is ignored by Git; never commit
Discord tokens or TypeSafe keys.

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | Yes | — | Discord bot token. |
| `TYPESAFE_API_KEY` | Yes | — | TypeSafe API key. |
| `TYPESAFE_MODEL` | No | `jev-latest` | TypeSafe model or alias used for classification. |
| `LOG_LEVEL` | No | `INFO` | `CRITICAL`, `ERROR`, `WARNING`, `INFO`, or `DEBUG`. |
| `EMOJI_BRILLIANT` | Yes | — | Emoji name for `brilliant`. |
| `EMOJI_GREAT` | Yes | — | Emoji name for `great`. |
| `EMOJI_BEST` | Yes | — | Emoji name for `best`. |
| `EMOJI_EXCELLENT` | Yes | — | Emoji name for `excellent`. |
| `EMOJI_GOOD` | Yes | — | Emoji name for `good`. |
| `EMOJI_BOOK_MOVE` | Yes | — | Emoji name for `book move`. |
| `EMOJI_INACCURACY` | Yes | — | Emoji name for `inaccuracy`. |
| `EMOJI_MISTAKE` | Yes | — | Emoji name for `mistake`. |
| `EMOJI_BLUNDER` | Yes | — | Emoji name for `blunder`. |
| `EMOJI_MISS` | Yes | — | Emoji name for `miss`. |
| `EMOJI_FORCED` | Yes | — | Emoji name for `forced`. |

Emoji values are bare names, such as `brilliant`, not `:brilliant:`. Each name must resolve to
exactly one custom emoji usable by the bot, and the eleven ratings must map to different emojis.
Because the command is global, every participating server must provide emojis with the same names.
The bot validates the complete mapping before sending text to TypeSafe.

## Analysis and duplicate handling

- Attachments and embeds are ignored. A selected message without text is rejected.
- Bot-authored, system, attachment-only, and embed-only history entries are excluded.
- The TypeSafe request uses one structured `Choice` question and the eleven rating definitions in
  [`rating.py`](src/messageeval_discord/rating.py).
- A message is considered analyzed only when the bot itself owns one of the configured reactions.
  A member manually adding the same emoji does not suppress analysis.
- Repeating the command on an analyzed message does not make another TypeSafe request.
- Simultaneous attempts in one bot process are collapsed so only one analysis runs. The public
  reaction preserves duplicate detection across restarts.
- API, history, configuration, and reaction errors are reported privately without marking the
  message as successfully analyzed.

## Privacy

TypeSafe receives the selected message text and qualifying conversation history. It does not
receive Discord usernames, display names, user IDs, timestamps, attachments, or embeds from this
bot. Authors are represented only by per-request pseudonyms such as `Speaker 1`.

Message content is still sent to a third-party service. Server operators should disclose this to
members and avoid enabling the command in channels whose contents should not leave Discord.

## Project layout

```text
src/messageeval_discord/
├── __main__.py       # executable entry point
├── config.py         # environment loading and validation
├── discord_bot.py    # Discord command, history, reactions, and idempotency
└── rating.py         # TypeSafe request, rubric, validation, and formatting
tests/                # unit and workflow tests with mocked external services
```

## Development

Install the development dependency group with `uv sync`, then run the checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The automated tests do not require Discord or TypeSafe credentials. A live smoke test requires a
configured Discord guild, all eleven custom emojis, and valid service tokens.

## Troubleshooting

### The command does not appear

Confirm that the application was installed with the `applications.commands` scope. The command is
global, so initial registration and updates may take time to propagate. It is intentionally hidden
in direct messages.

### The bot cannot read history

Enable **Message Content Intent** in the Developer Portal and grant **View Channel** plus
**Read Message History** in the affected channel. Channel overrides can deny permissions that were
granted at the server role level.

### Emoji configuration is rejected

Check that every `EMOJI_*` value is a bare name, each named emoji exists exactly once in that
server, all eleven emojis are distinct, and role restrictions allow the bot to use them.

### Analysis succeeds but no reaction appears

Grant **Add Reactions** in the channel. The bot intentionally withholds the successful top-three
result when it cannot add the public classification reaction, allowing the message to be retried.

## License

MessageEval Discord is available under the [MIT License](LICENSE).

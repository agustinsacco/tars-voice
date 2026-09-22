# Contributing

## Development setup

```bash
git clone https://github.com/agustinsacco/tars-voice.git
cd tars-voice
make setup
make validate
```

Python 3.12+, Node.js 20+, and Bash are required. Tests do not download speech models.

## Pull requests

- Keep browser dependencies at zero unless there is a demonstrated need.
- Preserve loopback-only backend assumptions.
- Never commit credentials, Access assertions, cookies, transcripts, recordings, model files, or runtime logs.
- Add or update tests for protocol, authentication, VAD, queueing, and failure behavior.
- Run `make validate` before submitting.
- Document protocol or operational changes under `docs/`.

## Design constraints

- Tars remains authoritative for reasoning, memory, tools, and confirmations.
- `done` from the relay is the authoritative end of a turn.
- Browser interruption may stop playback but must not claim remote operation cancellation.
- User-facing status messages must not expose raw tool or provider details.

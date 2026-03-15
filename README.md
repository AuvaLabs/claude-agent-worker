# claude-agent-worker

A local API bridge that lets you use your **Claude Max subscription** programmatically — no API keys, no per-token billing.

The `claude` CLI authenticates via **OAuth** (same login as claude.ai in your browser). This worker wraps it in an OpenAI-compatible HTTP interface so any app using the OpenAI SDK can point here instead.

## How it works

```
Your app
    │
    ▼
POST /v1/chat/completions
    │
    ▼
claude-agent-worker  (this server)
    │
    ▼
claude -p ...  (Claude CLI subprocess)
    │
    ▼
Claude Max via OAuth  ← your subscription, $0 per call
```

## Requirements

- [Claude Code CLI](https://claude.ai/claude-code) installed
- Python 3.11+

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Authenticate Claude CLI (one-time, opens browser)
claude login

# 3. Start the worker
python server.py
# → listening on http://localhost:8400
```

## Usage

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8400/v1",
    api_key="unused",
)

response = client.chat.completions.create(
    model="sonnet",   # sonnet | opus | haiku
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"},
    ]
)
print(response.choices[0].message.content)
```

### Streaming

```python
stream = client.chat.completions.create(
    model="sonnet",
    messages=[{"role": "user", "content": "Write me a poem."}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

### curl

```bash
curl http://localhost:8400/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "sonnet", "messages": [{"role": "user", "content": "Hello"}]}'
```

## Models

| Alias | Model |
|---|---|
| `sonnet` *(default)* | `claude-sonnet-4-6` |
| `opus` | `claude-opus-4-6` |
| `haiku` | `claude-haiku-4-5-20251001` |

Full model IDs are also accepted directly.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_BIN` | `claude` | Path to the claude CLI binary |
| `MAX_CONCURRENT` | `2` | Max parallel claude subprocesses |
| `REQUEST_TIMEOUT` | `300` | Seconds before a request times out |

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Status, version, config |
| `POST` | `/v1/chat/completions` | Chat completions |

## Notes

- Designed for local use only — do not expose port 8400 to the internet.
- Token counts are word-split approximations, not exact.
- `--max-turns 1` — single-turn only, no agentic tool use.

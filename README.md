# claude-agent-worker

An OpenAI-compatible API bridge that routes requests through the **Claude CLI** — so you can use your **Claude Max subscription** (OAuth, flat-rate) instead of paying per-token via the Anthropic or OpenAI APIs.

## How it works

```
Your app (OpenAI SDK)
        │
        ▼
 POST /v1/chat/completions        ← standard OpenAI format
        │
        ▼
  claude-agent-worker             ← this server (FastAPI)
        │
        ▼
   claude -p ...                  ← Claude CLI subprocess
        │
        ▼
  Claude Max (OAuth)              ← your subscription, $0 per call
```

The `claude` CLI authenticates via **OAuth** — the same login as claude.ai in your browser. No Anthropic API key. No OpenAI key. Just your subscription.

## Why

| | API Key (pay-per-token) | Claude Max + this worker |
|---|---|---|
| Cost | ~$3–15 / 1M tokens | Flat monthly subscription |
| Setup | Instant | One `claude login` |
| Rate limits | Per-org token budget | Subscription limits |
| Best for | Production / high-volume | Development / personal use |

## Requirements

- [Claude Code CLI](https://claude.ai/claude-code) installed and authenticated
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

Point any OpenAI-compatible client at `http://localhost:8400/v1`:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8400/v1",
    api_key="not-needed",          # required by SDK, value ignored
)

response = client.chat.completions.create(
    model="sonnet",                # sonnet | opus | haiku | full model ID
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
  -d '{
    "model": "sonnet",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

## Models

| Alias | Full model ID |
|---|---|
| `sonnet` *(default)* | `claude-sonnet-4-6` |
| `opus` | `claude-opus-4-6` |
| `haiku` | `claude-haiku-4-5-20251001` |

Full model IDs are also accepted directly.

## Configuration

All settings via environment variables:

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_BIN` | `claude` | Path to the claude CLI binary |
| `MAX_CONCURRENT` | `2` | Max parallel claude subprocesses |
| `REQUEST_TIMEOUT` | `300` | Seconds before a request times out |

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Returns version and config info |
| `POST` | `/v1/chat/completions` | OpenAI-compatible completions |

## Running as a service

```bash
# systemd example
[Unit]
Description=Claude Agent Worker
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/deploy/claude-worker/server.py
Restart=on-failure
Environment=MAX_CONCURRENT=2

[Install]
WantedBy=multi-user.target
```

## Notes

- The worker is intentionally **open** (no API key) — it is designed for local/trusted network use only. Do not expose port 8400 to the internet.
- Token counts in responses are word-split approximations, not exact BPE counts.
- `--max-turns 1` is set by default — agentic multi-step tool use is not supported in this bridge.

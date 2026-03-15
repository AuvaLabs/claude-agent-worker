# claude-agent-worker

> Use your **Claude Max subscription** as a local API — zero per-token cost, no API keys required.

`claude-agent-worker` is a lightweight FastAPI server that wraps the [Claude Code CLI](https://claude.ai/claude-code) and exposes it as an **OpenAI-compatible HTTP API**. Any app, script, or library that speaks the OpenAI chat completions format can point here and run on your flat-rate Claude Max subscription instead.

---

## Why this exists

Anthropic offers two ways to use Claude:

| | Claude API | Claude Max + this worker |
|---|---|---|
| Billing | Pay per token (~$3–15 / 1M tokens) | Flat monthly subscription |
| Auth | API key | OAuth (browser login) |
| Use case | Production, high-volume | Development, personal, local tools |
| Setup | Add credit card, manage keys | `claude login` once |

If you already pay for **Claude Max** ($100/mo) and are building personal tools, scripts, or local AI features — this worker means **you pay nothing extra per call**. The CLI authenticates via OAuth using the same session as claude.ai in your browser.

---

## How it works

```
Your app  ──►  POST /v1/chat/completions  ──►  claude-agent-worker
                                                       │
                                               claude -p <prompt>
                                               --model <model>
                                               --output-format json|stream-json
                                                       │
                                               Claude Max (OAuth session)
                                                       │
                                               Claude API  ◄── subscription covers this
```

The worker:
1. Receives an OpenAI-format request
2. Converts messages to `Human:` / `Assistant:` format
3. Forwards system prompts via `--system-prompt`
4. Spawns `claude -p` as an async subprocess
5. Returns an OpenAI-shaped JSON response (or SSE stream)

---

## Use cases

- **Local AI scripts** — automate writing, summarization, code review, data extraction without API costs
- **Personal tools** — build CLI tools, browser extensions, or desktop apps backed by Claude
- **LangChain / LlamaIndex apps** — point the OpenAI client at localhost, no code changes needed
- **Rapid prototyping** — iterate on prompts and apps without watching token costs
- **Home automation** — run Claude-powered automations on a home server
- **Development & testing** — test AI features locally without burning API budget
- **Drop-in replacement** — swap `api.openai.com` for `localhost:8400` in any existing project

---

## Requirements

- [Claude Code CLI](https://claude.ai/claude-code) installed
- A **Claude Max subscription** (claude.ai)
- Python 3.11+

---

## Setup

```bash
# 1. Clone
git clone https://github.com/AuvaLabs/claude-agent-worker.git
cd claude-agent-worker

# 2. Install dependencies
pip install -r requirements.txt

# 3. Authenticate the Claude CLI (one-time — opens browser for OAuth)
claude login

# 4. Start the worker
python server.py
# Listening on http://localhost:8400
```

---

## Usage

### Python — openai SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8400/v1",
    api_key="unused",   # required by SDK, ignored by worker
)

response = client.chat.completions.create(
    model="sonnet",     # sonnet | opus | haiku (default: sonnet)
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user",   "content": "Summarize the benefits of TDD."},
    ]
)
print(response.choices[0].message.content)
```

### Streaming

```python
stream = client.chat.completions.create(
    model="sonnet",
    messages=[{"role": "user", "content": "Write a short story about a robot."}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

### Multi-turn conversation

```python
response = client.chat.completions.create(
    model="sonnet",
    messages=[
        {"role": "user",      "content": "My name is Alex."},
        {"role": "assistant", "content": "Nice to meet you, Alex!"},
        {"role": "user",      "content": "What is my name?"},
    ]
)
print(response.choices[0].message.content)
# → Your name is Alex.
```

### curl

```bash
curl http://localhost:8400/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "sonnet",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Health check

```bash
curl http://localhost:8400/health
# {
#   "status": "healthy",
#   "claude_version": "1.x.x",
#   "default_model": "claude-sonnet-4-6",
#   "max_concurrent": 2
# }
```

### Environment variable override (zero code changes)

If you have an existing app already using the OpenAI SDK:

```bash
export OPENAI_BASE_URL=http://localhost:8400/v1
export OPENAI_API_KEY=unused
python your_existing_app.py   # now runs on Claude Max
```

---

## Models

| Alias | Full model ID | Best for |
|---|---|---|
| `sonnet` *(default)* | `claude-sonnet-4-6` | General use, fast, balanced |
| `opus` | `claude-opus-4-6` | Complex reasoning, deep analysis |
| `haiku` | `claude-haiku-4-5-20251001` | Simple tasks, fastest responses |

Full model IDs are also accepted directly in the `model` field.

---

## Configuration

All settings via environment variables — no config file needed.

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_BIN` | `claude` | Path to the Claude CLI binary |
| `MAX_CONCURRENT` | `2` | Max parallel `claude` subprocesses |
| `REQUEST_TIMEOUT` | `300` | Seconds before a request times out |

```bash
MAX_CONCURRENT=4 REQUEST_TIMEOUT=120 python server.py
```

---

## API reference

### `GET /health`

Returns worker status and configuration.

```json
{
  "status": "healthy",
  "claude_version": "1.x.x",
  "default_model": "claude-sonnet-4-6",
  "max_concurrent": 2
}
```

### `POST /v1/chat/completions`

OpenAI-compatible chat completions endpoint.

**Request body:**

| Field | Type | Default | Description |
|---|---|---|---|
| `messages` | array | required | Array of `{role, content}` objects |
| `model` | string | `sonnet` | Model alias or full model ID |
| `stream` | boolean | `false` | Enable SSE streaming |

**Roles supported:** `system`, `user`, `assistant`

**Response:** Standard OpenAI `chat.completion` or `chat.completion.chunk` (streaming) object.

---

## Running as a background service

### systemd

```ini
[Unit]
Description=Claude Agent Worker
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/deploy/claude-agent-worker/server.py
Restart=on-failure
Environment=MAX_CONCURRENT=2
Environment=REQUEST_TIMEOUT=300

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable claude-agent-worker
sudo systemctl start claude-agent-worker
```

### Background process

```bash
nohup python server.py > worker.log 2>&1 &
```

---

## Limitations

- **Local use only** — no authentication on the HTTP layer; do not expose port 8400 to the internet
- **Single-turn per request** — `--max-turns 1` is enforced; agentic/tool-use loops are not supported
- **Token counts are approximate** — word-split estimates, not exact BPE tokenization
- **One session per request** — no persistent conversation sessions across requests; pass full history in `messages`

---

## License

MIT

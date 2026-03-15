# claude-agent-worker

Use your **Claude Max subscription** as a local API. No API keys, no per-token billing, no surprises at the end of the month.

`claude-agent-worker` is a small FastAPI server that wraps the [Claude Code CLI](https://claude.ai/claude-code) and exposes it over HTTP in the OpenAI chat completions format. Any app or script that already talks to OpenAI can point here instead and run on your Claude Max subscription for free.

**Starting the server**

![Server startup](docs/demo_startup.gif)

**Making a request**

![Live request](docs/demo_request.gif)

## Why bother

Anthropic gives you two ways to use Claude programmatically:

| | Claude API | Claude Max + this worker |
|---|---|---|
| Billing | Pay per token | Flat monthly subscription |
| Auth | API key | OAuth (browser login) |
| Good for | Production, high volume | Personal tools, local dev |
| Setup | Add card, manage keys | Run `claude login` once |

If you already pay for Claude Max and you are building personal tools, scripts, or local AI features, every call through this worker costs you nothing extra. The CLI uses the same OAuth session as claude.ai in your browser, so there are no separate credentials to manage.


## How it works

```
Your app
    |
    v
POST /v1/chat/completions
    |
    v
claude-agent-worker  (this server)
    |
    v
claude -p ...  (CLI subprocess)
    |
    v
Claude Max via OAuth  (your subscription)
```

The worker takes in an OpenAI-format request, converts the messages into the `Human:` / `Assistant:` format Claude expects, runs the CLI, and hands back a response shaped exactly like what the OpenAI SDK expects.


## What you can build with it

- **Personal scripts** that summarize, rewrite, classify, or extract data without paying per call
- **Local CLI tools** backed by Claude, running entirely on your machine
- **LangChain or LlamaIndex apps** where you just swap the base URL and nothing else changes
- **Rapid prototypes** where you want to iterate fast without watching token costs
- **Home server automations** that run Claude-powered tasks on a schedule
- **Dev and test environments** so you stop burning API budget on non-production work
- **Drop-in replacement** for any existing project already using the OpenAI SDK


## Requirements

- [Claude Code CLI](https://claude.ai/claude-code) installed
- A Claude Max subscription
- Python 3.11+


## Setup

```bash
# 1. Clone the repo
git clone https://github.com/AuvaLabs/claude-agent-worker.git
cd claude-agent-worker

# 2. Install dependencies
pip install -r requirements.txt

# 3. Log in to Claude (one-time, opens browser)
claude login

# 4. Start the server
python server.py
# Listening on http://localhost:8400
```


## Usage

### Basic request

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8400/v1",
    api_key="unused",   # the SDK requires this field but the worker ignores it
)

response = client.chat.completions.create(
    model="sonnet",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user",   "content": "Summarize the benefits of TDD in three bullet points."},
    ]
)
print(response.choices[0].message.content)
```

### Streaming

```python
stream = client.chat.completions.create(
    model="sonnet",
    messages=[{"role": "user", "content": "Write a short story about a robot learning to cook."}],
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
# Your name is Alex.
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

### Zero code changes for existing projects

If you already have an app using the OpenAI SDK, just set two environment variables and it will route through Claude instead:

```bash
export OPENAI_BASE_URL=http://localhost:8400/v1
export OPENAI_API_KEY=unused
python your_existing_app.py
```

### Health check

```bash
curl http://localhost:8400/health
```

```json
{
  "status": "healthy",
  "claude_version": "1.x.x",
  "default_model": "claude-sonnet-4-6",
  "max_concurrent": 2
}
```


## Models

| Alias | Full model ID | Good for |
|---|---|---|
| `sonnet` (default) | `claude-sonnet-4-6` | General use, fast, well-balanced |
| `opus` | `claude-opus-4-6` | Complex reasoning, deep analysis |
| `haiku` | `claude-haiku-4-5-20251001` | Simple tasks, fastest responses |

You can pass either the short alias or the full model ID in the `model` field.


## Configuration

Set these as environment variables before starting the server.

| Variable | Default | What it does |
|---|---|---|
| `CLAUDE_BIN` | `claude` | Path to the Claude CLI binary |
| `MAX_CONCURRENT` | `2` | How many Claude processes can run at once |
| `REQUEST_TIMEOUT` | `300` | Seconds to wait before giving up on a request |

```bash
MAX_CONCURRENT=4 REQUEST_TIMEOUT=120 python server.py
```


## API reference

### GET /health

Returns the current status of the worker.

```json
{
  "status": "healthy",
  "claude_version": "1.x.x",
  "default_model": "claude-sonnet-4-6",
  "max_concurrent": 2
}
```

### POST /v1/chat/completions

Standard OpenAI chat completions format.

| Field | Type | Default | Notes |
|---|---|---|---|
| `messages` | array | required | Array of `{role, content}` objects |
| `model` | string | `sonnet` | Short alias or full model ID |
| `stream` | boolean | `false` | Set to `true` for SSE streaming |

Supported roles: `system`, `user`, `assistant`


## Running as a service

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


## Limitations

- **Local use only.** There is no authentication on the HTTP layer. Do not expose port 8400 to the public internet.
- **Single-turn per request.** The worker runs with `--max-turns 1`. Agentic loops and tool use are not supported.
- **Token counts are approximate.** The usage fields in responses are word-split estimates, not exact tokenization.
- **No persistent sessions.** Each request is stateless. Pass the full conversation history in `messages` if you need context across turns.


## License

MIT

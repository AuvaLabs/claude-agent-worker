# Changelog

## v0.4.0

**Streaming fix**

- Fixed streaming completely broken due to missing `--verbose` flag on `stream-json` output format
- Updated event parsing to correctly read `stream_event.content_block_delta` text deltas instead of the wrong `assistant` event type
- Streaming now delivers tokens incrementally as expected

**Concurrency increase**

- Raised default `MAX_CONCURRENT` from 2 to 4 per account for better throughput when running alongside other tools

## v0.3.0

**Multi-account pool with load balancing**

- Added `CLAUDE_ACCOUNTS` env var to configure a pool of Claude Max accounts
- Requests are routed using least-active-connections so no single account gets overloaded
- Each account runs its own semaphore and tracks active requests, total requests, and errors
- Single account mode works exactly as before with no config change needed

**Queue protection**

- Added `MAX_QUEUE_SIZE` env var (default 20)
- Requests beyond queue capacity now return 429 immediately instead of hanging the caller

**Rotating file logging**

- All requests now log to `logs/worker.log` with rotation (10 MB per file, 5 backups)
- Each log line includes: request ID, account ID, model, duration in ms, prompt/response size, status
- Errors log the return code and CLI stderr output
- Live monitoring: `tail -f logs/worker.log`

**Health endpoint improvements**

- `/health` now returns per-account stats: active requests, total handled, error count

## v0.2.0

**Core improvements over the initial version**

- Real streaming support via SSE using `--output-format stream-json`
- Model forwarding: the `model` field is now actually passed to the CLI via `--model`
- Model aliases: `sonnet`, `opus`, `haiku` map to full model IDs
- System prompt forwarding via `--system-prompt`
- Proper `Human:` / `Assistant:` conversation formatting for multi-turn history
- Structured request logging with request IDs
- Configurable timeout via `REQUEST_TIMEOUT` env var
- Content array support for OpenAI vision-style messages

## v0.1.0

**Initial release**

- FastAPI server wrapping `claude -p` as a subprocess
- OpenAI-compatible `POST /v1/chat/completions` endpoint
- `GET /health` endpoint
- Concurrency control via asyncio semaphore
- 300 second timeout with 504 response on breach

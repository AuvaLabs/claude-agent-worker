"""Claude Code Worker — OpenAI-compatible API wrapping `claude -p`.

Provides /v1/chat/completions and /health endpoints.
Uses Claude Max subscription ($0 cost) via the claude CLI.
No API key required — intended as a local drop-in for direct API calls.

Features:
- Default model: claude-sonnet-4-6 (switchable per request)
- Streaming support (stream: true → SSE)
- System prompt forwarding (--system-prompt)
- Proper Human:/Assistant: conversation formatting
- Structured request logging with request IDs
- Configurable concurrency and timeout via env vars
"""

import asyncio
import json
import logging
import os
import time
import uuid
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("claude-worker")

app = FastAPI(title="Claude Code Worker")

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "2"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "300"))

semaphore = asyncio.Semaphore(MAX_CONCURRENT)

MODEL_ALIASES: dict[str, str] = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-opus-4-6": "claude-opus-4-6",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5-20251001",
}
DEFAULT_MODEL = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_model(model: str) -> str:
    return MODEL_ALIASES.get(model, DEFAULT_MODEL)


def extract_text(content: str | list) -> str:
    if isinstance(content, str):
        return content
    return " ".join(
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


def build_prompt_and_system(messages: list[dict]) -> tuple[str, str]:
    system_parts: list[str] = []
    prompt_parts: list[str] = []

    for msg in messages:
        role = msg.get("role", "user")
        text = extract_text(msg.get("content", ""))
        if role == "system":
            system_parts.append(text)
        elif role == "user":
            prompt_parts.append(f"Human: {text}")
        elif role == "assistant":
            prompt_parts.append(f"Assistant: {text}")

    return "\n\n".join(prompt_parts), "\n\n".join(system_parts)


def build_cmd(prompt: str, system: str, model: str, streaming: bool) -> list[str]:
    cmd = [
        CLAUDE_BIN, "-p", prompt,
        "--output-format", "stream-json" if streaming else "json",
        "--max-turns", "1",
        "--model", model,
    ]
    if streaming:
        cmd.append("--include-partial-messages")
    if system:
        cmd += ["--system-prompt", system]
    return cmd


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

async def _stream_sse(prompt: str, system: str, model: str) -> AsyncIterator[str]:
    cmd = build_cmd(prompt, system, model, streaming=True)
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    async with semaphore:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async for raw_line in proc.stdout:
                line = raw_line.decode().strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                if event_type == "assistant":
                    for block in event.get("message", {}).get("content", []):
                        if block.get("type") == "text":
                            yield f"data: {json.dumps(_sse_chunk(completion_id, created, model, block['text']))}\n\n"

                elif event_type == "result":
                    yield f"data: {json.dumps(_sse_stop_chunk(completion_id, created, model))}\n\n"
                    yield "data: [DONE]\n\n"
                    return

        except Exception as exc:
            log.exception("streaming error: %s", exc)
            yield f"data: {json.dumps({'error': {'message': str(exc), 'type': 'server_error'}})}\n\n"
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()


def _sse_chunk(cid: str, created: int, model: str, text: str) -> dict:
    return {
        "id": cid, "object": "chat.completion.chunk", "created": created,
        "model": f"claude-worker-{model}",
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}],
    }


def _sse_stop_chunk(cid: str, created: int, model: str) -> dict:
    return {
        "id": cid, "object": "chat.completion.chunk", "created": created,
        "model": f"claude-worker-{model}",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    proc = await asyncio.create_subprocess_exec(
        CLAUDE_BIN, "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return {
        "status": "healthy",
        "claude_version": stdout.decode().strip(),
        "default_model": DEFAULT_MODEL,
        "max_concurrent": MAX_CONCURRENT,
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages: list[dict] = body.get("messages", [])
    model = resolve_model(body.get("model", "sonnet"))
    stream: bool = body.get("stream", False)
    request_id = uuid.uuid4().hex[:8]

    prompt, system = build_prompt_and_system(messages)
    log.info("req=%s model=%s stream=%s chars=%d", request_id, model, stream, len(prompt))

    if stream:
        return StreamingResponse(
            _stream_sse(prompt, system, model),
            media_type="text/event-stream",
            headers={"X-Request-Id": request_id, "Cache-Control": "no-cache"},
        )

    cmd = build_cmd(prompt, system, model, streaming=False)
    async with semaphore:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=REQUEST_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            log.error("req=%s timed out after %ds", request_id, REQUEST_TIMEOUT)
            return JSONResponse(
                status_code=504,
                content={"error": {"message": "Claude CLI timed out", "type": "timeout"}},
            )

    if proc.returncode != 0:
        err = stderr.decode()[:500]
        log.error("req=%s cli error rc=%d: %s", request_id, proc.returncode, err)
        return JSONResponse(
            status_code=502,
            content={"error": {"message": f"Claude CLI error: {err}", "type": "cli_error"}},
        )

    try:
        result = json.loads(stdout.decode())
        content: str = result.get("result", stdout.decode())
    except json.JSONDecodeError:
        content = stdout.decode().strip()

    log.info("req=%s done chars=%d", request_id, len(content) if isinstance(content, str) else 0)

    prompt_tokens = len(prompt.split())
    completion_tokens = len(content.split()) if isinstance(content, str) else 0
    return JSONResponse({
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": f"claude-worker-{model}",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8400)

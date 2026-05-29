"""Claude Code Worker — OpenAI-compatible API wrapping `claude -p`.

Provides /v1/chat/completions and /health endpoints.
Uses Claude Max subscription ($0 cost) via the claude CLI.
No API key required — intended as a local drop-in for direct API calls.

Single account (default):
    python3 server.py

Multi-account pool (least-active-connections load balancing):
    CLAUDE_ACCOUNTS=/home/acct1,/home/acct2,/home/acct3 python3 server.py

Each account HOME must be pre-authenticated:
    HOME=/home/acct1 claude login
"""

import asyncio
import json
import logging
import logging.handlers
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

# ---------------------------------------------------------------------------
# Logging setup — stdout + rotating file
# ---------------------------------------------------------------------------

LOG_DIR = os.environ.get("LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

formatter = logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s %(message)s"
)

file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "worker.log"),
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
)
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)

logging.basicConfig(level=logging.INFO, handlers=[file_handler, stream_handler])
log = logging.getLogger("claude-bridge")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "4"))
MAX_QUEUE_SIZE = int(os.environ.get("MAX_QUEUE_SIZE", "20"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "300"))
CLAUDE_ACCOUNTS_RAW = os.environ.get("CLAUDE_ACCOUNTS", "")

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
# Account pool
# ---------------------------------------------------------------------------

@dataclass
class Account:
    id: str
    home_dir: str
    semaphore: asyncio.Semaphore = field(init=False)
    active: int = 0
    total_requests: int = 0
    errors: int = 0

    def __post_init__(self):
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT)


class AccountPool:
    def __init__(self, accounts: list[Account]):
        self.accounts = accounts
        self._total_queued = 0

    def pick(self) -> Account:
        """Pick the account with the fewest active requests."""
        return min(self.accounts, key=lambda a: a.active)

    @property
    def total_queued(self) -> int:
        return self._total_queued

    def status(self) -> list[dict]:
        return [
            {
                "id": a.id,
                "active": a.active,
                "total_requests": a.total_requests,
                "errors": a.errors,
            }
            for a in self.accounts
        ]


def _build_pool() -> AccountPool:
    if CLAUDE_ACCOUNTS_RAW:
        homes = [h.strip() for h in CLAUDE_ACCOUNTS_RAW.split(",") if h.strip()]
        accounts = [
            Account(id=f"acct{i+1}", home_dir=h)
            for i, h in enumerate(homes)
        ]
        log.info("multi-account pool: %d accounts loaded", len(accounts))
    else:
        accounts = [Account(id="default", home_dir=os.environ.get("HOME", ""))]
        log.info("single-account mode")
    return AccountPool(accounts)


pool: AccountPool = None  # initialised in startup


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Claude Bridge")


@app.on_event("startup")
async def startup():
    global pool
    pool = _build_pool()
    log.info("claude-bridge started — port 8400, timeout %ds, queue limit %d", REQUEST_TIMEOUT, MAX_QUEUE_SIZE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_model(model: str) -> str:
    return MODEL_ALIASES.get(model, DEFAULT_MODEL)


import base64 as _base64
import tempfile as _tempfile

# Temp dir for image attachments extracted from OpenAI-format requests
_BRIDGE_IMAGE_DIR = os.environ.get("BRIDGE_IMAGE_DIR", "/tmp/claude_bridge_images")
os.makedirs(_BRIDGE_IMAGE_DIR, exist_ok=True)


def _save_data_url_image(data_url: str, req_id: str, idx: int) -> str | None:
    """Save a base64 data URL image to disk; return absolute path."""
    if not data_url.startswith("data:"):
        return None
    try:
        meta, b64 = data_url.split(",", 1)
        ext = "png"
        if "image/jpeg" in meta or "image/jpg" in meta:
            ext = "jpg"
        elif "image/webp" in meta:
            ext = "webp"
        elif "image/gif" in meta:
            ext = "gif"
        path = os.path.join(_BRIDGE_IMAGE_DIR, f"req_{req_id}_{idx}.{ext}")
        with open(path, "wb") as f:
            f.write(_base64.b64decode(b64))
        return path
    except Exception as e:
        log.warning("failed to decode image: %s", e)
        return None


def extract_text_and_images(content: str | list, req_id: str = "0", start_idx: int = 0) -> tuple[str, list[str]]:
    """Extract text + image file paths from an OpenAI-format content array."""
    if isinstance(content, str):
        return content, []
    text_parts: list[str] = []
    image_paths: list[str] = []
    idx = start_idx
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            text_parts.append(part.get("text", ""))
        elif ptype == "image_url":
            url = part.get("image_url", {}).get("url", "") if isinstance(part.get("image_url"), dict) else part.get("image_url", "")
            saved = _save_data_url_image(url, req_id, idx)
            if saved:
                image_paths.append(saved)
                idx += 1
        elif ptype == "image":
            src = part.get("source", {})
            if src.get("type") == "base64":
                data_url = f"data:image/{src.get('media_type','png').replace('image/','')};base64,{src.get('data','')}"
                saved = _save_data_url_image(data_url, req_id, idx)
                if saved:
                    image_paths.append(saved)
                    idx += 1
    return " ".join(text_parts), image_paths


def extract_text(content: str | list) -> str:
    """Legacy text-only extractor (kept for compat)."""
    text, _ = extract_text_and_images(content)
    return text


def build_prompt_and_system(messages: list[dict], req_id: str = "0") -> tuple[str, str]:
    system_parts: list[str] = []
    prompt_parts: list[str] = []
    all_images: list[str] = []

    for msg in messages:
        role = msg.get("role", "user")
        text, images = extract_text_and_images(msg.get("content", ""), req_id, len(all_images))
        all_images.extend(images)
        if role == "system":
            system_parts.append(text)
        elif role == "user":
            prompt_parts.append(f"Human: {text}")
        elif role == "assistant":
            prompt_parts.append(f"Assistant: {text}")

    prompt = "\n\n".join(prompt_parts)
    if all_images:
        # Prepend an instruction so Claude reads the attached images using the Read tool.
        img_instructions = "\n".join(f"Use the Read tool to view this attached image: {p}" for p in all_images)
        prompt = img_instructions + "\n\n" + prompt

    return prompt, "\n\n".join(system_parts)


def build_cmd(prompt: str, system: str, model: str, streaming: bool) -> list[str]:
    # If the prompt contains an image-read instruction (vision request), allow
    # up to 5 turns + bypass permissions + add the image dir to Read scope so
    # Claude can Read the attached image(s) and respond.
    has_images = "Use the Read tool to view this attached image" in prompt
    max_turns = "5" if has_images else "1"
    cmd = [
        CLAUDE_BIN, "-p", prompt,
        "--output-format", "stream-json" if streaming else "json",
        "--max-turns", max_turns,
        "--model", model,
    ]
    if has_images:
        cmd += ["--add-dir", _BRIDGE_IMAGE_DIR, "--permission-mode", "bypassPermissions"]
    if streaming:
        cmd += ["--include-partial-messages", "--verbose"]
    if system:
        cmd += ["--system-prompt", system]
    return cmd


def make_env(account: Account) -> dict:
    env = os.environ.copy()
    if account.home_dir:
        env["HOME"] = account.home_dir
    return env


def _sse_chunk(cid: str, created: int, model: str, text: str) -> dict:
    return {
        "id": cid, "object": "chat.completion.chunk", "created": created,
        "model": f"claude-bridge-{model}",
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}],
    }


def _sse_stop_chunk(cid: str, created: int, model: str) -> dict:
    return {
        "id": cid, "object": "chat.completion.chunk", "created": created,
        "model": f"claude-bridge-{model}",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }


# ---------------------------------------------------------------------------
# Queue guard
# ---------------------------------------------------------------------------

def check_queue() -> JSONResponse | None:
    total_active = sum(a.active for a in pool.accounts)
    total_capacity = len(pool.accounts) * MAX_CONCURRENT
    if total_active >= total_capacity + MAX_QUEUE_SIZE:
        log.warning("queue full — active=%d capacity=%d queue_limit=%d", total_active, total_capacity, MAX_QUEUE_SIZE)
        return JSONResponse(
            status_code=429,
            content={"error": {"message": "Worker queue full, try again later", "type": "rate_limit"}},
        )
    return None


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

async def _stream_sse(
    prompt: str, system: str, model: str, account: Account, request_id: str
) -> AsyncIterator[str]:
    cmd = build_cmd(prompt, system, model, streaming=True)
    env = make_env(account)
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    started = time.monotonic()

    account.active += 1
    account.total_requests += 1
    async with account.semaphore:
        proc = await asyncio.create_subprocess_exec(
            *cmd, env=env,
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

                # token-level text deltas
                if event_type == "stream_event":
                    inner = event.get("event", {})
                    if inner.get("type") == "content_block_delta":
                        delta = inner.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield f"data: {json.dumps(_sse_chunk(cid, created, model, delta['text']))}\n\n"

                elif event_type == "result":
                    duration = int((time.monotonic() - started) * 1000)
                    log.info("req=%s acct=%s model=%s stream=True duration_ms=%d status=ok", request_id, account.id, model, duration)
                    yield f"data: {json.dumps(_sse_stop_chunk(cid, created, model))}\n\n"
                    yield "data: [DONE]\n\n"
                    return

        except Exception as exc:
            account.errors += 1
            log.exception("req=%s acct=%s streaming error: %s", request_id, account.id, exc)
            yield f"data: {json.dumps({'error': {'message': str(exc), 'type': 'server_error'}})}\n\n"
        finally:
            account.active -= 1
            if proc.returncode is None:
                proc.kill()
                await proc.wait()


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
        "max_concurrent_per_account": MAX_CONCURRENT,
        "max_queue_size": MAX_QUEUE_SIZE,
        "accounts": pool.status(),
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    rejected = check_queue()
    if rejected:
        return rejected

    body = await request.json()
    messages: list[dict] = body.get("messages", [])
    model = resolve_model(body.get("model", "sonnet"))
    stream: bool = body.get("stream", False)
    request_id = uuid.uuid4().hex[:8]

    prompt, system = build_prompt_and_system(messages, req_id=request_id)
    account = pool.pick()

    log.info("req=%s acct=%s model=%s stream=%s prompt_chars=%d", request_id, account.id, model, stream, len(prompt))

    if stream:
        return StreamingResponse(
            _stream_sse(prompt, system, model, account, request_id),
            media_type="text/event-stream",
            headers={"X-Request-Id": request_id, "Cache-Control": "no-cache"},
        )

    cmd = build_cmd(prompt, system, model, streaming=False)
    env = make_env(account)
    started = time.monotonic()
    account.active += 1
    account.total_requests += 1

    async with account.semaphore:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=REQUEST_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            account.active -= 1
            account.errors += 1
            duration = int((time.monotonic() - started) * 1000)
            log.error("req=%s acct=%s timeout after %dms", request_id, account.id, duration)
            return JSONResponse(
                status_code=504,
                content={"error": {"message": "Claude CLI timed out", "type": "timeout"}},
            )

    account.active -= 1
    duration = int((time.monotonic() - started) * 1000)

    if proc.returncode != 0:
        account.errors += 1
        err = stderr.decode()[:500]
        log.error("req=%s acct=%s rc=%d duration_ms=%d error=%s", request_id, account.id, proc.returncode, duration, err)
        return JSONResponse(
            status_code=502,
            content={"error": {"message": f"Claude CLI error: {err}", "type": "cli_error"}},
        )

    try:
        result = json.loads(stdout.decode())
        content: str = result.get("result", stdout.decode())
    except json.JSONDecodeError:
        content = stdout.decode().strip()

    completion_chars = len(content) if isinstance(content, str) else 0
    log.info("req=%s acct=%s model=%s duration_ms=%d prompt_chars=%d completion_chars=%d status=ok",
             request_id, account.id, model, duration, len(prompt), completion_chars)

    prompt_tokens = len(prompt.split())
    completion_tokens = len(content.split()) if isinstance(content, str) else 0
    return JSONResponse({
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": f"claude-bridge-{model}",
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

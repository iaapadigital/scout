"""Minimal async LLM client for the distillation step.

Talks straight HTTP to OpenAI or Anthropic (whichever is configured) so we
aren't coupled to SDK versions. browser-use manages its own LLM connection
separately (see agents/browser_agent.py)."""
import asyncio
import json

import httpx

from . import config

TIMEOUT = httpx.Timeout(120.0, connect=15.0)


async def complete(prompt: str, system: str = "", max_tokens: int = 1500) -> str:
    if config.LLM_PROVIDER == "anthropic":
        return await _anthropic(prompt, system, max_tokens)
    if config.LLM_PROVIDER == "ollama":
        return await _ollama(prompt, system, max_tokens)
    if config.LLM_PROVIDER == "claude-code":
        return await _claude_code(prompt, system)
    return await _openai(prompt, system, max_tokens)


# Claude Code headless mode authenticates with your Claude subscription (Max),
# so no API key is needed. Capped to a few concurrent CLI calls to be gentle
# on your plan's rate limits.
_cc_sem = asyncio.Semaphore(3)


async def _claude_code(prompt: str, system: str) -> str:
    full = (f"{system}\n\n{prompt}" if system else prompt)
    cmd = [config.CLAUDE_CODE_BIN, "-p", full, "--output-format", "text"]
    if config.CLAUDE_CODE_MODEL:
        cmd += ["--model", config.CLAUDE_CODE_MODEL]
    async with _cc_sem:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=180)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("claude CLI timed out after 180s")
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {err.decode(errors='replace')[:300]}")
    return out.decode(errors="replace").strip()


async def _ollama(prompt: str, system: str, max_tokens: int) -> str:
    """Ollama exposes an OpenAI-compatible endpoint at /v1 — free local LLM."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(
            f"{config.OLLAMA_BASE_URL}/v1/chat/completions",
            json={"model": config.OLLAMA_MODEL, "messages": messages,
                  "max_tokens": max_tokens, "temperature": 0.3},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def _openai(prompt: str, system: str, max_tokens: int) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
            json={
                "model": config.OPENAI_MODEL,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.3,
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def _anthropic(prompt: str, system: str, max_tokens: int) -> str:
    body = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            json=body,
        )
        r.raise_for_status()
        blocks = r.json().get("content", [])
        return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def extract_json(text: str):
    """Best-effort extraction of a JSON object/array from an LLM reply."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # fall back: find first bracket span
    for open_c, close_c in (("[", "]"), ("{", "}")):
        start, end = text.find(open_c), text.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None

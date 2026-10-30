"""Model providers behind one interface (phase 06).

A provider turns (system prompt, instruction, JSON schema) into raw text and
reports what produced it. It never interprets the text: validation happens in
``intent.parse_intent``. Only local Ollama is supported; any host other than
loopback is refused, and system proxies are bypassed so the prompt cannot leave
the machine (docs/10-ai.md, ADR-006).
"""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:14b"
DEFAULT_TIMEOUT_S = 180.0
MAX_HTTP_BYTES = 1024 * 1024
# Ollama's default context (2048 to 4096 tokens, by version) silently drops the start of a
# longer prompt, and the overgrow library alone is about 11k tokens. The window is sized
# from the prompt at 3 bytes per token, plus room for the reply, in steps of 4096.
BYTES_PER_TOKEN = 3
REPLY_TOKENS = 4096
CONTEXT_STEP = 4096
MIN_NUM_CTX = 8192
MAX_NUM_CTX = 32768


def context_window(*texts: str) -> int:
    """Tokens of context for a prompt made of ``texts``; a prompt that cannot fit is an error."""
    tokens = sum(len(text.encode()) for text in texts) // BYTES_PER_TOKEN + REPLY_TOKENS
    if tokens > MAX_NUM_CTX:
        raise ProviderError(f"prompt needs about {tokens} tokens of context; the limit is {MAX_NUM_CTX}")
    return max(MIN_NUM_CTX, -(-tokens // CONTEXT_STEP) * CONTEXT_STEP)


class ProviderError(RuntimeError):
    """The provider could not produce a response; the message says why and what to do."""


@dataclass(frozen=True, slots=True)
class ProviderResult:
    text: str
    provenance: dict[str, Any] = field(default_factory=dict)


class Provider(Protocol):
    name: str

    def complete(self, system: str, instruction: str, schema: dict[str, Any]) -> ProviderResult: ...


@dataclass(frozen=True, slots=True)
class StaticProvider:
    """No model: returns a document given by the user (``none``) or recorded earlier (``replay``)."""

    name: str
    text: str
    source: str

    def complete(self, system: str, instruction: str, schema: dict[str, Any]) -> ProviderResult:
        return ProviderResult(self.text, {"provider": self.name, "source": self.source, "model": None})


def check_loopback(url: str) -> str:
    """Return the normalised base URL, or raise if it is not plain http on a loopback address."""
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname or parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ProviderError(f"Ollama host must look like http://127.0.0.1:11434, got {url!r}")
    if parts.username or parts.password:
        raise ProviderError("Ollama host must not contain credentials")
    # "localhost" is pinned to 127.0.0.1: name resolution could point it elsewhere.
    host = "127.0.0.1" if parts.hostname == "localhost" else parts.hostname
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError
    except ValueError:
        raise ProviderError(f"refusing non-local Ollama host {host!r}: only loopback addresses are allowed "
                            "(external AI providers are disabled)") from None
    port = parts.port or 11434
    shown = f"[{host}]" if ":" in host else host
    return f"http://{shown}:{port}"


@dataclass(slots=True)
class OllamaProvider:
    model: str = DEFAULT_OLLAMA_MODEL
    host: str = DEFAULT_OLLAMA_HOST
    seed: int = 0
    timeout_s: float = DEFAULT_TIMEOUT_S
    name: str = "ollama"

    def __post_init__(self) -> None:
        self.host = check_loopback(self.host)
        if not self.model or len(self.model) > 200:
            raise ProviderError("model name must be 1–200 characters")

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.host + path, data=data, method="GET" if data is None else "POST",
                                     headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=self.timeout_s) as resp:
                body = resp.read(MAX_HTTP_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = exc.read(2000).decode("utf-8", "replace")
            raise ProviderError(f"Ollama returned HTTP {exc.code} for {path}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ProviderError(f"cannot reach Ollama at {self.host} ({reason}); is it running? "
                                "Without a model, use --provider none --intent FILE") from exc
        if len(body) > MAX_HTTP_BYTES:
            raise ProviderError(f"Ollama response to {path} exceeds {MAX_HTTP_BYTES} bytes")
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Ollama returned non-JSON for {path}") from exc

    def describe(self) -> dict[str, Any]:
        version = self._request("/api/version").get("version")
        models = self._request("/api/tags").get("models", [])
        match = next((m for m in models if m.get("name") == self.model or m.get("model") == self.model), None)
        if match is None:
            names = ", ".join(sorted(m.get("name", "?") for m in models)) or "none"
            raise ProviderError(f"model {self.model!r} is not installed in Ollama (installed: {names}). "
                                "Choose one with --model; nothing is downloaded automatically")
        details = match.get("details", {})
        return {"server_version": version, "model_digest": match.get("digest"),
                "parameter_size": details.get("parameter_size"), "quantization": details.get("quantization_level"),
                "family": details.get("family")}

    def options(self, num_ctx: int) -> dict[str, Any]:
        return {"temperature": 0, "seed": self.seed, "num_ctx": num_ctx}

    def complete(self, system: str, instruction: str, schema: dict[str, Any]) -> ProviderResult:
        num_ctx = context_window(system, instruction)
        info = self.describe()
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": instruction}],
            "format": schema,
            "stream": False,
            "think": False,
            "options": self.options(num_ctx),
        }
        reply = self._request("/api/chat", payload)
        message = reply.get("message") if isinstance(reply, dict) else None
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str):
            raise ProviderError("Ollama reply has no message content")
        provenance = {"provider": self.name, "host": self.host, "model": self.model, **info,
                      "options": {**self.options(num_ctx), "think": False, "stream": False, "format": "intent JSON schema"},
                      "done_reason": reply.get("done_reason"), "eval_count": reply.get("eval_count"),
                      "prompt_eval_count": reply.get("prompt_eval_count"),
                      "total_duration_ns": reply.get("total_duration")}
        return ProviderResult(text, provenance)

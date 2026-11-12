"""Model providers behind one interface (phase 06).

A provider turns (system prompt, instruction, JSON schema) into raw text and
reports what produced it. It never interprets the text: validation happens in
``intent.parse_intent``. Only local Ollama is supported; any host other than
loopback is refused, and system proxies are bypassed so the prompt cannot leave
the machine (docs/10-ai.md, ADR-006). Inside a container, and only there, the one
name ``host.docker.internal`` stands for the machine's own loopback (ADR-006
amendment of 12 November 2026): it must resolve to a private address, and the
host is pinned to that address before connecting.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
# tools/docker/run.sh sets it on macOS, where the container's 127.0.0.1 is not the Mac. It is checked like --host.
OLLAMA_HOST_ENV = "ONWARD_OLLAMA_HOST"
# Docker Desktop's name for the machine running the container, accepted only where DOCKERENV exists.
CONTAINER_HOST = "host.docker.internal"
DOCKERENV = Path("/.dockerenv")
DEFAULT_OLLAMA_MODEL = "qwen3:14b"
DEFAULT_TIMEOUT_S = 180.0
MAX_HTTP_BYTES = 1024 * 1024
# Ollama's default context (2048 to 4096 tokens, by version) silently drops the start of a
# longer prompt; the overgrow prompt carries up to 14k tokens of library rows (overgrow/prompt.py). The window is sized
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


def default_ollama_host() -> str:
    """``$ONWARD_OLLAMA_HOST`` when it is set and not empty, else ``http://127.0.0.1:11434``."""
    return os.environ.get(OLLAMA_HOST_ENV) or DEFAULT_OLLAMA_HOST


def _container_host(port: int) -> str:
    """The private address ``host.docker.internal`` resolves to, inside a container; refused anywhere else."""
    if not DOCKERENV.exists():
        raise ProviderError(f"refusing Ollama host {CONTAINER_HOST!r} outside a container: "
                            "on this machine use http://127.0.0.1:11434")
    try:
        infos = socket.getaddrinfo(CONTAINER_HOST, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ProviderError(f"cannot resolve {CONTAINER_HOST!r} ({exc}): it needs Docker Desktop") from exc
    addresses = sorted({str(info[4][0]) for info in infos})
    if not addresses or not all(ipaddress.ip_address(a).is_private for a in addresses):
        raise ProviderError(f"refusing Ollama host {CONTAINER_HOST!r}: it resolves to {', '.join(addresses) or 'nothing'}, "
                            "not only to private addresses")
    return addresses[0]


def check_loopback(url: str) -> str:
    """Return the normalised base URL, or raise if it is not plain http on a loopback address.

    In a container, ``host.docker.internal`` is the machine's loopback and is pinned to the private address it
    resolves to."""
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname or parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ProviderError(f"Ollama host must look like http://127.0.0.1:11434, got {url!r}")
    if parts.username or parts.password:
        raise ProviderError("Ollama host must not contain credentials")
    # "localhost" is pinned to 127.0.0.1: name resolution could point it elsewhere.
    host = "127.0.0.1" if parts.hostname == "localhost" else parts.hostname
    port = parts.port or 11434
    if host == CONTAINER_HOST:
        return f"http://{_container_host(port)}:{port}"
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError
    except ValueError:
        raise ProviderError(f"refusing non-local Ollama host {host!r}: only loopback addresses are allowed "
                            "(external AI providers are disabled)") from None
    shown = f"[{host}]" if ":" in host else host
    return f"http://{shown}:{port}"


@dataclass(slots=True)
class OllamaProvider:
    model: str = DEFAULT_OLLAMA_MODEL
    host: str = field(default_factory=default_ollama_host)
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

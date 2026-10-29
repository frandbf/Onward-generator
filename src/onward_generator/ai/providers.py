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
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:14b"
DEFAULT_TIMEOUT_S = 180.0
MAX_HTTP_BYTES = 1024 * 1024


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
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
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

    def options(self) -> dict[str, Any]:
        return {"temperature": 0, "seed": self.seed}

    def complete(self, system: str, instruction: str, schema: dict[str, Any]) -> ProviderResult:
        info = self.describe()
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": instruction}],
            "format": schema,
            "stream": False,
            "think": False,
            "options": self.options(),
        }
        reply = self._request("/api/chat", payload)
        message = reply.get("message") if isinstance(reply, dict) else None
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str):
            raise ProviderError("Ollama reply has no message content")
        provenance = {"provider": self.name, "host": self.host, "model": self.model, **info,
                      "options": {**self.options(), "think": False, "stream": False, "format": "intent JSON schema"},
                      "done_reason": reply.get("done_reason"), "eval_count": reply.get("eval_count"),
                      "total_duration_ns": reply.get("total_duration")}
        return ProviderResult(text, provenance)
